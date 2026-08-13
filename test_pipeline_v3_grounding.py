#!/usr/bin/env python3
"""Regression tests for Techbro v3 factual grounding and engagement scoring."""
import json
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "pipeline_v3", Path(__file__).with_name("pipeline-v3.py")
)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def test_ungrounded_rupiah_range_is_rejected():
    article = {"title": "Rupiah melemah", "body": "Rupiah berada di Rp17.976 per dolar AS."}
    posts = {f"post_{i}": "" for i in range(1, 7)}
    posts["post_2"] = "Setiap Rp1.000 melemah, harga impor naik Rp500 sampai Rp1.000."
    issues = pipeline.deterministic_grounding_validate(article, posts)
    assert any("Rp500" in issue or "Rp1.000" in issue for issue in issues), issues


def test_thin_article_is_rejected_before_generation():
    assert pipeline.article_evidence_gate({"body": "Fakta ekonomi."}) == "body_under_1000_chars"
    assert pipeline.article_evidence_gate({"body": "teks " * 250}) == "insufficient_source_claims_for_six_posts"
    body = " ".join(f"Nilai bulan {month} mencapai Rp{month}." for month in range(1, 7)) * 12
    assert pipeline.article_evidence_gate({"body": body}) is None


def test_six_post_draft_requires_six_source_claims_before_llm():
    body = ("Bank Indonesia menetapkan suku bunga menjadi 5 persen. "
            + "Narasi tanpa fakta tambahan. " * 50)
    assert pipeline.article_evidence_gate({"body": body}) == "insufficient_source_claims_for_six_posts"


def test_source_claim_plan_uses_article_sentences_only():
    article = {"body": "Rupiah berada di Rp17.976 per dolar AS. Ekonom memproyeksikan pelemahan berlanjut. Kalimat pendek."}
    plan = pipeline.source_claim_plan(article)
    assert "Rp17.976" in plan
    assert "pelemahan berlanjut" in plan
    assert "Kalimat pendek." not in plan


def test_article_body_strips_detik_scroll_marker(monkeypatch):
    class Response:
        status_code = 200
        content = b""

    html = """<html><meta property='og:image' content='https://example.test/image.jpg'><article>
    <p>Kalimat fakta pertama yang cukup panjang untuk diekstrak dari artikel dan menjadi bukti sumber yang valid.</p>
    <p>SCROLL TO CONTINUE WITH CONTENT</p>
    <p>Kalimat fakta kedua yang cukup panjang untuk diekstrak dari artikel dan tetap dipakai sebagai bukti sumber.</p>
    </article></html>"""
    monkeypatch.setattr(pipeline, "_http_get", lambda url, timeout=15: (200, html))
    monkeypatch.setattr(pipeline, "validate_article_image", lambda url: None)
    pipeline._BODY_CACHE.clear()
    body, _, _ = pipeline._fetch_article_body("https://example.test/article")
    assert "SCROLL TO CONTINUE WITH CONTENT" not in body
    assert "Kalimat fakta pertama" in body
    assert "Kalimat fakta kedua" in body


def test_article_body_removes_embedded_detik_marker_before_source_join(monkeypatch):
    html = """<html><article>
    <p>Fakta artikel yang cukup panjang untuk lolos ekstraksi sebagai sumber utama dan menjadi bagian isi artikel yang benar.</p>
    <p>SCROLL TO CONTINUE WITH CONTENT Fakta lanjutan artikel tetap harus dipertahankan sebagai fakta sumber yang sah dan tidak boleh hilang.</p>
    </article></html>"""
    monkeypatch.setattr(pipeline, "_http_get", lambda url, timeout=15: (200, html))
    pipeline._BODY_CACHE.clear()
    body, _, _ = pipeline._fetch_article_body("https://example.test/article-embedded")
    assert "SCROLL TO CONTINUE WITH CONTENT" not in body
    assert "Fakta lanjutan artikel tetap harus dipertahankan" in body


def test_source_claim_map_ranks_and_assigns_source_sentences_to_slides():
    body = (
        "Pemerintah menetapkan kebijakan subsidi energi mulai Januari 2027. "
        "Menteri Keuangan mengatakan aturan itu menyasar rumah tangga berpendapatan rendah. "
        "Penyaluran dilakukan melalui basis data penerima yang sudah disiapkan. "
        "Tujuannya menjaga anggaran dan bantuan mulai berlaku pada Januari 2027. "
        "Perubahan ini memengaruhi konsumen dan pelaku usaha. "
        "Proses penetapan masih menunggu pembahasan DPR."
    )
    claim_map = pipeline.source_claim_map({"body": body})

    assert set(claim_map) == {f"post_{i}" for i in range(1, 7)}
    assert claim_map["post_1"][0]["sentence"].startswith("Pemerintah menetapkan")
    assert any("Menteri Keuangan" in c["sentence"] for c in claim_map["post_2"])
    assert all(c["score"] > 0 for claims in claim_map.values() for c in claims)
    assert len({c["sentence"] for claims in claim_map.values() for c in claims}) == 6


def test_source_claim_map_uses_selected_story_spine():
    body = " ".join([
        "Rupiah ditutup melemah 0,20% ke posisi Rp17.865/US$.",
        "The Fed lebih mencemaskan inflasi daripada pelemahan pasar tenaga kerja.",
        "Indeks dolar AS menguat 0,03% pada pukul 15.00 WIB.",
        "Pasar memperhitungkan peluang The Fed mempertahankan suku bunga sebesar 52%.",
        "Harga minyak bergerak naik setelah serangan terhadap jalur pelayaran.",
        "Pemerintah mengusulkan calon gubernur BI kepada DPR.",
    ])
    claim_map = pipeline.source_claim_map({
        "title": "Rupiah Ditutup Loyo, Dolar AS Naik",
        "body": body,
        "pattern": "PASAR",
    })
    mapped = " ".join(item["sentence"] for claims in claim_map.values() for item in claims)
    assert "calon gubernur BI" not in mapped


def test_s6_question_needs_only_one_source_anchor():
    body = (
        "Pemerintah menetapkan kebijakan subsidi energi mulai Januari 2027. "
        "Menteri Keuangan mengatakan aturan itu menyasar rumah tangga berpendapatan rendah. "
        "Penyaluran dilakukan melalui basis data penerima yang sudah disiapkan. "
        "Tujuannya menjaga anggaran dan bantuan mulai berlaku pada Januari 2027. "
        "Perubahan ini memengaruhi konsumen dan pelaku usaha. "
        "Proses penetapan masih menunggu pembahasan DPR."
    )
    posts = {
        "post_1": "Pemerintah menetapkan kebijakan subsidi energi mulai Januari 2027.",
        "post_2": "Menteri Keuangan mengatakan aturan itu menyasar rumah tangga berpendapatan rendah.",
        "post_3": "Penyaluran dilakukan melalui basis data penerima yang sudah disiapkan.",
        "post_4": "Tujuannya menjaga anggaran dan bantuan mulai berlaku pada Januari 2027.",
        "post_5": "Perubahan ini memengaruhi konsumen dan pelaku usaha.",
        "post_6": "Proses penetapan masih menunggu pembahasan DPR. Menurut lo, proses ini perlu diawasi?",
    }
    assert not pipeline._validate_source_evidence_map(posts, body)


def test_revision_prompt_contains_current_draft():
    draft = {f"post_{i}": f"draft {i}" for i in range(1, 7)}
    prompt = pipeline.build_revision_prompt("post_2: bad quote", draft)
    assert '"post_2": "draft 2"' in prompt
    assert "JANGAN membuat ulang slide yang tidak disebut issue" in prompt


def test_revision_prompt_carries_sanitized_issue_and_source_allowlist():
    body = "MSCI mengeluarkan 10 saham Indonesia dari indeks."
    posts = {f"post_{i}": f"draft {i}" for i in range(1, 7)}
    prompt = pipeline.build_revision_prompt(
        "post_4: name 'Boy Thohir' not in article; post_2: quote not verbatim",
        posts,
        {"body": body},
    )
    assert "Boy Thohir" not in prompt
    assert "MSCI mengeluarkan 10 saham Indonesia dari indeks." in prompt
    assert "NAMA/ENTITAS LITERAL" in prompt


def test_parse_llm_json_recovers_fenced_revision_object():
    content = "Berikut hasil revisi:\n```json\n{\"status\":\"success\",\"post_1\":\"ok\"}\n```"
    assert pipeline._parse_llm_json(content) == {"status": "success", "post_1": "ok"}


