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
    assert pipeline.article_evidence_gate({"body": "teks " * 250}) == "no_numeric_or_quote_evidence"
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
    assert "Kalimat pendek." not in plan


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


def test_refresh_metrics_preserves_data_on_api_failure(monkeypatch):
    topic = {"post_id": "p1", "likes": 7}
    data = {"topics": [topic]}

    def fail(*args, **kwargs):
        raise pipeline.httpx.RequestError("offline")

    monkeypatch.setattr(pipeline, "THREADS_TOKEN", "token")
    monkeypatch.setattr(pipeline.httpx, "get", fail)
    assert pipeline.refresh_performance_metrics(data, now=999999) is False
    assert topic == {"post_id": "p1", "likes": 7}


def test_grounding_verifier_error_blocks_publish(monkeypatch):
    captured = {}

    def fake_llm(*args, **kwargs):
        captured.update(kwargs)
        return None, "timeout"

    monkeypatch.setattr(pipeline, "_call_llm", fake_llm)
    issues = pipeline.grounding_validate({"title": "T", "body": "B"}, {"post_1": "T."})
    assert issues == ["grounding: verifier unavailable"], issues
    assert captured["temperature"] == 0


def test_claim_markers_block_unsupported_wallet_conclusion():
    issues = pipeline._validate_claim_markers(
        {"post_1": "Surplus ini bukan untung bersih buat kantong kita."},
        "Perdagangan mencatat surplus US$1 miliar.",
    )
    assert "unsupported claim marker 'untung bersih'" in issues[0]


def test_grounding_verifier_checks_facts_not_cta_or_editorial_shape(monkeypatch):
    captured = {}

    def fake_llm(system, *args, **kwargs):
        captured["system"] = system
        return "PASS", None

    monkeypatch.setattr(pipeline, "_call_llm", fake_llm)
    assert pipeline.grounding_validate({"body": "Nilai mencapai Rp1 miliar."}, {"post_1": "Nilai Rp1 miliar."}) == []
    assert "standar fail-closed" in captured["system"]
    assert "mengubah surplus menjadi klaim untung bersih" in captured["system"]


def test_rate_limit_error_retries_twice_with_cooldown_then_stops(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}

    calls = []
    monkeypatch.setattr(pipeline.httpx, "post", lambda *args, **kwargs: calls.append(1) or Response())
    monkeypatch.setattr(pipeline, "_get_api_key", lambda: "test-key")
    assert pipeline._call_llm("system", "user", max_retries=3) == (None, "Rate limit 429")
    # initial call + 2 bounded cooldown retries, then error propagates
    assert calls == [1, 1, 1]

    article = {"body": "Nilai mencapai Rp1 miliar. " * 60}
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
    article = {"body": "Nilai mencapai Rp1 miliar. " * 60}
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
    assert error == "LLM failed after 2 attempts"
    assert len(calls) >= 2


def test_writer_prompt_forbids_unsourced_worker_impact_and_revision_stays_literal():
    assert "Jangan menyebut PHK, nasib karyawan, kompensasi, atau penempatan ulang" in pipeline.SYSTEM_PROMPT
    assert "hapus seluruh frasa yang disebut issue" in pipeline.REVISION_PROMPT
    assert "fakta yang muncul literal di ISI ARTIKEL" in pipeline.REVISION_PROMPT


def test_writer_prompt_uses_full_body_without_title_or_hook_instructions():
    body = "Fakta satu. " * 300
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
    assert body in prompt
    assert "Judul yang tidak boleh dipakai" not in prompt
    assert "https://example.test/untrusted" not in prompt
    assert "instruksi palsu" not in prompt


def test_writer_prompt_requires_two_sentence_s1_and_allows_non_numeric_policy_hook():
    assert "WAJIB 2 kalimat" in pipeline.SYSTEM_PROMPT
    assert "keputusan resmi/perubahan kebijakan baru" in pipeline.SYSTEM_PROMPT
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


def test_quality_gate_blocks_revision_style_violation():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_1"] = "x" * 141
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_quality_gate_blocks_missing_s6_cta():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_hook_allows_supported_policy_change_without_forced_number_or_contradiction():
    assert not pipeline.hook_issues("Pemerintah ubah aturan PPN minggu depan.", "Kebijakan berlaku 1 Agustus.")


def test_thread_contract_keeps_source_url_within_500_char_limit():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_6"] = "Takeaway. Apa yang perlu dipantau?"
    issues = pipeline.thread_contract_issues(posts, "https://contoh.go.id/dokumen")
    assert issues == [], issues
    assert posts["post_6"].endswith("https://contoh.go.id/dokumen")


def test_thread_contract_replaces_url_placeholder_variants():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_6"] = "Baca [URL sumber]."
    pipeline.thread_contract_issues(posts, "https://contoh.go.id/dokumen")
    assert "[URL" not in posts["post_6"]
    assert posts["post_6"].count("https://contoh.go.id/dokumen") == 1


def test_thread_contract_rejects_over_limit_post():
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_3"] = "x" * 501
    assert "post_3: over 500 chars" in pipeline.thread_contract_issues(posts, "")


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
    posts = {"post_1": "a", "post_2": "b"}
    assert not pipeline._publish_complete({"post_ids": ["1"], "error": "post_2 failed"}, posts)
    assert pipeline._publish_complete({"post_ids": ["1", "2"]}, posts)


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
        "chat_id": "8771306538",
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
    assert "Buka dengan fakta paling mahal" in pipeline.SYSTEM_PROMPT
    assert "Buat kalimat pertama menyampaikan fakta" in pipeline.SYSTEM_PROMPT
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


def test_prepared_article_normalizes_old_double_url_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREPARED_ARTICLE_FILE", tmp_path / "prepared.json")
    posts = {f"post_{i}": "Dua fakta sumber. Fakta kedua lengkap." for i in range(1, 7)}
    posts["post_1"] = "Angka sumber penting. Dampaknya perlu dilihat."
    posts["post_6"] = "Dua posisi netral. Mana yang lebih masuk akal?\n\nhttps://tautan-lama.test"
    article = {"title": "T", "url": "https://sumber.test", "body": "Dua fakta sumber. Fakta kedua lengkap.",
               "og_image": "i", "posts": posts, "prepared_at": 1, "expires_at": 9_999_999_999}
    pipeline.PREPARED_ARTICLE_FILE.write_text(json.dumps(article))
    loaded = pipeline.load_prepared_article(set())
    assert loaded is not None
    assert loaded["posts"]["post_6"].count("http") == 1
    assert loaded["posts"]["post_6"].endswith(article["url"])


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


if __name__ == "__main__":
    test_ungrounded_rupiah_range_is_rejected()
    test_thin_article_is_rejected_before_generation()
    test_source_claim_plan_uses_article_sentences_only()
    class MonkeyPatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    test_grounding_verifier_error_blocks_publish(MonkeyPatch())
    test_hook_allows_supported_policy_change_without_forced_number_or_contradiction()
    test_engagement_prefers_reposts_replies_and_likes_per_view()
    print("PASS")
