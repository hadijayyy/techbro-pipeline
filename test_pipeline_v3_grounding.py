#!/usr/bin/env python3
"""Regression tests for Techbro v3 factual grounding and engagement scoring."""
import json
import importlib.util
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
    assert pipeline.article_evidence_gate({"body": "Nilai mencapai Rp17.976. " * 60}) is None


def test_source_claim_plan_uses_article_sentences_only():
    article = {"body": "Rupiah berada di Rp17.976 per dolar AS. Ekonom memproyeksikan pelemahan berlanjut. Kalimat pendek."}
    plan = pipeline.source_claim_plan(article)
    assert "Rp17.976" in plan
    assert "Kalimat pendek." not in plan


def test_grounding_verifier_error_blocks_publish(monkeypatch):
    monkeypatch.setattr(pipeline, "_call_llm", lambda *args, **kwargs: (None, "timeout"))
    issues = pipeline.grounding_validate({"title": "T", "body": "B"}, {"post_1": "T."})
    assert issues == ["grounding: verifier unavailable"], issues


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


def test_proper_noun_validation_blocks_invented_institution():
    body = "Bank Indonesia menetapkan aturan baru dengan nilai Rp1 miliar. "
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Kementerian Keuangan ikut mengawasi aturan ini."
    assert any("Kementerian Keuangan" in issue for issue in pipeline._validate_proper_nouns(posts, body))


def test_story_prompt_requires_source_anchored_story_arc():
    assert "SUMBER ADALAH BATAS" in pipeline.SYSTEM_PROMPT
    assert "Jangan menambah dampak, profesi, angka, atau skenario" in pipeline.SYSTEM_PROMPT
    assert "S1 fakta pemicu atau perubahan konkret" in pipeline.SYSTEM_PROMPT


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


def test_keyword_fallback_cannot_approve_article_without_pindar_pattern():
    body = "Koperasi, ekonomi, anggaran, pajak, subsidi, investasi, pemerintah, masyarakat, dan UMKM menjadi perhatian. " * 30
    title = "Semangat Koperasi Berhembus di Festival Lembah Baliem"
    assert pipeline._topic_score(title, body)[0] >= 7
    assert pipeline._classify_pattern(title, body) == (None, 0)
    assert pipeline._is_eligible_candidate(title, body, "cnn_ekonomi")[0] is False


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