def test_source_fallback_starts_with_selected_story_spine():
    body = " ".join([
        "Rupiah ditutup melemah 0,20% ke posisi Rp17.865/US$.",
        "Indeks dolar AS menguat 0,03% pada pukul 15.00 WIB.",
        "Pasar memperhitungkan peluang The Fed mempertahankan suku bunga sebesar 52%.",
        "Harga minyak bergerak naik setelah serangan terhadap jalur pelayaran.",
        "Tekanan terhadap rupiah membesar dibandingkan posisi pembukaan.",
        "Pemerintah mengumumkan data inflasi konsumen AS pada Rabu waktu setempat.",
        "Pelaku pasar masih mencermati arah suku bunga bank sentral Amerika Serikat.",
        "Risiko inflasi membuat pasar belum menutup peluang kenaikan suku bunga.",
        "Nilai tukar rupiah kembali mengakhiri perdagangan di zona merah.",
        "Perdagangan berlangsung pada Rabu 12 Agustus 2026.",
        "Data pasar menjadi perhatian pelaku ekonomi sepanjang hari.",
        "Perubahan tersebut memengaruhi pergerakan mata uang regional.",
    ])
    article = {"body": body, "pattern": "PASAR"}
    posts = pipeline._source_fallback_posts(article)
    assert posts is not None
    assert "Rupiah ditutup melemah" in posts["post_1"]


def test_historical_profile_without_current_economy_action_is_rejected():
    body = " ".join([
        "Dalam autobiografinya, tokoh itu mengaku penghasilannya sebagai pensiunan tidak cukup.",
        "Ia lahir 124 tahun lalu dan mundur dari pemerintahan pada 1957.",
        "Putrinya pernah membantu membayar tagihan listrik dan air.",
        "Kisah tersebut dimuat dalam biografi dan surat-surat lama.",
        "Keluarganya hidup pas-pasan setelah tokoh itu pensiun.",
    ] * 4)
    ok, reason = pipeline._is_eligible_candidate(
        "Cerita Pejabat Lama Tak Bisa Bayar Pajak dan Tagihan Rumah", body, "cnbc_entrepreneur"
    )
    assert not ok
    assert reason == "historical_profile_without_current_economy_action"


def test_source_fallback_builds_six_grounded_posts():
    sentences = [
        "Pemerintah menetapkan kebijakan subsidi energi mulai Januari 2027.",
        "Menteri Keuangan mengatakan aturan itu menyasar rumah tangga berpendapatan rendah.",
        "Penyaluran dilakukan melalui basis data penerima yang sudah disiapkan.",
        "Tujuannya menjaga anggaran dan bantuan mulai berlaku pada Januari 2027.",
        "Perubahan ini memengaruhi konsumen dan pelaku usaha.",
        "Proses penetapan masih menunggu pembahasan DPR.",
        "Pembahasan lanjutan dilakukan setelah persetujuan DPR diterima.",
        "Pemerintah menyiapkan jadwal pembahasan lanjutan bersama DPR.",
        "Menteri Keuangan akan menyampaikan perkembangan aturan kepada DPR.",
        "Penyaluran bantuan tetap mengikuti basis data penerima yang sudah disiapkan.",
        "Kebijakan tersebut berlaku setelah pembahasan lanjutan selesai dilakukan.",
        "Pemerintah mencatat proses penetapan masih berjalan sesuai aturan.",
    ]
    article = {"body": " ".join(sentences), "pattern": "PROYEK"}
    posts = pipeline._source_fallback_posts(article)
    assert posts is not None
    assert set(posts) == {f"post_{i}" for i in range(1, 7)}
    assert all(len(text) <= pipeline.SLIDE_CHAR_LIMIT for text in posts.values())
    assert posts["post_1"].count(".") >= 2
    assert not pipeline.deterministic_grounding_validate(article, posts)


def test_source_fallback_uses_winning_story_arc_not_generic_cta():
    body = " ".join([
        "Pemerintah menetapkan subsidi energi mulai Januari 2027.",
        "Menteri Keuangan mengatakan kebijakan itu menyasar rumah tangga berpendapatan rendah.",
        "Penyaluran dilakukan melalui basis data penerima yang sudah disiapkan.",
        "Bantuan mulai berlaku pada Januari 2027 setelah pembahasan DPR.",
        "Perubahan ini memengaruhi konsumen dan pelaku usaha.",
        "Proses penetapan masih menunggu persetujuan DPR.",
        "Pemerintah menyiapkan jadwal pembahasan lanjutan bersama DPR.",
        "Kebijakan tersebut berlaku setelah pembahasan lanjutan selesai dilakukan.",
        "Menteri Keuangan akan menyampaikan perkembangan aturan kepada DPR.",
        "Penyaluran bantuan tetap mengikuti basis data penerima yang sudah disiapkan.",
        "Pemerintah mencatat proses penetapan masih berjalan sesuai aturan.",
        "Pembahasan lanjutan dilakukan setelah persetujuan DPR diterima.",
    ])
    posts = pipeline._source_fallback_posts({"body": body, "pattern": "KEBIJAKAN"})
    assert posts is not None
    assert "menetapkan" in posts["post_1"].lower()
    assert posts["post_1"].count(".") >= 2
    assert "persetujuan" in posts["post_6"].lower() or "biaya" in posts["post_6"].lower()
    assert "fakta ini perlu dipantau" not in posts["post_6"].lower()
    assert not pipeline.deterministic_grounding_validate({"body": body}, posts)


def test_winning_gate_blocks_weak_hook_and_generic_cta():
    posts = {f"post_{i}": "Fakta sumber yang cukup panjang untuk validasi. Fakta kedua juga ada." for i in range(1, 7)}
    posts["post_1"] = "Hal ini tercermin dari perubahan kebijakan. Statusnya masih dibahas."
    posts["post_6"] = "Dampaknya masih dibahas. Menurut lo, fakta ini perlu dipantau?"
    warnings = pipeline.deterministic_validate(posts)
    assert any("weak winning hook" in warning for warning in warnings)
    assert any("generic winning CTA" in warning for warning in warnings)


def test_writer_prompt_contains_source_claims():
    body = "Pemerintah menetapkan kebijakan subsidi senilai Rp1 triliun. " * 10
    prompt = pipeline.build_user_prompt({"body": body})
    assert "CLAIM MAP S1-S6" in prompt
    assert "Jangan menambah klaim di luar CLAIM MAP" in prompt


def test_policy_prompt_encodes_verified_high_perform_arc():
    prompt = pipeline.SYSTEM_PROMPT
    assert "opsi resmi + kelompok terdampak + status belum final" in prompt
    assert "pembagian kewenangan serta dasar aturan" in prompt
    assert "hitung-hitungan pelaksanaan dan biaya" in prompt
    assert "beban/keuntungan antar pihak" in prompt


def test_policy_winner_evidence_requires_six_roles():
    body = " ".join([
        "Pemerintah mengusulkan guru PPPK dipindahkan menjadi ASN pusat.",
        "Menteri Pendidikan menyebut opsi itu dibahas bersama pemerintah daerah.",
        "Pemindahan kewenangan mengikuti aturan yang berlaku.",
        "Pemerintah menghitung anggaran dan jumlah guru yang terdampak.",
        "Pembahasan dilakukan untuk pemerataan distribusi guru.",
        "Langkah berikutnya menunggu rapat lanjutan pekan depan.",
        "Perubahan ini mengurangi beban daerah tetapi menambah anggaran pusat.",
        "Status guru yang sudah diangkat masih belum ditentukan.",
        "Pemerintah menyiapkan bahan pembahasan bersama DPR.",
        "Daerah masih menunggu pembagian kewenangan yang baru.",
        "Anggaran pemindahan dibahas dalam rapat pemerintah.",
        "Keputusan akhir belum diumumkan.",
    ])
    article = {"pattern": "KEBIJAKAN", "body": body}
    evidence = pipeline.policy_winner_evidence(article)
    assert all(evidence[f"post_{i}"] for i in range(1, 7))
    posts = pipeline._source_fallback_posts(article)
    assert posts is not None
    assert not pipeline._validate_policy_winner_arc(article, posts)


def test_policy_winner_gate_rejects_missing_tradeoff():
    body = " ".join([
        "Pemerintah mengusulkan guru PPPK dipindahkan menjadi ASN pusat.",
        "Menteri Pendidikan menyebut opsi itu dibahas bersama pemerintah daerah.",
        "Pemindahan kewenangan mengikuti aturan yang berlaku.",
        "Pemerintah menghitung anggaran dan jumlah guru yang terdampak.",
        "Pembahasan dilakukan untuk pemerataan distribusi guru.",
        "Langkah berikutnya menunggu rapat lanjutan pekan depan.",
        "Pemerintah menyiapkan bahan pembahasan bersama DPR.",
        "Daerah masih menunggu pembagian kewenangan yang baru.",
        "Anggaran pemindahan dibahas dalam rapat pemerintah.",
        "Keputusan akhir belum diumumkan.",
        "Pemerintah menyampaikan perkembangan aturan.",
        "Rapat lanjutan akan digelar pekan depan.",
    ])
    posts = {f"post_{i}": "Fakta sumber yang cukup panjang untuk validasi. Fakta kedua juga ada." for i in range(1, 7)}
    posts["post_1"] = "Pemerintah mengusulkan guru PPPK dipindahkan menjadi ASN pusat. Opsi ini dibahas resmi."
    posts["post_2"] = "Menteri Pendidikan menyebut opsi itu dibahas bersama pemerintah daerah. Aturan berlaku."
    posts["post_3"] = "Pemerintah menghitung anggaran dan jumlah guru yang terdampak. Anggaran dibahas."
    posts["post_4"] = "Pembahasan dilakukan untuk pemerataan distribusi guru. Rapat lanjutan pekan depan."
    posts["post_5"] = "Pemerintah menyiapkan bahan pembahasan bersama DPR. Daerah masih menunggu pembagian kewenangan."
    posts["post_6"] = "Keputusan akhir belum diumumkan. Menurut lo, aturan atau pembahasan?"
    issues = pipeline._validate_policy_winner_arc({"pattern": "KEBIJAKAN", "body": body}, posts)
    assert any("post_5" in issue for issue in issues)


