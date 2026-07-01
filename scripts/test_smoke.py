#!/usr/bin/env python3
"""Smoke tests for techbro pipeline scripts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

results = {}

def test(name, fn):
    try:
        fn()
        results[name] = "PASS"
        print(f"  ✅ {name}")
    except Exception as e:
        results[name] = f"FAIL: {e}"
        print(f"  ❌ {name}: {e}")

# === 1. db.py ===
print("\n1. DB.PY")
print("-" * 40)

def test_db_tables():
    from db import get_db
    conn = get_db()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "articles" in tables, f"Missing articles table, got {tables}"
    assert "posts" in tables, f"Missing posts table, got {tables}"
test("db_tables", test_db_tables)

def test_db_upsert():
    from db import get_db, upsert_article
    conn = get_db()
    aid = upsert_article(conn, {"title":"Smoke Test","url":"https://smoke.test","source":"test","image":"","body":"body","score":42})
    assert aid > 0, f"Bad id: {aid}"
    art = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    assert art["title"] == "Smoke Test"
    # Dedup test
    aid2 = upsert_article(conn, {"title":"Smoke Test","url":"https://smoke.test","source":"test","image":"","body":"x","score":99})
    assert aid2 == aid, f"Dedup failed: {aid} vs {aid2}"
    conn.execute("DELETE FROM articles WHERE id=?", (aid,))
    conn.commit(); conn.close()
test("db_upsert_dedup", test_db_upsert)

def test_db_stage():
    from db import get_db, upsert_article, stage_post, mark_posted
    conn = get_db()
    aid = upsert_article(conn, {"title":"Stage Test","url":"https://stage.test","source":"test","image":"","body":"b","score":10})
    slides = {"hook":"h","setup":"s","twist":"t","deep":"d","sowhat":"sw","cta":"c"}
    pid = stage_post(conn, aid, slides, "cap", "#tag")
    assert pid > 0
    post = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    assert post["status"] == "staged"
    mark_posted(conn, pid, "12345")
    post2 = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    assert post2["status"] == "posted"
    assert post2["thread_post_id"] == "12345"
    conn.execute("DELETE FROM articles WHERE id=?", (aid,))
    conn.execute("DELETE FROM posts WHERE id=?", (pid,))
    conn.commit(); conn.close()
test("db_stage_mark", test_db_stage)


# === 2. scraper.py ===
print("\n2. SCRAPER.PY")
print("-" * 40)

def test_scraper_product_filter():
    from scraper import score_article
    score = score_article("Vivo TWS 5 Pro resmi diluncurkan",
        "Vivo meluncurkan earbuds TWS 5 Pro dengan ANC 60dB, audio lossless, baterai 50 jam, harga Rp 600 ribu.")
    assert score == 0, f"Product filter failed: {score}"
test("scraper_product_zero", test_scraper_product_filter)

def test_scraper_excluded():
    from scraper import score_article
    score = score_article("Jadwal Liga Champions", "Pertandingan bola malam ini.")
    assert score == 0, f"Exclusion failed: {score}"
test("scraper_excluded", test_scraper_excluded)

def test_scraper_t1():
    from scraper import score_article
    score = score_article("AI agent hack perusahaan",
        "Hacker menggunakan AI agent untuk breach keamanan siber perusahaan besar dengan exploit zero-day.")
    assert score > 30, f"T1 score too low: {score}"
    print(f"    (score={score})")
test("scraper_t1_scoring", test_scraper_t1)

def test_scraper_rss():
    from scraper import scrape_all
    articles = scrape_all(top_n=3)
    assert len(articles) > 0, "RSS returned 0 articles"
    print(f"    ({len(articles)} articles from RSS)")
test("scraper_rss_fetch", test_scraper_rss)


# === 3. generator.py postprocessor ===
print("\n3. GENERATOR.PY — postprocessor")
print("-" * 40)

def test_whitespace():
    from generator import _add_whitespace
    result = _add_whitespace("First sentence. Second sentence. Third sentence.")
    assert "\n\n" in result, f"No blank lines: {repr(result)}"
    parts = result.split("\n\n")
    assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}"
test("postproc_whitespace", test_whitespace)

def test_em_dash():
    from generator import _postprocess_slides
    s = {"hook":"Ini test — dengan em dash. Oke ya.","setup":"S.","twist":"T.","deep":"D.","sowhat":"SW.","cta":"C."}
    p = _postprocess_slides(s, "")
    assert "—" not in p["hook"], f"Em dash remains: {p['hook']}"
test("postproc_em_dash", test_em_dash)

def test_banned_phrases():
    from generator import _postprocess_slides
    s = {"hook":"Bayangin geleng-geleng lo. Ini serius. Tahan dulu bro.","setup":"S.","twist":"T.","deep":"D.","sowhat":"SW.","cta":"C."}
    p = _postprocess_slides(s, "")
    assert "geleng" not in p["hook"].lower(), f"geleng remains: {p['hook']}"
    assert "tahan dulu" not in p["twist"].lower(), f"tahan dulu remains: {p['twist']}"
test("postproc_banned", test_banned_phrases)

def test_url_strip():
    from generator import _postprocess_slides
    s = {"hook":"Check https://fake.com ya. Penting banget.","setup":"S.","twist":"T.","deep":"D.","sowhat":"SW.","cta":"C."}
    p = _postprocess_slides(s, "")
    assert "https://" not in p["hook"], f"URL remains in hook: {p['hook']}"
test("postproc_url_strip", test_url_strip)

def test_source_url_in_cta():
    from generator import _postprocess_slides
    s = {"hook":"H.","setup":"S.","twist":"T.","deep":"D.","sowhat":"SW.","cta":"Pilih mana?"}
    p = _postprocess_slides(s, "https://source.com/artikel")
    assert "source.com" in p["cta"], f"Source URL missing from CTA: {p['cta']}"
test("postproc_source_url", test_source_url_in_cta)

def test_fake_quote_strip():
    from generator import _postprocess_slides
    # Single quotes stripped (chars removed, content kept) — Threads gak render
    s = {"hook":"H.","setup":"S.","twist":"T.","deep":"Ini kata mereka: 'dialog panjang'.","sowhat":"SW.","cta":"C."}
    p = _postprocess_slides(s, "")
    assert "'" not in p["deep"], f"Single quote remains: {p['deep']}"
    # Double quotes long (20+ chars) stripped entirely
    s2 = {"hook":"H.","setup":"S.","twist":"T.","deep":"Kata mereka: \"ini dialog imajiner yang sangat panjang dan harus dihapus\".","sowhat":"SW.","cta":"C."}
    p2 = _postprocess_slides(s2, "")
    assert "imajiner" not in p2["deep"], f"Fake quote remains: {p2['deep']}"
test("postproc_fake_quotes", test_fake_quote_strip)


# === 4. generator.py API ===
print("\n4. GENERATOR.PY — Mistral API")
print("-" * 40)

def test_mistral_api():
    from generator import _call_mistral
    raw = _call_mistral("Test AI News", "OpenAI mengumumkan model AI baru untuk startup Indonesia. Model ini 3x lebih cepat.")
    assert raw is not None, "Mistral returned None"
    assert len(raw) > 100, f"Response too short: {len(raw)}"
    print(f"    (response: {len(raw)} chars)")
test("generator_mistral_call", test_mistral_api)

def test_parse_slides():
    from generator import _parse_slides
    raw = '{"slide_1":"hook text here for testing","slide_2":"setup text","slide_3":"twist text","slide_4":"deep text","slide_5":"sowhat text","slide_6":"cta text","caption":"cap","hashtags":"#tag"}'
    slides = _parse_slides(raw)
    assert slides is not None, "Parse returned None"
    assert "hook" in slides, f"Missing hook key: {slides.keys()}"
    assert slides["hook"] == "hook text here for testing"
test("generator_parse", test_parse_slides)

def test_full_generate():
    from generator import generate_carousel
    slides = generate_carousel(
        "Kebocoran Data Pengguna e-Commerce Indonesia",
        "Sebuah keamanan siber menemukan bahwa 15 juta data pengguna platform e-commerce Indonesia bocor dan dijual di dark web. Data tersebut meliputi nama, alamat, nomor telepon, dan riwayat transaksi. Kementerian Kominfo sedang menyelidiki insiden ini.",
        "", "https://example.com/test")
    assert slides is not None, "generate_carousel returned None"
    assert slides.pop("_provider", "") in ("mistral", "groq"), "Unknown provider"
    for k in ["hook","setup","twist","deep","sowhat","cta"]:
        assert k in slides, f"Missing key: {k}"
        assert len(slides[k]) > 10, f"{k} too short: {len(slides[k])}"
    assert "example.com" in slides["cta"], f"Source URL missing from CTA"
    print(f"    (hook: {len(slides['hook'].split())} words)")
test("generator_full", test_full_generate)


# === 5. poster.py ===
print("\n5. POSTER.PY")
print("-" * 40)

def test_poster_imports():
    from poster import post_carousel, _post_container, post_from_db
    assert callable(post_carousel)
    assert callable(_post_container)
    assert callable(post_from_db)
test("poster_imports", test_poster_imports)

def test_image_inline():
    """Image validation logic — just verify the code pattern exists in poster.py."""
    import inspect, poster
    src = inspect.getsource(poster._post_container)
    assert "content-type" in src or "image/" in src, "No image validation in _post_container"
    assert "image_url" in src, "No image_url param in _post_container"
test("poster_image_validation", test_image_inline)


# === 6. pipeline.py imports ===
print("\n6. PIPELINE.PY")
print("-" * 40)

def test_pipeline_import():
    from pipeline import run
    assert callable(run)
test("pipeline_import", test_pipeline_import)


# === 7. threads_auth.py ===
print("\n7. THREADS_AUTH.PY")
print("-" * 40)

def test_auth_import():
    from threads_auth import get_auth_url, exchange_code, refresh_long_lived
    assert callable(get_auth_url)
    assert callable(exchange_code)
    assert callable(refresh_long_lived)
test("auth_import", test_auth_import)


# === SUMMARY ===
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
passed = sum(1 for v in results.values() if v == "PASS")
total = len(results)
for name, status in results.items():
    icon = "✅" if status == "PASS" else "❌"
    detail = "" if status == "PASS" else f" — {status}"
    print(f"  {icon} {name}{detail}")
print(f"\n  {passed}/{total} passed")