def test_generic_policy_article_skips_policy_winner_gate():
    body = " ".join([
        "Menteri Pertanian memastikan harga pakan ayam dijaga hingga akhir 2026.",
        "Pemerintah mewajibkan dapur MBG menyerap telur dari peternak sesuai petunjuk teknis.",
        "Kebijakan diambil saat pemerintah merespons keluhan peternak terkait biaya pakan.",
        "Pemerintah memperkuat penyerapan telur melalui dapur SPPG.",
        "Peternak meminta pemerintah membuka transparansi harga bahan baku pakan.",
        "Mereka juga meminta penyerapan telur melalui program pemerintah diperbesar.",
    ] * 4)
    article = {"pattern": "KEBIJAKAN", "title": "Mentan jaga harga pakan ayam", "body": body}
    assert not pipeline._policy_winner_enabled(article)
    assert pipeline.article_evidence_gate(article) is None
    assert pipeline._validate_policy_winner_arc(article, {}) == []


def test_policy_article_evidence_gate_only_applies_to_decision_story():
    base = " ".join(
        f"Dokumen pemerintah nomor {i} memuat rincian pelaksanaan dan pembagian kewenangan untuk rapat resmi."
        for i in range(1, 16)
    ) + " " + ("Catatan administrasi disimpan untuk pemeriksaan pihak terkait. " * 20)
    article = {"pattern": "KEBIJAKAN", "body": base}
    assert pipeline.article_evidence_gate(article) is None

    article["body"] = base + " Pemerintah sebelumnya menyerahkan kewenangan guru ke daerah, tetapi kini mengusulkan pemindahan ke pusat."
    assert pipeline.article_evidence_gate(article) == "policy_missing_literal_tradeoff"

    article["body"] = base + " Pemerintah sebelumnya menyerahkan kewenangan guru ke daerah, tetapi kini mengusulkan pemindahan ke pusat. Kebijakan ini mengurangi beban daerah tetapi menambah anggaran pusat."
    assert pipeline.article_evidence_gate(article) is None


def test_inflight_chain_round_trip_preserves_partial_post_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "INFLIGHT_FILE", tmp_path / "inflight_chain.json")
    state = {"article": {"url": "https://example.test/a"}, "posts": {"post_1": "one"}, "post_ids": ["p1"]}
    pipeline.save_inflight(state)
    assert pipeline.load_inflight() == state


def test_llm_has_room_for_complete_six_post_json():
    class Response:
        status_code = 200
        text = '{"choices": [{"message": {"content": "ok"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    original = pipeline.httpx.post
    pipeline.httpx.post = fake_post
    try:
        content, error = pipeline._call_llm("system", "user", max_retries=1)
    finally:
        pipeline.httpx.post = original

    assert (content, error) == ("ok", None)
    assert captured["max_tokens"] == 4000


def test_learning_bonus_is_bounded_and_needs_three_samples():
    sparse = {"topics": [{"article_source": "A", "arc": "x", "views": 1000, "likes": 100}]}
    assert pipeline._learning_bonus(sparse, "A") == 0
    data = {"topics": (
        [{"article_source": "A", "arc": "x", "views": 1000, "likes": 100}] * 3
        + [{"article_source": "B", "arc": "x", "views": 1000, "likes": 1}] * 3
    )}
    assert 0 < pipeline._learning_bonus(data, "A") <= 0.06
    assert -0.06 <= pipeline._learning_bonus(data, "B") < 0


def test_personal_finance_gets_distinct_arc_and_hook():
    pattern, arc, hook = pipeline._content_metadata(
        "Bunga kredit naik, cicilan rumah makin berat",
        "Bank Indonesia mempertahankan suku bunga. Debitur membayar cicilan lebih tinggi."
    )
    assert pattern == "PASAR"
    assert arc == "personal_finance_explainer"
    assert hook == "finance_practical"


def test_supply_story_gets_supply_shock_arc():
    pattern, arc, _ = pipeline._content_metadata(
        "Harga ayam peternak turun, pasokan antardaerah diatur",
        "Harga di tingkat produsen turun. Pemerintah mengatur distribusi pasokan dan harga konsumen."
    )
    assert pattern == "PERDAGANGAN"
    assert arc == "supply_shock"


def test_fallback_rejects_sentence_fragments():
    body = " ".join([
        "Pemerintah mengatur pasokan ayam di sejumlah daerah.",
        "Harga peternak turun karena distribusi terganggu.",
        "Kemudian harga ayam harga rata-rata nasional Rp36 ribu per kg.",
    ] * 5)
    posts = pipeline._source_fallback_posts({"body": body, "pattern": "PERDAGANGAN"})
    assert posts is None or all(not text.lstrip().lower().startswith("kemudian harga ayam harga")
                                for text in posts.values())


def test_thread_contract_rejects_repeated_s6_numbers():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_1"] = "Harga tercatat Rp36 ribu per kg. Angka ini berasal dari data resmi."
    posts["post_6"] = "Harga tercatat Rp36 ribu per kg. Menurut lo, apa solusinya?"
    assert any("post_6: repeats numeric fact" in issue
               for issue in pipeline.thread_contract_issues(posts, "https://x.test/a"))


def test_learning_bonus_can_use_hook_performance():
    data = {"topics": [
        {"article_source": "A", "pattern": "PASAR", "hook_pattern": "finance_practical",
         "views": 1000, "likes": 100, "replies": 20, "reposts": 10, "quotes": 2}
    ] * 3 + [
        {"article_source": "B", "pattern": "PASAR", "hook_pattern": "number_shock",
         "views": 1000, "likes": 1, "replies": 0, "reposts": 0, "quotes": 0}
    ] * 3}
    assert pipeline._learning_bonus(data, "A", "PASAR", "finance_practical") > 0


def test_refresh_metrics_preserves_data_on_api_failure(monkeypatch):
    topic = {"post_id": "p1", "likes": 7}
    data = {"topics": [topic]}

    def fail(*args, **kwargs):
        raise pipeline.httpx.RequestError("offline")

    monkeypatch.setattr(pipeline, "THREADS_TOKEN", "token")
    monkeypatch.setattr(pipeline.httpx, "get", fail)
    assert pipeline.refresh_performance_metrics(data, now=999999) is False
    assert topic == {"post_id": "p1", "likes": 7}


def test_metrics_request_omits_media_period(monkeypatch):
    captured = {}
    class Response:
        status_code = 200
        def json(self):
            return {"data": [{"name": "views", "values": [{"value": 100}]}]}
    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return Response()
    topic = {"post_id": "p1", "timestamp": "2026-08-11T00:00:00+07:00"}
    monkeypatch.setattr(pipeline, "THREADS_TOKEN", "token")
    monkeypatch.setattr(pipeline.httpx, "get", fake_get)
    assert pipeline.refresh_performance_metrics({"topics": [topic]}, now=999999) is True
    assert "period" not in captured["params"]


def test_performance_evaluator_labels_against_cohort_median():
    topics = [{"views": 1000, "likes": 100, "replies": 10, "reposts": 5, "quotes": 0,
               "timestamp": "2026-08-01T00:00:00+07:00"},
              {"views": 1000, "likes": 1, "replies": 0, "reposts": 0, "quotes": 0,
               "timestamp": "2026-08-01T00:00:00+07:00"},
              {"views": 1000, "likes": 10, "replies": 1, "reposts": 0, "quotes": 0,
               "timestamp": "2026-08-01T00:00:00+07:00"}]
    assert pipeline.evaluate_published_content({"topics": topics}, now=9999999999) is True
    assert {t["performance_evaluation"]["label"] for t in topics} == {"strong", "weak", "normal"}


def test_grounding_validation_does_not_spend_verifier_call(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("grounding must stay deterministic")

    monkeypatch.setattr(pipeline, "_call_llm", fail_if_called)
    issues = pipeline.grounding_validate(
        {"title": "T", "body": "Nilai mencapai Rp1 miliar."},
        {"post_1": "Nilai Rp1 miliar."},
    )
    assert issues == []


def test_claim_markers_block_unsupported_wallet_conclusion():
    issues = pipeline._validate_claim_markers(
        {"post_1": "Surplus ini bukan untung bersih buat kantong kita."},
        "Perdagangan mencatat surplus US$1 miliar.",
    )
    assert "unsupported claim marker 'untung bersih'" in issues[0]


def test_grounding_validation_is_deterministic_and_keeps_editorial_shape_out(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("grounding must not call LLM")

    monkeypatch.setattr(pipeline, "_call_llm", fail_if_called)
    assert pipeline.grounding_validate(
        {"body": "Nilai mencapai Rp1 miliar."},
        {"post_1": "Nilai Rp1 miliar."},
    ) == []


def test_rate_limit_error_retries_twice_with_cooldown_then_stops(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}

    calls = []
    monkeypatch.setattr(pipeline.httpx, "post", lambda *args, **kwargs: calls.append(1) or Response())
    monkeypatch.setattr(pipeline, "_get_api_key", lambda: "test-key")
    assert pipeline._call_llm("system", "user", max_retries=3) == (None, "Rate limit 429")
    # Provider quota: stop immediately; no retry cascade.
    assert calls == [1]

    article = {"body": " ".join(
        f"Fakta sumber unik nomor {i} menjelaskan perubahan kebijakan ekonomi."
        for i in range(1, 7)
    ) * 20}
    monkeypatch.setattr(pipeline, "_call_llm", lambda *args, **kwargs: (None, "LLM failed: Rate limit 429"))
    assert pipeline.is_rate_limit_error("LLM failed: Rate limit 429")
    assert pipeline.generate_thread(article) == (None, "LLM failed: Rate limit 429")
    assert not pipeline.is_rate_limit_error("LLM failed: HTTP 500")


def test_publish_candidates_append_only_body_verified_scout_fallbacks():
    articles = [
        {"url": "https://example.test/global", "title": "Global generic"},
        {"url": "https://example.test/indonesia", "title": "Dampak Indonesia"},
        {"url": "https://example.test/fallback", "title": "Cadangan terverifikasi"},
    ]
    topics = [{"canonical_url": "https://example.test/indonesia", "rank": 1}]
    fallback_topics = [{"canonical_url": "https://example.test/fallback", "rank": 2}]
    assert pipeline._publish_candidates_from_hot_topics(articles, topics, fallback_topics) == [articles[1], articles[2]]


def test_revision_requires_independent_grounding_verifier(monkeypatch):
    article = {"body": " ".join([
        "Pemerintah menetapkan kebijakan baru.",
        "Menteri Keuangan menjelaskan aturan tersebut.",
        "Penyaluran dilakukan melalui basis data penerima.",
        "Program mulai berlaku setelah pembahasan selesai.",
        "Perubahan ini memengaruhi konsumen dan pelaku usaha.",
        "Proses penetapan masih menunggu persetujuan.",
    ]) * 20}
    revised = {
        "status": "success",
        **{f"post_{i}": "Fakta sumber yang cukup panjang." for i in range(1, 7)},
    }
    calls = []

    def fake_llm(*args, **kwargs):
        calls.append(args[0])
        return (json.dumps(revised), None) if len(calls) == 1 else ("FAIL", None)

    monkeypatch.setattr(pipeline, "_call_llm", fake_llm)
    result, error = pipeline.generate_thread(article)
    assert result is None
    assert error == "generation_failed"
    assert len(calls) == 2  # writer + one revision; deterministic evidence fails before verifier


def test_writer_prompt_forbids_unsourced_worker_impact_and_revision_stays_literal():
    assert "Jangan menyebut PHK, nasib karyawan, kompensasi, atau penempatan ulang" in pipeline.SYSTEM_PROMPT
    assert "hapus seluruh frasa yang disebut issue" in pipeline.REVISION_PROMPT
    assert "fakta yang muncul literal di ISI ARTIKEL" in pipeline.REVISION_PROMPT


def test_writer_prompt_uses_full_body_without_title_or_hook_instructions():
    body = "Fakta sumber yang cukup panjang untuk dipakai. " * 300
    prompt = pipeline.build_user_prompt({
        "title": "Judul yang tidak boleh dipakai",
        "url": "https://example.test/untrusted",
        "source": "untrusted_source",
        "pattern": "KEBIJAKAN",
        "pattern_label": "Kebijakan",
        "image_hint": "instruksi palsu",
        "recent_openings": ["instruksi palsu lain"],
        "body": body,
    })
    assert "Fakta sumber yang cukup panjang untuk dipakai." in prompt
    assert "Judul yang tidak boleh dipakai" not in prompt
    assert "https://example.test/untrusted" not in prompt
    assert "instruksi palsu" not in prompt


def test_claim_level_gate_blocks_live_airlangga_overclaims():
    body = " ".join([
        "Airlangga memberikan pekerjaan rumah kepada Maman Abdurrahman untuk mendorong penyaluran kredit UMKM hingga Rp2.000 triliun sepanjang 2026.",
        "Target tersebut dinaikkan dari realisasi kredit UMKM sektor perbankan di kisaran Rp1.500 triliun.",
        "Target ideal 30 persen setara sekitar Rp3.000 triliun, tetapi kenaikan itu terlalu besar dikejar dalam waktu singkat.",
        "Gimana kalau kita turunkan ke Pak Maman Rp2.000 triliun.",
        "Tantangan berikutnya mendorong pembiayaan UMKM di luar skema KUR sekitar Rp370 triliun sampai Rp400 triliun.",
        "Pembiayaan perlu diperluas ke segmen usaha dengan kredit lebih besar sehingga pelaku UMKM dapat naik kelas.",
        "Total pembiayaan UMKM lintas sektor mencapai Rp1.948,72 triliun pada Juni 2026.",
        "Kredit UMKM melalui perbankan mencapai Rp1.519,35 triliun atau 16,73 persen dari total kredit perbankan.",
        "Rasio kredit bermasalah NPL UMKM tercatat 4,54 persen.",
    ])
    posts = {
        "post_1": "Total kredit perbankan Rp9.000 triliun. Ini pertama kalinya pemerintah menaikkan jatah hampir dua kali lipat dalam dua tahun.",
        "post_2": "Airlangga bilang, 'Gimana kalau kita turunkan ke Pak Maman Rp2.000 triliun.' Target resmi naik dari Rp1.500 triliun.",
        "post_3": "KUR sekitar Rp370-400 triliun dan kredit sampai Rp50 miliar.",
        "post_4": "Airlangga bilang, 'Idealnya sih 30% dari total kredit, tapi Rp3.000 triliun keberatan dikejar sekarang.'",
        "post_5": "NPL UMKM masih tinggi 4,54 persen. Bank harus hati-hati biar gak boncos.",
        "post_6": "Kalau bank takut kredit macet, UMKM bakal kesulitan naik kelas. Lo setuju?",
    }
    issues = pipeline.deterministic_grounding_validate({"body": body}, posts)
    assert any("novelty claim" in x for x in issues), issues
    assert any("derived ratio" in x for x in issues), issues
    assert any("unsupported timeline" in x for x in issues), issues
    assert any("official-status claim" in x for x in issues), issues
    assert any("quote not verbatim" in x for x in issues), issues
    assert any("unsupported evaluation" in x for x in issues), issues
    assert any("unsupported motive" in x for x in issues), issues
    assert any("unsupported consequence" in x for x in issues), issues


def test_writer_prompt_requires_two_sentence_s1_and_allows_non_numeric_policy_hook():
    assert "WAJIB 2 kalimat" in pipeline.SYSTEM_PROMPT
    assert "keputusan/perubahan kebijakan yang tertulis" in pipeline.SYSTEM_PROMPT
    assert "aktor berwenang + tindakan" in pipeline.SYSTEM_PROMPT
    assert "Template non-numerik" in pipeline.SYSTEM_PROMPT



def test_deterministic_validate_rejects_slide_without_sentence():
    complete = "Fakta sumber cukup panjang untuk memenuhi batas minimum setiap slide. Konteks sumber menambah rincian yang berbeda."
    posts = {f"post_{i}": complete for i in range(1, 7)}
    posts["post_1"] = "Fakta sumber cukup panjang untuk memenuhi batas minimum tetapi tanpa tanda baca kalimat"
    assert "post_1: no sentences" in pipeline.deterministic_validate(posts)
    posts["post_1"] = complete
    posts["post_6"] = "Fakta sumber cukup panjang untuk memenuhi batas minimum tetapi tanpa tanda baca kalimat"
    assert "post_6: no sentences" in pipeline.deterministic_validate(posts)


def test_style_warnings_do_not_block_quality_gate():
    posts = {f"post_{i}": "Fakta sumber cukup panjang dan lengkap untuk konteks. Bukti kedua menambah rincian." for i in range(1, 7)}
    posts["post_1"] = "Fakta sumber cukup panjang dan lengkap untuk konteks. Bukti sumber menambah konteks."
    posts["post_6"] = "Fakta sumber cukup panjang dan lengkap untuk konteks. Menurut lo?"
    posts["post_2"] = "Padahal fakta sumber cukup panjang dan lengkap untuk konteks. Bukti kedua menambah rincian."
    warnings = pipeline.deterministic_validate(posts)
    assert any("slop 'padahal'" in item for item in warnings)
    assert pipeline._quality_gate({"body": "x"}, {"status": "success"}, posts, []) is True


def test_quality_gate_blocks_revision_style_violation():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_1"] = "x" * 141
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_quality_gate_blocks_missing_s6_cta():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_hook_allows_supported_policy_change_without_forced_number_or_contradiction():
    assert not pipeline.hook_issues("Pemerintah ubah aturan PPN minggu depan.", "Kebijakan berlaku 1 Agustus.")


def test_thread_contract_moves_source_url_to_s7():
    posts = {f"post_{i}": "Fakta sumber. Konteks sumber." for i in range(1, 7)}
    posts["post_6"] = "Takeaway. Apa yang perlu dipantau?"
    issues = pipeline.thread_contract_issues(posts, "https://contoh.go.id/dokumen")
    assert issues == [], issues
    assert posts["post_6"] == "Takeaway. Apa yang perlu dipantau?"
    assert posts["post_7"] == "Sumber: https://contoh.go.id/dokumen"


def test_thread_contract_requires_two_sentences_and_allows_450_chars():
    posts = {f"post_{i}": "Fakta pertama. Konteks kedua." for i in range(1, 7)}
    assert pipeline.thread_contract_issues(posts, "") == []

    posts["post_2"] = "Fakta pertama."
    issues = pipeline.thread_contract_issues(posts, "")
    assert any("post_2: minimum 2 sentences" in issue for issue in issues), issues

    posts["post_2"] = "Kalimat pertama. " + "Konteks tambahan. " * 40
    pipeline.thread_contract_issues(posts, "")
    assert len(posts["post_2"]) <= 450


def test_thread_contract_removes_legacy_url_from_s6_and_adds_s7():
    posts = {f"post_{i}": "Fakta sumber. Konteks tambahan." for i in range(1, 7)}
    posts["post_6"] = "Baca fakta sumber. Menurut lo perlu dipantau?\n\nhttps://tautan-lama.test"
    issues = pipeline.thread_contract_issues(posts, "https://contoh.go.id/dokumen")
    assert issues == [], issues
    assert "http" not in posts["post_6"]
    assert "[URL" not in posts["post_6"]
    assert posts["post_7"] == "Sumber: https://contoh.go.id/dokumen"


def test_thread_contract_rejects_source_slide_over_450_chars():
    posts = {f"post_{i}": "Fakta sumber. Konteks tambahan." for i in range(1, 7)}
    url = "https://contoh.go.id/" + "x" * 430
    issues = pipeline.thread_contract_issues(posts, url)
    assert any("post_7: over 450 chars" in issue for issue in issues)


def test_publish_completion_requires_seven_posts():
    posts = {f"post_{i}": "x" for i in range(1, 8)}
    assert not pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 7)]}, posts)
    assert pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 8)]}, posts)


def test_thread_contract_rejects_over_limit_post():
    posts = {f"post_{i}": "Fakta sumber. Konteks tambahan." for i in range(1, 7)}
    posts["post_3"] = "x" * 451
    assert "post_3: over 450 chars" in pipeline.thread_contract_issues(posts, "")


def test_normalize_s1_uses_compact_limit_and_complete_sentences():
    posts = {"post_1": "Kalimat pertama lengkap. " + "Kalimat kedua sangat panjang " * 30}
    pipeline._normalize_s1(posts, "Fakta sumber cukup panjang. Fakta tambahan cukup panjang.")
    assert len(posts["post_1"]) <= pipeline.S1_CHAR_LIMIT
    assert posts["post_1"].endswith(".")


def test_normalize_s1_uses_compact_220_char_limit():
    posts = {"post_1": "Kalimat hook pertama yang cukup panjang tetapi tetap lengkap. Konteks hook kedua juga harus tetap lengkap."}
    pipeline._normalize_s1(posts, "Fakta sumber tambahan yang tidak boleh dipaksakan masuk ke hook.")
    assert len(posts["post_1"]) <= pipeline.S1_CHAR_LIMIT


def test_thread_contract_rejects_s1_over_compact_limit():
    posts = {f"post_{i}": "Fakta pertama. Konteks kedua." for i in range(1, 7)}
    posts["post_1"] = "Kalimat pertama " + "sangat panjang " * 30 + ". Kalimat kedua tetap bersumber."
    issues = pipeline.thread_contract_issues(posts, "")
    assert any("post_1: over 220 char compact-hook limit" in issue for issue in issues), issues


def test_slide_limit_is_450_and_truncation_keeps_complete_sentences():
    posts = {f"post_{i}": "Fakta sumber. Konteks tambahan." for i in range(1, 7)}
    posts["post_2"] = "Kalimat pertama lengkap. " + "Kalimat kedua sangat panjang " * 30
    issues = pipeline.deterministic_validate(posts)
    assert len(posts["post_2"]) <= 450
    assert posts["post_2"].endswith(".")
    assert "Kalimat kedua sangat panjang Kalimat kedua sangat panjang Kalimat kedua sangat panjang" not in posts["post_2"]
    assert not any("post_2: over" in issue for issue in issues)


def test_thread_contract_all_slides_use_450_char_limit():
    posts = {f"post_{i}": "Kalimat lengkap. " + "Konteks tambahan. " * 30 for i in range(1, 7)}
    pipeline.thread_contract_issues(posts, "")
    assert all(len(posts[f"post_{i}"]) <= 450 for i in range(1, 7))
    assert all(posts[f"post_{i}"].endswith(".") for i in range(1, 7))


def test_sensitive_content_blocks_categorical_verdict_even_if_source_mentions_case():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Pejabat itu jelas korup dan harus dihukum."
    body = "Penyidik menetapkan pejabat tersebut sebagai tersangka dalam perkara dugaan korupsi."
    issues = pipeline._validate_sensitive_language(posts, body)
    assert any("categorical verdict" in issue for issue in issues), issues


def test_unsupported_inference_is_a_hard_grounding_failure():
    article = {"body": "Cadangan devisa turun US$300 juta."}
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Berarti BI jual dolar buat bayar utang negara."
    issues = pipeline._validate_claim_markers(posts, article["body"])
    assert any("berarti" in issue for issue in issues), issues


def test_prompt_guides_empathetic_opinion_without_tightening_filter():
    assert "OPINI EMPATIK — BOLEH, TAPI JANGAN MENGHAKIMI" in pipeline.SYSTEM_PROMPT
    assert "bahasa manusiawi" in pipeline.SYSTEM_PROMPT
    assert "Menurut lo, apa yang perlu dijelaskan?" in pipeline.SYSTEM_PROMPT


def test_conversational_future_and_causal_words_do_not_trigger_retry_alone():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Aturan ini bakal bikin biaya hidup naik."
    assert not pipeline._validate_claim_markers(posts, "Bank mengubah saldo minimum nasabah prioritas.")


def test_strong_inference_marker_remains_a_hard_grounding_failure():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Berarti bank menjual dolar untuk bayar utang."
    assert "post_2: unsupported claim marker 'berarti'" in pipeline._validate_claim_markers(posts, "Cadangan devisa turun.")


def test_unsourced_editorial_claims_are_hard_grounding_failures():
    body = "Danantara menyederhanakan 274 BUMN. Target akhir 652 BUMN."
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_1"] = "274 BUMN dipangkas, tapi separuh jalan sudah kebablasan."
    posts["post_2"] = "COO BP BUMN menyebut proses ini selesai."
    posts["post_5"] = "274 perusahaan sudah kena, sisanya tinggal tunggu giliran."
    posts["post_6"] = "Nasib karyawan bagaimana? Ada skema penempatan ulang atau kompensasi?"
    issues = pipeline._validate_claim_markers(posts, body)
    assert any("kebablasan" in issue for issue in issues), issues
    assert any("coo bp bumn" in issue for issue in issues), issues
    assert any("sudah kena" in issue for issue in issues), issues
    assert any("nasib karyawan" in issue for issue in issues), issues


def test_publish_completion_rejects_partial_chain():
    posts = {f"post_{i}": "x" for i in range(1, 8)}
    assert not pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 7)], "error": "post_7 failed"}, posts)
    assert pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 8)]}, posts)


def test_success_report_sends_expected_telegram_message(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return b'{"ok": true, "result": {"message_id": 1}}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(pipeline, "SZEJAY_BOT_TOKEN", "test-token")
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)
    assert pipeline.send_success_report("Judul", "FACT_FIRST", 142.0, "https://threads.test/post/1")
    assert captured["payload"] == {
        "chat_id": "1022032312",
        "text": "✅ v3 Posted @ " + pipeline.datetime.now(pipeline.WIB).strftime("%H:%M") + " WIB\nJudul\nPattern: FACT_FIRST | 142.0s\nhttps://threads.test/post/1",
    }
    assert captured["timeout"] == 10


def test_success_report_does_not_send_without_token(monkeypatch):
    monkeypatch.setattr(pipeline, "SZEJAY_BOT_TOKEN", "")
    assert not pipeline.send_success_report("Judul", "FACT_FIRST", 1.0, "https://threads.test/post/1")


def test_proper_noun_validation_blocks_invented_institution():
    body = "Bank Indonesia menetapkan aturan baru dengan nilai Rp1 miliar. "
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Kementerian Keuangan ikut mengawasi aturan ini."
    assert any("Kementerian Keuangan" in issue for issue in pipeline._validate_proper_nouns(posts, body))


def test_proper_noun_validation_blocks_reporting_prefix_with_invented_name():
    body = "Pemerintah menyampaikan langkah baru."
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Kata Badan Dana Nasional, langkah baru segera berlaku."
    issues = pipeline._validate_proper_nouns(posts, body)
    assert any("Badan Dana Nasional" in issue for issue in issues)


def test_story_prompt_requires_body_only_story_arc():
    assert "SUMBER ADALAH BATAS" in pipeline.SYSTEM_PROMPT
    assert "ISI ARTIKEL satu-satunya sumber" in pipeline.SYSTEM_PROMPT
    assert "Kata sambung boleh diparafrasekan; jangan mengganti atau menambah makna" in pipeline.SYSTEM_PROMPT
    assert "gua–lu" in pipeline.SYSTEM_PROMPT
    assert "Jangan menambah dampak, profesi, angka, skenario, penilaian" in pipeline.SYSTEM_PROMPT
    assert "Buka dengan fakta paling mahal dan fakta paling kuat" in pipeline.SYSTEM_PROMPT
    assert "buat kalimat pertama menyampaikan fakta" in pipeline.SYSTEM_PROMPT.lower()
    assert "jangan ulang angka, fakta, atau contoh" in pipeline.SYSTEM_PROMPT
    assert "S6 menutup dengan satu pertanyaan spesifik" in pipeline.SYSTEM_PROMPT
    assert "## DAMPAK" not in pipeline.SYSTEM_PROMPT


def test_duplicate_fact_warning_allows_two_slide_reuse():
    posts = {f"post_{i}": "Fakta lain dari artikel." for i in range(1, 7)}
    posts["post_1"] = "652 perusahaan akan dipangkas menjadi 250."
    posts["post_2"] = "Targetnya tinggal 250 dari 652 perusahaan."
    assert pipeline._duplicate_fact_warnings(posts) == []


def test_duplicate_fact_warning_flags_number_reused_across_three_slides():
    posts = {f"post_{i}": "Fakta lain dari artikel." for i in range(1, 7)}
    posts["post_1"] = "652 perusahaan akan dipangkas menjadi 250."
    posts["post_2"] = "Targetnya tinggal 652 perusahaan."
    posts["post_3"] = "Dari 652 perusahaan, 250 akan bertahan."
    assert pipeline._duplicate_fact_warnings(posts) == ["post_3: repeats material numbers from post_1"]


def test_ryanhadiii_voice_allows_gua_lu():
    posts = {"post_1": "Gua dan lu sama-sama lihat harga naik."}
    assert pipeline._voice_warnings(posts) == []


def test_political_title_without_economy_signal_is_not_candidate():
    title = "Prabowo Sebut Kecerdasan Bukan dari Sekolah, Singgung Kampusnya Bahlil"
    assert not pipeline._has_economy_title_signal(title)


def test_market_story_with_policy_in_body_is_not_routine():
    title = "IHSG Melemah Setelah Pengumuman Bank Indonesia"
    body = (
        "Bank Indonesia mengumumkan perubahan kebijakan makroprudensial hari ini. "
        "Kebijakan tersebut mengubah ketentuan pembiayaan perbankan nasional. "
    ) * 20
    assert pipeline._is_routine_market_story(title, body) is False


def test_utility_and_ceremony_titles_fail_full_economy_gate():
    body = "Bank Indonesia menjelaskan aturan dengan nilai Rp1.000.000. " * 30
    for title in (
        "Saldo Minimal Nasabah Prioritas Bank BRI per Agustus 2026",
        "Semangat Koperasi Berhembus di Festival Lembah Baliem",
        "Cara Daftar Magang Kemenkeu 2026",
    ):
        assert pipeline._is_eligible_candidate(title, body, "cnbc_market")[0] is False


def test_keyword_fallback_can_approve_body_verified_article_without_pattern():
    body = "Koperasi, ekonomi, anggaran, pajak, subsidi, investasi, pemerintah, masyarakat, dan UMKM menjadi perhatian. " * 30
    title = "Koperasi Jadi Perhatian Pemerintah"
    assert pipeline._topic_score(title, body)[0] >= 7
    assert pipeline._classify_pattern(title, body) == (None, 0)
    assert pipeline._is_eligible_candidate(title, body, "cnn_ekonomi")[0] is True


def test_mass_layoff_is_not_a_candidate_without_remaining_pindar_pattern():
    title = "Bank Besar Mau PHK Massal, Ini Biang Keroknya"
    body = ("Perusahaan menghadapi PHK massal yang mengancam pekerja dan upah. " * 30)
    assert pipeline._classify_pattern(title, body)[0] is None


def test_hot_topics_are_body_verified_ranked_and_source_diverse(monkeypatch):
    now = 1_800_000_000
    articles = [
        {"title": "Pajak dan APBN Resmi Ditetapkan Rp9 Triliun", "url": "https://a.test/1", "source": "cnn_ekonomi", "ts": now - 60},
        {"title": "Aturan APBN Baru Berlaku", "url": "https://a.test/2", "source": "cnn_ekonomi", "ts": now - 120},
        {"title": "Proyek Infrastruktur dan Investasi Baru", "url": "https://b.test/1", "source": "antara_ekonomi", "ts": now - 180},
    ]
    bodies = {
        "https://a.test/1": "Pemerintah menetapkan kebijakan pajak dan APBN senilai Rp9 triliun untuk penerimaan negara Indonesia. " * 12,
        "https://a.test/2": "Pemerintah menetapkan kebijakan APBN dan peraturan baru untuk anggaran negara Indonesia. " * 12,
        "https://b.test/1": "Pemerintah Indonesia menandatangani kontrak proyek infrastruktur dan investasi senilai Rp8 triliun. " * 12,
    }
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda url: (bodies[url], None, now - 60))

    topics = pipeline.scout_hot_topics(articles, now=now, limit=5, per_source_limit=1)

    assert [topic["canonical_url"] for topic in topics] == ["https://a.test/1", "https://b.test/1"]
    assert all(topic["body_verified"] for topic in topics)
    assert all("hot_score" in topic for topic in topics)


def test_hot_topic_fallback_can_reuse_cluster_after_primary_rejects(monkeypatch):
    now = 1_800_000_000
    articles = [
        {"title": f"Kebijakan APBN seri {i} Rp{i} triliun", "url": f"https://a.test/{i}", "source": "antara_ekonomi", "ts": now - i}
        for i in range(3)
    ]
    body = "Pemerintah Indonesia menetapkan kebijakan APBN senilai Rp9 triliun untuk penerimaan negara. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda _url: (body, None, now - 60))

    primary = pipeline.scout_hot_topics(articles, now=now, limit=15, per_source_limit=2)
    fallback = pipeline.scout_hot_topics(articles, now=now, limit=15, per_source_limit=6, allow_cluster_repeats=True)

    assert len(primary) == 1
    assert len(fallback) == 3
    assert all(topic["body_verified"] for topic in fallback)


def test_hot_topic_default_pool_is_top_15_and_cluster_deduped(monkeypatch):
    now = 1_800_000_000
    articles = [
        {"title": f"Rupiah bergerak seri {i} Rp{i} triliun", "url": f"https://r.test/{i}", "source": "cnn_ekonomi", "ts": now - i}
        for i in range(3)
    ] + [
        {"title": f"Kebijakan APBN seri {i} Rp{i} triliun", "url": f"https://a.test/{i}", "source": "antara_ekonomi", "ts": now - i}
        for i in range(20)
    ]
    body = "Pemerintah Indonesia menetapkan kebijakan APBN senilai Rp9 triliun untuk penerimaan negara. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda _url: (body, None, now - 60))

    topics = pipeline.scout_hot_topics(articles, now=now, per_source_limit=20)

    assert pipeline.HOT_TOPIC_LIMIT == 15
    assert len(topics) <= 15
    assert len({topic["cluster"] for topic in topics}) == len(topics)


def test_literal_fact_allowlist_is_embedded_in_writer_prompt():
    body = "Bank Indonesia menetapkan suku bunga menjadi 5 persen. Nilai rupiah tercatat Rp17.000 per dolar AS."
    prompt = pipeline.build_user_prompt({"body": body})
    assert "ALLOWLIST FAKTA LITERAL" in prompt
    assert "Bank Indonesia menetapkan suku bunga menjadi 5 persen." in prompt
    assert "Jangan membuat fakta baru" in prompt


def test_literal_entity_allowlist_rejects_invented_names():
    body = "The Fed menahan suku bunga. Survei konsumen menunjukkan optimisme. Rupiah menguat."
    entities = pipeline.literal_entity_allowlist(body)
    assert "The Fed" in entities
    assert "Survei Konsumen Juli" not in entities
    assert "Peluang The Fed" not in entities
    prompt = pipeline.build_user_prompt({"body": body})
    assert "NAMA/ENTITAS LITERAL" in prompt
    assert "The Fed" in prompt
    assert "dilarang membuat frasa nama baru" in prompt


def test_proper_noun_validator_accepts_title_prefix_with_source_name():
    body = "Airlangga Hartarto menyampaikan kebijakan pemerintah."
    posts = {"post_1": "Menteri Koordinator Airlangga menyampaikan kebijakan."}
    assert pipeline._validate_proper_nouns(posts, body) == []


def test_prepared_article_requires_unexpired_validated_posts(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREPARED_ARTICLE_FILE", tmp_path / "prepared.json")
    pipeline.PREPARED_ARTICLE_FILE.write_text(json.dumps({"title": "T", "url": "u", "body": "b", "og_image": "i", "posts": {}, "prepared_at": 1, "expires_at": 9_999_999_999}))
    assert pipeline.load_prepared_article(set()) is None


def test_runtime_has_one_active_system_prompt():
    source = Path(__file__).with_name("pipeline-v3.py").read_text()
    assert source.count("SYSTEM_PROMPT = \"\"\"") == 1


def test_writer_prompt_contains_claim_map_and_grounding_contract():
    body = "Pemerintah menetapkan subsidi energi senilai Rp1 triliun. " * 12
    prompt = pipeline.build_user_prompt({"body": body})
    assert "CLAIM MAP S1-S6" in prompt
    assert "Jangan menambah klaim di luar CLAIM MAP" in prompt
    assert "Jangan membuat fakta baru" in prompt


def test_literal_entity_prompt_forbids_new_name_phrases():
    prompt = pipeline.build_user_prompt({"body": "The Fed menahan suku bunga."})
    assert "dilarang membuat frasa nama baru" in prompt


def test_topic_entities_extract_named_economy_entities():
    entities = pipeline._topic_entities("Purbaya Bahas Beban APBD Guru PPPK di Bawah Danantara")
    assert {"purbaya", "pppk", "danantara"} <= entities


def test_repeat_issue_blocks_same_named_entity_but_not_generic_rupiah():
    old = [{"title": "Danantara Salurkan Cuan BUMN ke APBN", "timestamp": "2026-08-12T10:00:00+07:00"}]
    assert pipeline._is_repeat_issue("Purbaya Ungkap Rencana Danantara Masuk APBN", old)[0]
    market = [{"title": "Rupiah Dibuka Melemah ke Rp17.872", "timestamp": "2026-08-12T10:00:00+07:00"}]
    assert not pipeline._is_repeat_issue("Rupiah Ditutup Menguat ke Rp17.800", market)[0]


def test_topic_cohort_separates_explicit_current_from_legacy():
    assert pipeline.topic_cohort({"cohort": pipeline.CURRENT_COHORT}) == pipeline.CURRENT_COHORT
    assert pipeline.topic_cohort({"title": "old"}) == pipeline.LEGACY_COHORT


def test_publisher_pool_keeps_verified_rss_fallback_after_hot_topics():
    articles = [
        {"url": "https://example.test/hot", "title": "Hot"},
        {"url": "https://example.test/rss", "title": "RSS fallback"},
    ]
    hot = [{"canonical_url": "https://example.test/hot"}]
    verified = [{"canonical_url": "https://example.test/hot"}, {"canonical_url": "https://example.test/rss"}]
    assert pipeline._publisher_pool(articles, hot, verified) == articles


def test_prepared_article_normalizes_old_double_url_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREPARED_ARTICLE_FILE", tmp_path / "prepared.json")
    posts = {f"post_{i}": "Dua fakta sumber yang cukup panjang. Fakta kedua lengkap dan berbeda." for i in range(1, 7)}
    posts["post_1"] = "Angka sumber penting untuk pembaca. Dampaknya perlu dilihat bersama."
    posts["post_6"] = "Dua posisi netral. Mana yang lebih masuk akal?\n\nhttps://tautan-lama.test"
    body = " ".join([
        "Angka sumber penting untuk pembaca.",
        "Dua fakta sumber yang cukup panjang.",
        "Fakta kedua lengkap dan berbeda.",
        "Konteks kebijakan ekonomi tersedia.",
        "Data sumber memberi rincian tambahan.",
        "Dua posisi netral yang lebih masuk akal perlu dipantau.",
    ])
    article = {"title": "T", "url": "https://sumber.test", "body": body,
               "og_image": "i", "posts": posts, "prepared_at": 1, "expires_at": 9_999_999_999}
    pipeline.PREPARED_ARTICLE_FILE.write_text(json.dumps(article))
    loaded = pipeline.load_prepared_article(set())
    assert loaded is not None
    assert "http" not in loaded["posts"]["post_6"]
    assert loaded["posts"]["post_7"] == "Sumber: https://sumber.test"


def test_prepared_article_rechecks_current_eligibility_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREPARED_ARTICLE_FILE", tmp_path / "prepared.json")
    body = " ".join([
        "Dalam autobiografinya, tokoh itu mengaku penghasilannya sebagai pensiunan tidak cukup.",
        "Ia lahir 124 tahun lalu dan mundur dari pemerintahan pada 1957.",
        "Putrinya pernah membantu membayar tagihan listrik dan air.",
        "Kisah tersebut dimuat dalam biografi dan surat-surat lama.",
        "Keluarganya hidup pas-pasan setelah tokoh itu pensiun.",
    ] * 4)
    posts = {f"post_{i}": "Fakta sumber yang cukup panjang. Fakta kedua berbeda dan lengkap." for i in range(1, 7)}
    posts["post_7"] = "Sumber: https://example.test/article"
    pipeline.PREPARED_ARTICLE_FILE.write_text(json.dumps({
        "title": "Cerita Pejabat Lama Tak Bisa Bayar Pajak dan Tagihan Rumah",
        "url": "https://example.test/article", "source": "cnbc_entrepreneur",
        "body": body, "og_image": "https://example.test/image.jpg", "posts": posts,
        "prepared_at": 1, "expires_at": 9_999_999_999,
    }))
    assert pipeline.load_prepared_article(set()) is None


def test_hot_topic_scout_rejects_global_story_without_indonesia_connection(monkeypatch):
    now = 1_800_000_000
    article = {"title": "The Fed Naikkan Suku Bunga, Pasar Global Bergejolak", "url": "https://global.test/1", "source": "cnn_global", "ts": now - 60}
    body = "Federal Reserve menaikkan suku bunga dan pasar global bereaksi terhadap inflasi Amerika Serikat. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda _url: (body, None, now - 60))

    assert pipeline.scout_hot_topics([article], now=now) == []


def test_hot_topic_scout_accepts_global_story_with_indonesia_impact(monkeypatch):
    now = 1_800_000_000
    global_article = {"title": "OPEC Pangkas Produksi Minyak, Pemerintah Indonesia Siapkan Kebijakan BBM", "url": "https://global.test/1", "source": "cnn_global", "ts": now - 60}
    domestic_article = {"title": "Proyek Infrastruktur dan Investasi Baru", "url": "https://local.test/1", "source": "antara_ekonomi", "ts": now - 60}
    bodies = {
        global_article["url"]: "OPEC memangkas produksi minyak dunia. Pemerintah Indonesia menyiapkan kebijakan dan peraturan BBM untuk merespons inflasi yang berdampak pada daya beli masyarakat Indonesia. " * 10,
        domestic_article["url"]: "Pemerintah Indonesia menandatangani kontrak proyek infrastruktur dan investasi senilai Rp8 triliun. " * 12,
    }
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda url: (bodies[url], None, now - 60))

    topics = pipeline.scout_hot_topics([domestic_article, global_article], now=now)

    global_topic = next(topic for topic in topics if topic["canonical_url"] == global_article["url"])
    assert global_topic["indonesia_relevance"] == "global_indonesia_impact"


def test_source_verbatim_fallback_is_retired():
    assert not hasattr(pipeline, "source_fallback_thread")
    assert "source_fallback_thread" not in Path(pipeline.__file__ or "").read_text()


def test_fetch_article_body_reads_article_published_time(monkeypatch):
    html = '''<html><head><meta property="article:published_time" content="2026-08-09T10:30:00+07:00"></head><article><p>''' + ("Bukti ekonomi resmi. " * 20) + "</p></article></html>"
    monkeypatch.setattr(pipeline, "_http_get", lambda *_args, **_kwargs: (200, html))

    body, _, published_ts = pipeline._fetch_article_body("https://example.com/article")

    assert len(body) > 200
    assert published_ts == datetime(2026, 8, 9, 3, 30, tzinfo=timezone.utc).timestamp()


def test_fetch_article_body_reads_jsonld_date_published(monkeypatch):
    html = '''<html><head><script type="application/ld+json">{"datePublished":"2026-08-09T10:30:00+07:00"}</script></head><article><p>''' + ("Bukti ekonomi resmi. " * 20) + "</p></article></html>"
    monkeypatch.setattr(pipeline, "_http_get", lambda *_args, **_kwargs: (200, html))

    _, _, published_ts = pipeline._fetch_article_body("https://example.com/article")

    assert published_ts == datetime(2026, 8, 9, 3, 30, tzinfo=timezone.utc).timestamp()


def test_fetch_article_body_reads_time_datetime(monkeypatch):
    html = '''<html><body><time datetime="2026-08-09T10:30:00+07:00"></time><article><p>''' + ("Bukti ekonomi resmi. " * 20) + "</p></article></body></html>"
    monkeypatch.setattr(pipeline, "_http_get", lambda *_args, **_kwargs: (200, html))

    _, _, published_ts = pipeline._fetch_article_body("https://example.com/article")

    assert published_ts == datetime(2026, 8, 9, 3, 30, tzinfo=timezone.utc).timestamp()


def test_article_image_accepts_1200x669_cdn_rounding(monkeypatch):
    monkeypatch.setattr(pipeline.httpx, "get", lambda *_args, **_kwargs: type("Response", (), {
        "status_code": 200, "content": b"image"
    })())
    monkeypatch.setattr(pipeline, "_image_size", lambda _content: (1200, 669))

    assert pipeline.validate_article_image("https://example.test/image.jpg")


def test_pattern_label_has_safe_fallback_for_unclassified_article():
    assert pipeline._pattern_label(None) == "Tidak terklasifikasi"
    assert pipeline._pattern_label("PASAR") == pipeline.ECONOMY_PATTERNS["PASAR"]["label"]


def test_source_config_keeps_only_active_sources_with_required_fields():
    assert pipeline.MAX_ARTICLES_PER_SOURCE == 6
    assert {"cnn_ekonomi", "detik_finance", "cnbc_market", "antara_ekonomi", "bi_release", "kemenkeu_release"} <= set(pipeline.SOURCES)
    assert all({"url", "score", "type", "domain"} <= set(cfg) for cfg in pipeline.SOURCES.values())
    assert {cfg["type"] for cfg in pipeline.SOURCES.values()} <= {"rss", "html"}


def test_source_config_invalid_json_falls_back_to_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "SOURCES_FILE", tmp_path / "sources.json")
    pipeline.SOURCES_FILE.write_text("not json")
    assert pipeline.load_sources() == {}


def test_short_keyword_matching_does_not_match_inside_another_word():
    assert not pipeline._matches_keyword("kemasan makanan", "emas")
    assert pipeline._matches_keyword("harga emas naik", "emas")


def test_html_scraper_resolves_relative_links(monkeypatch):
    monkeypatch.setattr(pipeline, "_http_get", lambda *_: (200, '<a href="/rilis/ekonomi">Rilis ekonomi terbaru yang relevan</a>'))
    articles = pipeline._scrape_html("https://example.go.id/siaran-pers", "official", 10, "example.go.id/")
    assert articles[0]["url"] == "https://example.go.id/rilis/ekonomi"


def test_engagement_prefers_reposts_replies_and_likes_per_view():
    data = {"topics": [
        {"article_source": "good", "arc": "market_shock", "views": 1000, "likes": 80, "replies": 20, "reposts": 30},
        {"article_source": "bad", "arc": "debt_trap", "views": 3000, "likes": 5, "replies": 0, "reposts": 0},
    ]}
    stats = pipeline._compute_performance_stats(data)
    assert stats["source_avg"]["good"] > stats["source_avg"]["bad"]


def test_hook_metadata_is_deterministic_and_not_market_default():
    title = "Danantara Targetkan 4 BUMN IPO, Ada Pegadaian-Pelindo"
    body = "Danantara menargetkan empat BUMN melakukan IPO dalam 6-12 bulan."
    pattern, arc, hook = pipeline._content_metadata(title, body)
    assert pattern == "PASAR"
    assert arc != "market_shock"
    assert hook in {"number_shock", "decision_impact", "wallet_impact", "named_decision"}


def test_pattern_feedback_requires_three_samples():
    data = {"topics": [
        {"article_source": "A", "pattern": "PASAR", "views": 1000, "likes": 10},
        {"article_source": "A", "pattern": "PASAR", "views": 1000, "likes": 10},
        {"article_source": "A", "pattern": "PASAR", "views": 1000, "likes": 10},
        {"article_source": "A", "pattern": "KEBIJAKAN", "views": 1000, "likes": 1},
        {"article_source": "A", "pattern": "KEBIJAKAN", "views": 1000, "likes": 1},
        {"article_source": "A", "pattern": "KEBIJAKAN", "views": 1000, "likes": 1},
    ]}
    assert pipeline._learning_bonus(data, "A", "PASAR") > pipeline._learning_bonus(data, "A", "KEBIJAKAN")


def test_claim_markers_block_unsupported_editorial_leaps():
    issues = pipeline._validate_claim_markers(
        {"post_2": "Tetangga yang jualan nasi uduk ikut menanggung beban negara."},
        "Pemerintah membutuhkan investasi swasta.",
    )
    assert any("tetangga" in issue for issue in issues)


def test_score_rewards_concrete_public_impact():
    routine = {"title": "Rupiah Menguat Tajam Hari Ini", "url": "https://x.test/a"}
    concrete = {"title": "Prabowo Tetapkan Subsidi Rp80 Triliun, Beban APBN Berubah", "url": "https://x.test/b"}
    assert pipeline._score_article(concrete)[0] > pipeline._score_article(routine)[0]


def test_number_grounding_allows_source_decimal_rounding():
    body = "Total pembiayaan UMKM mencapai Rp 1.948,72 triliun. Kredit bank Rp 1.519,35 triliun."
    posts = {
        "post_1": "Pembiayaan mencapai Rp 1.948 triliun.",
        "post_2": "Kredit bank mencapai Rp 1.519 triliun.",
    }
    assert pipeline._validate_numbers(posts, body) == []


def test_claim_grounding_blocks_unsupported_analogies_and_inferences():
    body = "OJK mencatat kredit UMKM mencapai Rp 1.519,35 triliun dengan NPL 4,54%."
    posts = {
        "post_1": "Kredit UMKM Rp 1.519 triliun. Tapi lebih pelan dari inflasi lo bayar tiap beli gorengan.",
        "post_2": "NPL 4,54% artinya dari 100 pengusaha hampir 5 gagal bayar cicilan.",
        "post_3": "Bank lebih pelit dari tukang parkir yang ngutang.",
    }
    issues = pipeline._validate_claim_markers(posts, body)
    assert any("gorengan" in issue for issue in issues)
    assert any("gagal bayar" in issue for issue in issues)


def test_source_slide_audit_reports_lexical_matches_without_blocking():
    body = "Bank Indonesia menetapkan suku bunga menjadi 5 persen. Rupiah tercatat Rp17.000 per dolar AS."
    posts = {
        "post_1": "Bank Indonesia menetapkan suku bunga 5 persen.",
        "post_2": "Menurut lo, kebijakan ini perlu dipantau?",
    }
    audit = pipeline.source_slide_audit(body, posts)
    assert audit["post_1"]["lexical_match"] is True
    assert audit["post_1"]["source_sentences"] == [1]
    assert audit["post_2"]["lexical_match"] is False
    assert "audit" in audit["post_2"]


if __name__ == "__main__":
    test_ungrounded_rupiah_range_is_rejected()
    test_thin_article_is_rejected_before_generation()
    test_source_claim_plan_uses_article_sentences_only()
    class MonkeyPatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    test_grounding_validation_does_not_spend_verifier_call(MonkeyPatch())
    test_hook_allows_supported_policy_change_without_forced_number_or_contradiction()
    test_engagement_prefers_reposts_replies_and_likes_per_view()
    print("PASS")
