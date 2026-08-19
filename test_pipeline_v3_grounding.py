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


def test_generation_retries_remaining_eligible_candidates():
    candidates = [{"url": f"https://example.com/{i}"} for i in range(3)]
    remaining = pipeline._remaining_eligible_candidates(candidates, candidates[0]["url"])
    assert [item["url"] for item in remaining] == [
        "https://example.com/1", "https://example.com/2"
    ]


def test_generation_retry_is_bounded_to_one_candidate():
    source = Path(pipeline.__file__ or "").read_text()
    assert "for retry_article in retry_candidates[:1]:" in source


def test_fallback_allows_concessive_begitu_opener():
    posts = {"post_5": "Kendati begitu, peluang tersebut tetap terbuka."}
    assert pipeline._source_fallback_dangling_refs(posts) == []


def test_thin_article_is_rejected_before_generation():
    assert pipeline.article_evidence_gate({"body": "Fakta ekonomi."}) == "body_under_500_chars"
    assert pipeline.article_evidence_gate({"body": "teks " * 250}) == "insufficient_source_claims_for_four_posts"
    body = " ".join(f"Nilai bulan {month} mencapai Rp{month}." for month in range(1, 7)) * 12
    assert pipeline.article_evidence_gate({"title": "Kebijakan subsidi", "body": body}) is None


def test_source_cleanup_rejects_cnbc_dateline_and_truncated_sentence():
    body = (
        "Jakarta, CNBC Indonesia - Presiden Prabowo belum menyinggung rencana kenaikan gaji ASN. "
        "sebagaimana saat memberikan pidato nota keuangan dan. "
        "Pemerintah masih membahas kebijakan tersebut untuk tahun 2027."
    )
    cleaned = pipeline._clean_source_body(body)
    assert "Jakarta, CNBC Indonesia" not in cleaned
    assert all("sebagaimana saat memberikan" not in sentence
               for sentence in pipeline._source_sentences(body))
    assert all("Jakarta, CNBC Indonesia" not in fact
               for fact in pipeline.literal_fact_allowlist(body))


def test_language_gate_rejects_english_source_fallback_output():
    posts = {f"post_{i}": "The market has been more resilient than expected." for i in range(1, 7)}
    issues = pipeline._indonesian_language_issues(posts)
    assert len(issues) == 6, issues


def test_language_gate_accepts_indonesian_output_with_english_proper_nouns():
    posts = {f"post_{i}": "Pasar ini tetap kuat, menurut gw, meski Goldman Sachs disebut di sumber." for i in range(1, 7)}
    assert pipeline._indonesian_language_issues(posts) == []


def test_english_only_global_source_rejected_before_llm_generation():
    body = ("The market has been more resilient than expected and investors are watching earnings. " * 20)
    ok, reason = pipeline._is_eligible_candidate(
        "Global earnings lift stocks", body, "cnbc_global"
    )
    assert not ok
    assert reason == "source_body_english_only"


def test_indonesian_source_not_rejected_by_english_body_heuristic():
    body = ("Pemerintah menetapkan aturan investasi dan perusahaan membuka lapangan kerja. " * 20)
    ok, reason = pipeline._is_eligible_candidate(
        "Pemerintah tetapkan aturan investasi", body, "cnn_ekonomi"
    )
    assert ok, reason


def test_candidate_selection_allows_body_above_relaxed_minimum():
    body = "Pemerintah menetapkan subsidi energi untuk rumah tangga Indonesia. " * 14
    ok, reason = pipeline._is_eligible_candidate(
        "Pemerintah Tetapkan Subsidi Energi untuk Rumah Tangga", body, "cnn_ekonomi"
    )
    assert ok, reason


def test_candidate_selection_allows_consumer_advice_title_for_viral_testing():
    body = "AFPI menjelaskan penilaian kredit dan pembiayaan UMKM di Indonesia. " * 30
    ok, reason = pipeline._is_eligible_candidate(
        "Nomor HP Bisa Jadi Pengganti Skor Kredit, Begini Syaratnya", body, "cnbc_market"
    )
    assert not ok
    assert reason == "personal_finance_advice"


def test_editorial_selection_rejects_personal_finance_how_to():
    title = "Nomor HP Bisa Jadi Pengganti Skor Kredit, Begini Syaratnya"
    body = "AFPI menjelaskan cara penilaian kredit dan syarat pembiayaan UMKM di Indonesia. " * 30
    assert pipeline._editorial_candidate_gate(title, body) == "personal_finance_advice"


def test_editorial_selection_rejects_retail_event_even_with_large_target():
    title = "Geliat Pusat Belanja di Momen Long Weekend HUT RI ke-81"
    body = ("Pusat belanja menggelar festival dan diskon untuk mengejar target transaksi "
            "Rp38,97 triliun pada long weekend. " * 20)
    assert pipeline._editorial_candidate_gate(title, body) == "retail_event_promotion"


def test_editorial_selection_requires_economy_topic_and_material_change():
    body = "Perusahaan menjelaskan strategi bisnis dan layanan baru bagi pengguna di Indonesia. " * 20
    assert pipeline._editorial_candidate_gate("Perusahaan Rilis Layanan Baru", body) == "no_material_economic_topic"


def test_editorial_selection_accepts_national_policy_story():
    body = "Pemerintah menetapkan subsidi energi untuk rumah tangga Indonesia. " * 20
    assert pipeline._editorial_candidate_gate("Pemerintah Tetapkan Subsidi Energi", body) is None


def test_transmart_promo_final_veto_ignores_wallet_pressure_angle():
    title = "Merdeka Belanja di Transmart Full Day Sale Hari Ini, Diskon 50% + 20%"
    body = ("Transmart menawarkan diskon 50 persen untuk kebutuhan harian dan produk lain. "
            "Tambahan diskon 20 persen berlaku dengan minimal transaksi Rp300 ribu "
            "dan pembayaran Allo Prime, Allo Paylater, atau kartu kredit Bank Mega. "
            "Promo berlangsung satu hari di seluruh gerai.")
    article = {"title": title, "body": body}
    result = {"angle": "wallet_pressure", "arc": "wallet_pressure"}
    assert pipeline._is_low_value_promo(title, body)
    assert not pipeline.has_material_economic_signal(title, body)
    assert pipeline._final_publish_veto(article, result) == (
        "LOW_VALUE_PROMO: material_economic_signal=False"
    )


def test_conflict_priority_rewards_decision_payer_and_beneficiary():
    strong = "Pemerintah Tetapkan Pajak Rp10 Triliun, Pengusaha Bayar untuk Subsidi"
    generic = "Perusahaan Bahas Strategi Bisnis di Era Digital"
    assert pipeline._engagement_priority_bonus(strong, strong) > pipeline._engagement_priority_bonus(generic, generic)


def test_story_selection_rewards_concrete_event_chain_and_human_stakes():
    concrete = "Serangan merusak pabrik baja, produksi berhenti dan logistik terganggu. Pekerja dan warga disebut terdampak; pemulihan belum jelas."
    generic = "Perusahaan membahas strategi bisnis dan peluang pertumbuhan di era digital."
    assert pipeline._story_selection_bonus("Pabrik baja rusak, produksi berhenti", concrete) > pipeline._story_selection_bonus("Strategi bisnis perusahaan", generic)


def test_household_impact_replaces_wallet_pressure_taxonomy():
    _, arc, _ = pipeline._content_metadata(
        "Harga BBM Naik 10 Persen, Daya Beli Rumah Tangga Tertekan",
        "Pemerintah menetapkan harga BBM naik 10 persen dan membahas daya beli rumah tangga. " * 3,
    )
    assert arc == "household_impact"
    assert "wallet_pressure" not in arc


def test_draft_requires_four_source_claims_before_llm():
    body = ("Bank Indonesia menetapkan suku bunga menjadi 5 persen. "
            + "Narasi tanpa fakta tambahan. " * 50)
    assert pipeline.article_evidence_gate({"body": body}) == "insufficient_source_claims_for_four_posts"


def test_source_claim_plan_uses_article_sentences_only():
    article = {"body": "Rupiah berada di Rp17.976 per dolar AS. Ekonom memproyeksikan pelemahan berlanjut. Kalimat pendek."}
    plan = pipeline.source_claim_plan(article)
    assert "Rp17.976" in plan
    assert "pelemahan berlanjut" in plan
    assert "Kalimat pendek." not in plan


def test_source_fallback_rejects_dangling_demonstrative_opener():
    posts = {
        "post_1": "Penyerahan secara simbolis ini diikuti penyaluran 771 paket sembako.",
        "post_2": "PT Pertamina menyerahkan dukungan PLTS kepada pekerja TPS 3R.",
        "post_3": "Hendry mengatakan biaya listrik turun 60 persen.",
        "post_4": "Dengan PLTS kebutuhan listrik kami dihemat 60 persen.",
        "post_5": "Ardian bekerja 2,5 tahun di TPS 3R GO-SARI.",
        "post_6": "Manfaat ekonomi dirasakan pekerja. Menurut lo, rumah tangga atau biaya?",
    }
    issues = pipeline._source_fallback_dangling_refs(posts)
    assert any("post_1" in i and "dangling" in i for i in issues), issues


def test_source_fallback_accepts_clean_opener():
    posts = {
        "post_1": "PLTS berkapasitas 6,6 kWp dipasang di TPS 3R GO-SARI Bantul.",
        "post_2": "PT Pertamina menyerahkan dukungan PLTS kepada pekerja TPS 3R.",
        "post_3": "Hendry mengatakan biaya listrik turun 60 persen.",
        "post_4": "Dengan PLTS kebutuhan listrik dihemat 60 persen.",
        "post_5": "Ardian bekerja 2,5 tahun di TPS 3R GO-SARI.",
        "post_6": "Manfaat ekonomi dirasakan pekerja. Menurut lo, rumah tangga atau biaya?",
    }
    assert pipeline._source_fallback_dangling_refs(posts) == []


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


def test_article_body_ignores_nested_paragraph_duplicates_and_publisher_cta(monkeypatch):
    html = """<html><article><p>Outer duplicate wrapper that must not enter evidence.
    <p>Fakta artikel pertama cukup panjang dan hanya boleh muncul satu kali dalam badan sumber, lengkap dengan konteks ekonomi yang diperlukan pembaca.</p>
    <p>Ikuti Whatsapp Channel Republika Sebagai kanal informasi tambahan untuk pembaca.</p>
    <p>Fakta artikel kedua cukup panjang dan tetap menjadi bagian badan sumber yang sah, lengkap dengan angka kebijakan serta dampaknya bagi rumah tangga.</p>
    </p></article></html>"""
    monkeypatch.setattr(pipeline, "_http_get", lambda url, timeout=15: (200, html))
    pipeline._BODY_CACHE.clear()
    body, _, _ = pipeline._fetch_article_body("https://example.test/article-nested")
    assert body.count("Fakta artikel pertama") == 1
    assert "Whatsapp Channel" not in body
    assert "Fakta artikel kedua" in body


def test_article_body_cuts_embedded_publisher_cta_after_source_fact(monkeypatch):
    html = """<html><article><p>Shein didenda regulator Italia karena manipulasi informasi lingkungan dalam pemasaran produknya. Regulator menyebut informasi keberlanjutan itu menyesatkan konsumen dan menjatuhkan sanksi setelah pemeriksaan resmi. Perusahaan juga diminta memperbaiki penjelasan dampak lingkungannya. Dapatkan akses cepat ke berita terkini dan data berharga dari WhatsApp Channel Katadata.co.id Dapatkan pengalaman membaca lebih nyaman lewat aplikasi mobile Katadata.</p></article></html>"""
    monkeypatch.setattr(pipeline, "_http_get", lambda url, timeout=15: (200, html))
    pipeline._BODY_CACHE.clear()
    body, _, _ = pipeline._fetch_article_body("https://example.test/article-embedded-cta")
    assert "Shein didenda regulator Italia" in body
    assert "Dapatkan akses cepat" not in body
    assert "WhatsApp Channel" not in body


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


def test_corporate_promo_article_is_rejected_before_llm():
    title = "wondr by BNI Bantu Nasabah Kelola Keuangan Keluarga Lebih Terencana"
    body = ("Agis memakai aplikasi wondr by BNI untuk mengatur keuangan keluarga. "
            "BNI membantu nasabah menabung dan mengatur dana harian dengan layanan digital. "
            "Program rejeki wondr BNI 2025 memberi hadiah sepeda motor kepada nasabah. "
            "BNI terus berkomitmen menghadirkan solusi keuangan yang bermanfaat bagi masyarakat. "
            "Layanan ini dibuat agar nasabah lebih disiplin dan nyaman mengelola keuangan. " * 5)
    ok, reason = pipeline._is_eligible_candidate(title, body, "detik_finance")
    assert not ok
    assert reason == "corporate_promo"


def test_substantive_corporate_news_is_not_rejected_as_promo():
    title = "Bank X Catat Laba Bersih Rp2 Triliun, Naik 20 Persen"
    body = ("Bank X di Indonesia mencatat laba bersih Rp2 triliun pada 2026, naik 20 persen dari tahun sebelumnya. "
            "Pendapatan tumbuh setelah perusahaan menekan biaya operasional. "
            "Manajemen menyiapkan investasi dan membagikan dividen kepada pemegang saham. " * 5)
    ok, _ = pipeline._is_eligible_candidate(title, body, "cnbc_market")
    assert ok


def test_corporate_profit_profile_without_public_event_is_rejected():
    title = "IFG Life Bukukan Laba Bersih Rp482,12 Miliar Sepanjang 2025"
    body = ("IFG Life mencatat laba bersih Rp482,12 miliar sepanjang 2025. "
            "RBC perusahaan berada di atas batas minimum OJK. "
            "Manajemen menjaga keseimbangan pertumbuhan bisnis dan risiko. "
            "Perusahaan menerapkan PSAK 117 agar laporan lebih jelas bagi pemegang polis. " * 8)
    ok, reason = pipeline._is_eligible_candidate(title, body, "detik_finance")
    assert not ok
    assert reason == "low_value_corporate_story"


def test_routine_company_digital_strategy_is_rejected():
    title = "Jaga Kualitas Aset, Begini Strategi Bank Aladin Syariah di Era Digital"
    body = ("Bank Aladin Syariah mengembangkan layanan digital untuk nasabah. "
            "Perusahaan menjaga prinsip syariah, tata kelola, risiko, keamanan, dan privasi. "
            "Strategi ini ditujukan agar bisnis bertahan dan nasabah nyaman. " * 10)
    ok, reason = pipeline._is_eligible_candidate(title, body, "cnbc_market")
    assert not ok
    assert reason == "low_value_corporate_story"


def test_routine_single_stock_explainer_is_rejected():
    title = "Ini Deretan Alasan Kenapa Saham GOTO Susah Gerak dari Harga Rp50"
    body = ("Saham GOTO berada di level Rp50. Analis menyebut kondisi pasar global dan harga minyak "
            "menjadi sentimen negatif bagi saham negara berkembang. Manajemen menyatakan fundamental "
            "perusahaan tetap kuat dan sedang menggodok aksi korporasi. " * 10)
    ok, reason = pipeline._is_eligible_candidate(title, body, "cnbc_market")
    assert not ok
    assert reason == "routine market story"


def test_subsidy_distribution_administration_is_rejected():
    title = "Gandeng Dukcapil, Pertamina Perketat Penyaluran Subsidi Elpiji 3 Kg"
    body = ("Pertamina menggandeng Dukcapil untuk memperketat penyaluran subsidi elpiji 3 kg. "
            "Data penerima diverifikasi menggunakan identitas kependudukan agar penyaluran lebih tepat sasaran. "
            "Program ini membahas pendataan, verifikasi, dan distribusi tabung kepada masyarakat. " * 8)
    ok, reason = pipeline._is_eligible_candidate(title, body, "cnn_ekonomi")
    assert not ok
    assert reason == "administrative_distribution_story"


def test_subsidy_price_change_remains_economy_story():
    title = "Harga Eceran Tertinggi Elpiji 3 Kg Naik, Anggaran Subsidi Diubah"
    body = ("Pemerintah mengubah harga eceran tertinggi elpiji 3 kg dan anggaran subsidi energi. "
            "Perubahan ini berdampak pada biaya rumah tangga dan daya beli masyarakat. "
            "Aturan baru menetapkan nilai subsidi dan kelompok penerima yang berhak. " * 8)
    ok, _ = pipeline._is_eligible_candidate(title, body, "cnn_ekonomi")
    assert ok


def test_global_economy_story_requires_indonesia_impact():
    title = "The Fed Naikkan Suku Bunga, Pasar Global Bergejolak"
    body = ("Federal Reserve menaikkan suku bunga dan pasar global bereaksi terhadap inflasi Amerika Serikat. " * 12)
    assert pipeline._indonesia_topic_relevance(title, body) == "international"

    connected = body + (" Kebijakan ini menekan rupiah dan meningkatkan biaya impor Indonesia, "
                        "sehingga daya beli masyarakat ikut terdampak.")
    assert pipeline._indonesia_topic_relevance(title, connected) == "global_indonesia_impact"
    assert pipeline._has_economy_title_signal(title)

    english_title = "US Stocks Slide as Interest Rates Stay High"
    english_body = ("US stocks fell as interest rates stayed high. Indonesia may face higher "
                    "financing costs and weaker export demand, affecting rupiah and investment. " * 8)
    assert pipeline._indonesia_topic_relevance(english_title, english_body) == "global_indonesia_impact"
    assert pipeline._is_global_finance_story(english_title, english_body)


def test_common_ministry_short_form_is_allowed_when_source_has_full_name():
    body = ("Menteri Keuangan Purbaya Yudhi Sadewa menyampaikan target penerimaan perpajakan 2027. "
            "Pemerintah membahas kebijakan pajak dan anggaran negara. " * 8)
    posts = {
        "post_1": "Menkeu Purbaya menyampaikan target penerimaan perpajakan 2027. Pemerintah membahas kebijakan pajak.",
        "post_2": "Purbaya menyampaikan target penerimaan perpajakan 2027. Pemerintah membahas anggaran negara.",
        "post_3": "Pemerintah membahas kebijakan pajak. Target penerimaan perpajakan disampaikan dalam artikel.",
        "post_4": "Anggaran negara ikut dibahas pemerintah. Kebijakan pajak menjadi bagian pembahasan.",
        "post_5": "Penerimaan perpajakan menjadi target pemerintah. Anggaran negara juga dibahas.",
        "post_6": "Pemerintah membahas target penerimaan perpajakan. Menurut lo, target atau anggaran?",
    }
    assert not any("Menkeu Purbaya" in issue for issue in pipeline._validate_proper_nouns(posts, body))


def test_concept_term_rejects_cost_to_price_shift():
    body = ("Pemerintah menargetkan menurunkan biaya kendaraan bagi rakyat. "
            "Program ini juga mengurangi konsumsi bahan bakar impor. " * 8)
    posts = {
        "post_1": "Ini buat turunin harga motor buat rakyat. Kabar bagus?",
        "post_2": "Pemerintah mau nurunin biaya kendaraan. Program ini mengurangi bahan bakar impor.",
        "post_3": "Targetnya nurunin biaya kendaraan buat rakyat.",
        "post_4": "Program ini juga mengurangi bahan bakar impor.",
        "post_5": "Biaya kendaraan dan bahan bakar impor jadi perhatian.",
        "post_6": "Menurut lo, bakal kejadian?",
    }
    issues = pipeline._validate_concept_terms(posts, body)
    # post_1 substitutes "harga motor" for "biaya kendaraan" -> flagged.
    assert any("post_1" in issue and "harga motor" in issue for issue in issues)
    # posts using the literal concept are clean.
    assert not any("post_2" in issue for issue in issues)
    assert not any("post_3" in issue for issue in issues)


def test_concept_term_rejects_bbm_substitution():
    body = ("Pemerintah mengurangi konsumsi bahan bakar impor dengan program kendaraan listrik. " * 8)
    posts = {
        "post_1": "Biar impor BBM turun, motor listrik didorong.",
        "post_2": "Pemerintah mengurangi konsumsi bahan bakar impor.",
    }
    issues = pipeline._validate_concept_terms(posts, body)
    # post_1 uses "BBM" for "bahan bakar impor" -> flagged.
    assert any("post_1" in issue and "BBM" in issue for issue in issues)
    # literal usage stays clean.
    assert not any("post_2" in issue for issue in issues)


def test_hormuz_story_requires_indonesia_energy_impact():
    title = "Selat Hormuz Terganggu, Harga Minyak Dunia Naik"
    body = ("Gangguan di Selat Hormuz mengerek harga minyak dunia dan memicu kekhawatiran pasar. " * 12)
    assert pipeline._indonesia_topic_relevance(title, body) == "international"

    connected = body + (" Indonesia berisiko menghadapi kenaikan biaya impor minyak dan tekanan harga BBM. "
                        "Dampaknya dapat terasa pada inflasi dan daya beli masyarakat.")
    assert pipeline._indonesia_topic_relevance(title, connected) == "global_indonesia_impact"
    assert pipeline._is_global_finance_story(title, connected)
    assert not pipeline._is_routine_market_story(title, connected)


def test_trump_economic_policy_requires_indonesia_trade_impact():
    title = "Trump Terapkan Tarif Baru, Perdagangan Global Tertekan"
    body = ("Donald Trump mengumumkan tarif baru untuk mitra dagang Amerika Serikat. " * 12)
    assert pipeline._indonesia_topic_relevance(title, body) == "international"

    connected = body + (" Kebijakan ini dapat mengubah akses ekspor Indonesia ke pasar Amerika dan menekan "
                        "investasi serta industri dalam negeri.")
    assert pipeline._indonesia_topic_relevance(title, connected) == "global_indonesia_impact"
    assert pipeline._is_global_finance_story(title, connected)
    assert pipeline._story_lane(title, connected) == "international_indonesia"
    assert pipeline._international_impact_channel(title, connected) == "trade"


def test_global_lane_needs_source_sentence_linking_event_to_indonesia():
    title = "Selat Hormuz Terganggu, Harga Minyak Dunia Naik"
    body = ("Selat Hormuz terganggu dan harga minyak dunia naik. " * 12
            + "Indonesia disebut dalam daftar negara peserta forum ekonomi.")
    assert pipeline._international_impact_channel(title, body) is None
    assert pipeline._story_lane(title, body) == "international"


def test_national_economy_story_accepts_domestic_public_actor():
    title = "Menteri Keuangan Tetapkan Anggaran Subsidi Energi"
    body = ("Menteri Keuangan menetapkan anggaran subsidi energi dan menjelaskan dampaknya bagi rumah tangga. " * 12)
    assert pipeline._indonesia_topic_relevance(title, body) == "national"


def test_non_material_digital_story_is_rejected():
    title = "Startup Indonesia Rilis Aplikasi Belanja Baru"
    body = ("Startup Indonesia meluncurkan aplikasi belanja baru dengan fitur yang lebih mudah. "
            "Layanan ini membantu pengguna menemukan produk dan menikmati pengalaman digital. " * 10)
    ok, reason = pipeline._is_eligible_candidate(title, body, "dailysocial")
    assert not ok
    assert reason == "source_title_not_material"


def test_source_title_gate_filters_digital_noise():
    assert not pipeline._has_source_title_signal("AWS Summit Jakarta 2026 Soroti Cloud", "dailysocial")
    assert not pipeline._has_source_title_signal("Startup Rilis Aplikasi Belanja Baru", "dailysocial")
    assert pipeline._has_source_title_signal("Startup Raih Pendanaan Seri B untuk Ekspansi", "dailysocial")


def test_source_title_gate_filters_non_economic_global_noise():
    assert not pipeline._has_source_title_signal("OpenAI Loses Revenue Chief", "cnbc_global")
    assert pipeline._has_source_title_signal("Fed Holds Interest Rates as Inflation Cools", "cnbc_global")
    assert pipeline._has_source_title_signal("Hormuz Disruption Pushes Oil Prices Higher", "cnbc_global")
    assert pipeline._has_source_title_signal("Trump Announces New Import Tariffs", "cnbc_global")


def test_material_digital_economy_story_is_allowed():
    title = "Startup Indonesia Raih Pendanaan Seri B US$50 Juta untuk Ekspansi"
    body = ("Startup Indonesia meraih pendanaan Seri B US$50 juta dari investor asing. "
            "Dana tersebut dipakai untuk ekspansi, membuka lapangan kerja, dan membangun pusat data. "
            "Investasi ini berdampak pada industri digital Indonesia. " * 8)
    ok, _ = pipeline._is_eligible_candidate(title, body, "dailysocial")
    assert ok


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


def test_source_fallback_rejects_ungrounded_generic_cta():
    body = " ".join([
        "Pemerintah menyampaikan perkembangan ekonomi terbaru kepada masyarakat.",
        "Kementerian menjelaskan proses pemantauan yang dilakukan setiap bulan.",
        "Data tersebut menjadi bahan evaluasi bagi pemerintah.",
        "Pembahasan lanjutan dilakukan bersama kementerian terkait.",
        "Pemerintah menyusun laporan berdasarkan informasi yang tersedia.",
        "Laporan itu akan dibahas dalam rapat berikutnya.",
        "Masyarakat diminta mengikuti perkembangan informasi resmi.",
        "Pemerintah memastikan proses berjalan sesuai ketentuan.",
        "Evaluasi dilakukan untuk memperbarui laporan berkala.",
        "Kementerian menyampaikan hasil pemantauan kepada pemerintah.",
        "Pembahasan masih berlangsung sesuai agenda yang telah disusun.",
        "Informasi baru akan disampaikan setelah rapat selesai.",
    ])
    assert pipeline._source_fallback_posts({"body": body, "pattern": "PROYEK"}) is None


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


def test_writer_prompt_requires_plain_words_for_general_readers():
    assert "kata sehari-hari" in pipeline.SYSTEM_PROMPT
    assert "pembaca awam" in pipeline.SYSTEM_PROMPT
    assert "jargon teknis" in pipeline.SYSTEM_PROMPT


def test_writer_prompt_requires_evidence_backed_cta_options():
    body = "Pemerintah mengubah subsidi energi. Rumah tangga menunggu aturan baru. " * 12
    prompt = pipeline.build_user_prompt({"body": body})
    assert "PILIHAN CTA BERBASIS BUKTI" in prompt
    assert "Jangan membuat pilihan CTA" in prompt
    assert "subsidi" in prompt
    assert "rumah tangga" in prompt.lower()


def test_writer_prompt_encodes_hanif_base_action_hook_calibration():
    prompt = pipeline.SYSTEM_PROMPT
    assert "HOOK & STRUKTUR — KALIBRASI AKSI" in prompt
    assert "masalah nyata yang pembaca alami" in prompt
    assert "janji konkret" in prompt
    assert "struktur aksi singkat" in prompt
    assert "pihak yang diuntungkan dan pihak yang menanggung biaya" in prompt
    assert "jangan menuduh motif bila tidak tertulis" in prompt


def test_writer_prompt_encodes_fellexandro_complement_skeptic_to_data():
    prompt = pipeline.SYSTEM_PROMPT
    assert "skeptis-ke-data" in prompt
    assert "sempat ngira X, ternyata data bilang Y" in prompt
    assert "ragu → cek angka → kesimpulan" in prompt
    assert "Angka konkret dulu, tafsir belakangan" in prompt
    assert "pernah ngerasain X?" in prompt


def test_revision_prompt_encodes_action_calibration_rules():
    assert "KALIBRASI AKSI" in pipeline.REVISION_PROMPT
    assert "SKEPTIS-KE-DATA" in pipeline.REVISION_PROMPT
    assert "PENUTUP" in pipeline.REVISION_PROMPT
    assert "Jangan menuduh motif institusi tanpa fakta tertulis" in pipeline.REVISION_PROMPT


def test_quality_gate_returns_true_on_clean_thread():
    """Regression: _quality_gate must return True for valid threads (bug: missing return True blocked ALL candidates)."""
    article = {
        "title": "Insentif Motor Listrik Bakal Diberikan September",
        "body": "Pemerintah memberikan insentif motor listrik pada September 2026. " * 12,
        "url": "https://finance.detik.com/industri/d-8624619/insentif-motor-listrik-bakal-diberikan-september",
        "source": "detikFinance",
    }
    posts = {
        "post_1": "September 2026 udah di depan mata, tapi insentif motor listrik masih ngantri PMK.",
        "post_2": "Insentifnya besar, tapi syaratnya juga gede. Perusahaan Indonesia harus bisa produksi 1 juta motor listrik di dalam negeri.",
        "post_3": "Kalau nggak? Nggak dapet duit negara. Presiden Prabowo bilang ini buat turunin harga motor buat rakyat.",
        "post_4": "Tapi, coba lo pikir 1 juta unit itu target yang realistis nggak buat industri lokal?",
        "post_5": "Insentif ini dibiayain dari APBN 2027. Artinya, uang negara yang dipakai.",
        "post_6": "Jadi, insentif motor listrik ini adil nggak buat rakyat? Menurut lo, ini kebijakan yang beneran buat rakyat, atau cuma janji manis?",
    }
    data = {"status": "success", "angle": "test"}
    assert pipeline._quality_gate(article, data, posts, []) is True
    assert "Jangan pura-pura ragu tanpa kontras di sumber" in pipeline.REVISION_PROMPT


def test_jargon_validator_is_advisory_for_conversational_voice():
    posts = {f"post_{i}": "" for i in range(1, 7)}
    posts["post_1"] = "Fundamental perusahaan terlihat kuat. Angkanya belum dijelaskan."
    issues = pipeline._validate_jargon(posts, "Fundamental perusahaan terlihat kuat. Perusahaan mencatat laba.")
    assert any("fundamental" in issue for issue in issues)
    assert not pipeline.deterministic_grounding_validate(
        {"body": "Fundamental perusahaan terlihat kuat. Perusahaan mencatat laba."}, posts
    )


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


def test_policy_winner_helper_is_advisory_only():
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
    issues = pipeline.deterministic_grounding_validate({"pattern": "KEBIJAKAN", "body": body}, posts)
    assert not any("policy trade-off" in issue for issue in issues)


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
    assert pipeline.article_evidence_gate({**article, "title": "Kebijakan subsidi pakan"}) is None
    assert pipeline._validate_policy_winner_arc(article, {}) == []


def test_policy_article_evidence_gate_only_applies_to_decision_story():
    base = " ".join(
        f"Dokumen pemerintah nomor {i} memuat rincian pelaksanaan dan pembagian kewenangan untuk rapat resmi."
        for i in range(1, 16)
    ) + " " + ("Catatan administrasi disimpan untuk pemeriksaan pihak terkait. " * 20)
    article = {"pattern": "KEBIJAKAN", "title": "Kebijakan subsidi", "body": base}
    assert pipeline.article_evidence_gate(article) is None

    article["body"] = base + " Pemerintah sebelumnya menyerahkan kewenangan guru ke daerah, tetapi kini mengusulkan pemindahan ke pusat."
    assert pipeline.article_evidence_gate(article) is None

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
    assert captured["max_tokens"] == 6000


def test_time_phrase_is_not_flagged_as_invented_name():
    posts = {f"post_{i}": "" for i in range(1, 7)}
    posts["post_4"] = "Sampai Juni, pemerintah masih membahas aturan ini. Status akhirnya belum ada."
    assert not any("Sampai Juni" in issue for issue in pipeline._validate_proper_nouns(
        posts, "Pemerintah masih membahas aturan ini sampai Juni."
    ))




def test_editorial_lens_is_repeatable_and_literal():
    assert pipeline._editorial_lens("Subsidi Rp10 triliun", "Anggaran negara membayar subsidi") == "siapa_yang_bayar"
    assert pipeline._editorial_lens("Harga beras naik", "Harga beras menekan konsumen") == "angka_ke_dompet"




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


def test_thread_contract_allows_repeated_s6_numbers_when_grounded():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_1"] = "Harga tercatat Rp36 ribu per kg. Angka ini berasal dari data resmi."
    posts["post_6"] = "Harga tercatat Rp36 ribu per kg. Menurut lo, apa solusinya?"
    assert not any("post_6: repeats numeric fact" in issue
                   for issue in pipeline.thread_contract_issues(posts, "https://x.test/a"))










def test_grounding_validation_does_not_spend_verifier_call(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("grounding must stay deterministic")

    monkeypatch.setattr(pipeline, "_call_llm", fail_if_called)
    issues = pipeline.grounding_validate(
        {"title": "T", "body": "Nilai mencapai Rp1 miliar."},
        {"post_1": "Nilai Rp1 miliar."},
    )
    assert issues == []


def test_unsupported_editorial_claims_block_unbacked_blame_and_loss():
    issues = pipeline._validate_unsupported_editorial_claims(
        {
            "post_1": "Yang rugi? APBN.",
            "post_2": "Kenapa baru sekarang ada tindakan?",
        },
        "Tujuh pekerja meninggal dalam kecelakaan tambang. Perusahaan menyampaikan belasungkawa.",
    )
    assert any("unsupported loss framing" in issue for issue in issues)
    assert any("unsupported timing/motive framing" in issue for issue in issues)


def test_duplicate_material_numbers_are_hard_quality_gate():
    posts = {f"post_{i}": "Fakta berbeda dari artikel. Bukti lain menambah konteks." for i in range(1, 7)}
    posts["post_1"] = "652 perusahaan akan dipangkas."
    posts["post_2"] = "Targetnya tinggal 652 perusahaan."
    posts["post_3"] = "Dari 652 perusahaan, sebagian akan bertahan."
    assert not pipeline._quality_gate({"body": "x"}, {"status": "success"}, posts, [])


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
    assert not any("quote not verbatim" in x for x in issues), issues
    assert any("unsupported evaluation" in x for x in issues), issues
    assert any("unsupported motive" in x for x in issues), issues
    assert any("unsupported consequence" in x for x in issues), issues


def test_writer_prompt_allows_compact_s1_hook():
    assert "S1 maksimal 220 karakter" in pipeline.SYSTEM_PROMPT
    assert "Jangan mulai dengan lead berita biasa" in pipeline.SYSTEM_PROMPT



def test_deterministic_validate_rejects_slide_without_sentence():
    complete = "Fakta sumber cukup panjang untuk memenuhi batas minimum setiap slide. Konteks sumber menambah rincian yang berbeda."
    posts = {f"post_{i}": complete for i in range(1, 7)}
    posts["post_1"] = "Fakta sumber cukup panjang untuk memenuhi batas minimum tetapi tanpa tanda baca kalimat"
    assert "post_1: no sentences" in pipeline.deterministic_validate(posts)
    posts["post_1"] = complete
    posts["post_6"] = "Fakta sumber cukup panjang untuk memenuhi batas minimum tetapi tanpa tanda baca kalimat"
    assert "post_6: no sentences" in pipeline.deterministic_validate(posts)


def test_padahal_is_conjunction_not_slop():
    posts = {f"post_{i}": "Fakta sumber cukup panjang dan lengkap untuk konteks. Bukti kedua menambah rincian." for i in range(1, 7)}
    posts["post_1"] = "Fakta sumber cukup panjang dan lengkap untuk konteks. Bukti sumber menambah konteks."
    posts["post_6"] = "Fakta sumber cukup panjang dan lengkap untuk konteks. Menurut lo?"
    posts["post_2"] = "Padahal fakta sumber cukup panjang dan lengkap untuk konteks. Bukti kedua menambah rincian."
    warnings = pipeline.deterministic_validate(posts)
    assert not any("slop 'padahal'" in item for item in warnings)


def test_quality_gate_blocks_revision_style_violation():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_1"] = "x" * 141
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_deterministic_validate_rejects_emoji_emote():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_1"] += " 🔥 :)"
    issues = pipeline.deterministic_validate(posts)
    assert any("emoji/emote" in issue for issue in issues), issues


def test_quality_gate_blocks_emoji_emote():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_1"] += " 🔥"
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])


def test_quality_gate_allows_grounded_s6_without_cta():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    assert pipeline._quality_gate({}, {"status": "success"}, posts, []) is True


def test_theoderick_accent_words_pass_quality_gate():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_2"] = "Ges, angka ini gokil bgt. Ndak ada yang ngira krn datanya dg skala besar."
    assert pipeline._quality_gate({}, {"status": "success"}, posts, []) is True
    issues = pipeline.deterministic_validate(posts)
    assert not any("emoji/emote" in issue for issue in issues), issues


def test_theoderick_reframe_does_not_relax_emoji_ban():
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk memenuhi batas minimum. Konteks sumber menambah rincian berbeda." for i in range(1, 7)}
    posts["post_3"] = "Reframe: yang penting bukan angkanya, tapi siapa yang nanggung. Ges 🙏"
    assert not pipeline._quality_gate({}, {"status": "success"}, posts, [])
    issues = pipeline.deterministic_validate(posts)
    assert any("emoji/emote" in issue for issue in issues), issues


def test_hook_allows_supported_policy_change_without_forced_number_or_contradiction():
    assert not pipeline.hook_issues("Pemerintah ubah aturan PPN minggu depan.", "Kebijakan berlaku 1 Agustus.")


def test_thread_contract_moves_source_url_to_s7():
    posts = {f"post_{i}": "Fakta sumber. Konteks sumber." for i in range(1, 7)}
    posts["post_6"] = "Takeaway. Apa yang perlu dipantau?"
    issues = pipeline.thread_contract_issues(posts, "https://contoh.go.id/dokumen")
    assert issues == [], issues
    assert posts["post_6"] == "Takeaway. Apa yang perlu dipantau?"
    assert posts["post_7"] == "Sumber: https://contoh.go.id/dokumen"


def test_thread_contract_requires_one_complete_sentence_and_allows_450_chars():
    posts = {f"post_{i}": "Fakta pertama. Konteks kedua." for i in range(1, 7)}
    assert pipeline.thread_contract_issues(posts, "") == []

    posts["post_2"] = "Fakta pertama."
    issues = pipeline.thread_contract_issues(posts, "")
    assert issues == [], issues

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
    assert pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 8)], "root_verified": {"media_type": "IMAGE", "permalink": "https://threads.test/p/1"}}, posts)


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


def test_unsupported_economic_relationships_are_hard_grounding_failures():
    article = {"body": "Startup lokal mendapat pendanaan dari investor asing. " * 8}
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_2"] = "Rp0 uang negara dipakai. Nama Indonesia di mata global dipertaruhkan."
    posts["post_3"] = "Startup lokal kalah sama perusahaan luar negeri."
    issues = pipeline.deterministic_grounding_validate(article, posts)
    assert any("unsupported economic relationship" in issue for issue in issues), issues


def test_payer_and_household_impact_overclaims_are_hard_grounding_failures():
    article = {"body": "Purbaya mengatakan pajak baru dipertimbangkan jika ekonomi tumbuh 6 persen dan daya beli masyarakat stabil. " * 8}
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    posts["post_1"] = "Kalau 6 persen terus, bikin lo bayar lebih dan dompet lo jadi sasaran."
    posts["post_2"] = "Pajak baru buat isi kas negara, atau negara cari duit lain mungkin utang."
    issues = pipeline.deterministic_grounding_validate(article, posts)
    assert any("unsupported payer framing" in issue for issue in issues), issues
    assert any("unsupported fiscal purpose" in issue for issue in issues), issues
    assert any("unsupported audience impact" in issue for issue in issues), issues
    assert any("unsupported fiscal alternative" in issue for issue in issues), issues


def test_prompt_guides_empathetic_opinion_without_tightening_filter():
    assert "OPINI BERPIHAK — BOLEH, TAPI JANGAN MENGARANG" in pipeline.SYSTEM_PROMPT
    assert "berpihak ke rakyat kecil" in pipeline.SYSTEM_PROMPT
    assert "Menurut lo, ini adil atau berpihak ke siapa?" in pipeline.SYSTEM_PROMPT


def test_audience_lens_requires_grounded_first_person_opinion_and_group():
    article = {"body": "Pemerintah menyiapkan anggaran untuk daerah dengan PAD kecil. "}
    posts = {f"post_{i}": "Fakta sumber daerah." for i in range(1, 7)}
    posts["post_2"] = "Menurut gua, daerah dengan PAD kecil perlu disebut jelas. Fakta sumber daerah."
    assert pipeline._validate_audience_lens(article, posts) == []


def test_audience_lens_rejects_missing_first_person_and_group():
    article = {"body": "Pemerintah menyiapkan anggaran untuk daerah dengan PAD kecil. "}
    posts = {f"post_{i}": "Fakta sumber." for i in range(1, 7)}
    issues = pipeline._validate_audience_lens(article, posts)
    assert "voice: missing first-person editorial opinion" in issues
    assert "audience lens: S2-S5 missing source-backed affected group" in issues


def test_audience_lens_rejects_unsupported_blame_framing():
    article = {"body": "Pemerintah menyiapkan transfer ke daerah. "}
    posts = {f"post_{i}": "Fakta sumber daerah." for i in range(1, 7)}
    posts["post_2"] = "Menurut gua, daerah nggak bisa cari duit sendiri. Fakta sumber daerah."
    issues = pipeline._validate_audience_lens(article, posts)
    assert any("unsupported blame framing" in issue for issue in issues)


def test_audience_lens_does_not_apply_without_named_public_group():
    article = {"body": "Perusahaan mengumumkan perubahan strategi bisnis. "}
    posts = {f"post_{i}": "Fakta sumber bisnis." for i in range(1, 7)}
    assert pipeline._validate_audience_lens(article, posts) == []


def test_audience_lens_is_advisory_not_quality_blocker():
    article = {"body": "Pemerintah menyiapkan anggaran untuk daerah dengan PAD kecil. " * 8}
    posts = {f"post_{i}": "Fakta sumber daerah menambah konteks kebijakan. Fakta kedua berbeda." for i in range(1, 7)}
    posts["post_6"] = "Fakta sumber daerah menambah konteks kebijakan. Menurut lo, anggaran atau aturan?"
    warnings = []
    assert pipeline._quality_gate(article, {"status": "success"}, posts, warnings)


def test_publish_completion_rejects_partial_chain():
    posts = {f"post_{i}": "x" for i in range(1, 8)}
    assert not pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 7)], "error": "post_7 failed"}, posts)
    assert pipeline._publish_complete({"post_ids": [str(i) for i in range(1, 8)], "root_verified": {"media_type": "IMAGE", "permalink": "https://threads.test/p/1"}}, posts)


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
    assert "gw–lo" in pipeline.SYSTEM_PROMPT
    assert "Jangan menambah dampak, profesi, angka, skenario, penilaian" in pipeline.SYSTEM_PROMPT
    assert "Buka dengan fakta paling mahal dan fakta paling kuat" in pipeline.SYSTEM_PROMPT
    assert "buat kalimat pertama menyampaikan fakta" in pipeline.SYSTEM_PROMPT.lower()
    assert "jangan ulang angka, fakta, atau contoh" in pipeline.SYSTEM_PROMPT
    assert "Jika tidak ada pilihan atau benturan konkret, tutup dengan simpulan editorial" in pipeline.SYSTEM_PROMPT
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


def test_routine_market_story_without_public_decision_stays_rejected():
    title = "Rupiah Pagi Melemah Tipis ke Rp17.878"
    body = ("Rupiah melemah karena sentimen pasar global dan pelaku pasar mencermati arah dolar AS. " * 20)
    assert pipeline._is_routine_market_story(title, body) is True


def test_editorial_priority_prefers_policy_and_public_money_over_market_update():
    policy = "Pemerintah mengubah aturan subsidi dan anggaran untuk rumah tangga. " * 12
    market = "Rupiah melemah karena sentimen pasar global. " * 12
    assert pipeline._engagement_priority_bonus("Aturan subsidi diubah", policy) > pipeline._engagement_priority_bonus("Rupiah melemah", market)


def test_s1_hook_has_no_extra_template_gate():
    body = "Pemerintah mengubah subsidi energi. Perubahan itu menaikkan biaya rumah tangga. " * 12
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_1"] = "Pemerintah membahas ekonomi. Informasi lain masih tersedia."
    assert pipeline._validate_s1_hook(posts, body) == []


def test_s1_hook_is_not_required_for_generic_economy_article():
    article = {
        "title": "Bank Catat Laba dan Kredit Tumbuh",
        "body": "Bank mencatat laba Rp4 triliun dan kredit tumbuh 6 persen. " * 20,
        "pattern": "PROYEK",
    }
    posts = {f"post_{i}": "Fakta sumber cukup panjang. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    assert pipeline._validate_s1_hook(posts, article["body"], article) == []


def test_s1_hook_is_not_a_hard_gate_for_policy_decision_article():
    article = {
        "title": "Pemerintah Usulkan Perubahan Subsidi",
        "body": ("Pemerintah mengusulkan perubahan subsidi energi. "
                 "Aturan sebelumnya berlaku untuk rumah tangga. "
                 "Perubahan ini menambah biaya anggaran dan manfaat penerima. " * 12),
        "pattern": "KEBIJAKAN",
    }
    posts = {f"post_{i}": "Fakta sumber cukup panjang. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    assert pipeline._validate_s1_hook(posts, article["body"], article) == []


def test_s6_accepts_one_source_anchored_cta():
    body = "Pemerintah mengubah anggaran subsidi energi. Pembahasan masih menunggu persetujuan DPR. " * 12
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_6"] = "Pembahasan masih menunggu persetujuan DPR. Menurut lo, bagian mana yang perlu dijelaskan?"
    issues = pipeline._validate_s6_cta(posts, body)
    assert issues == [], issues


def test_s6_allows_simple_source_anchored_cta():
    body = "Penerimaan pajak tumbuh 2,4 persen. Pemerintah memantau APBN setiap bulan. " * 12
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_6"] = "Penerimaan pajak tumbuh 2,4 persen. Menurut lo, pertumbuhan atau ekonomi?"
    issues = pipeline._validate_s6_cta(posts, body)
    assert issues == [], issues


def test_s6_allows_grounded_editorial_close_without_cta():
    body = "Pemerintah mengubah anggaran subsidi energi. Pembahasan masih menunggu persetujuan DPR. " * 12
    posts = {f"post_{i}": "Fakta sumber cukup panjang untuk validasi. Kalimat kedua menjelaskan konteks." for i in range(1, 7)}
    posts["post_6"] = "Pembahasan masih menunggu persetujuan DPR. Keputusan akhirnya belum keluar."
    assert pipeline._validate_s6_cta(posts, body) == []


def test_source_diversity_penalizes_recently_overused_source():
    data = {"topics": [{"article_source": "detik_finance"}] * 4}
    assert pipeline._source_diversity_penalty(data, "detik_finance") < pipeline._source_diversity_penalty(data, "antara_ekonomi")


def test_utility_and_ceremony_titles_can_enter_editorial_routing():
    body = "Bank Indonesia menjelaskan aturan dengan nilai Rp1.000.000. " * 30
    for title in (
        "Saldo Minimal Nasabah Prioritas Bank BRI per Agustus 2026",
        "Semangat Koperasi Berhembus di Festival Lembah Baliem",
        "Cara Daftar Magang Kemenkeu 2026",
    ):
        assert pipeline._is_eligible_candidate(title, body, "cnbc_market")[0] is False


def test_keyword_fallback_can_approve_body_verified_article_without_pattern():
    body = "Koperasi, ekonomi, anggaran, pajak, subsidi, investasi, pemerintah, masyarakat, UMKM, dan Indonesia menjadi perhatian. " * 30
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


def test_hot_topic_allows_same_issue_with_different_numbers(monkeypatch):
    now = 1_800_000_000
    articles = [
        {"title": f"Kebijakan APBN seri {i} Rp{i} triliun", "url": f"https://a.test/{i}", "source": "antara_ekonomi", "ts": now - i}
        for i in range(3)
    ]
    body = "Pemerintah Indonesia menetapkan kebijakan APBN senilai Rp9 triliun untuk penerimaan negara. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda _url: (body, None, now - 60))

    primary = pipeline.scout_hot_topics(articles, now=now, limit=15, per_source_limit=2)
    fallback = pipeline.scout_hot_topics(articles, now=now, limit=15, per_source_limit=6, allow_cluster_repeats=True)

    assert len(primary) == 2
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

    assert pipeline.HOT_TOPIC_LIMIT == 10
    assert len(topics) <= 10
    assert len({topic["cluster"] for topic in topics}) == len(topics)


def test_ranked_candidate_pool_uses_next_rank_when_top_is_posted():
    articles = [
        {"url": "https://example.test/1?utm_source=x", "title": "Pajak APBN Top 1", "source": "cnn_ekonomi", "ts": 1_800_000_000},
        {"url": "https://example.test/2", "title": "Pajak APBN Top 2", "source": "cnn_ekonomi", "ts": 1_800_000_000},
        {"url": "https://example.test/3", "title": "Pajak APBN Top 3", "source": "cnn_ekonomi", "ts": 1_800_000_000},
    ]
    topics = [
        {"canonical_url": "https://example.test/1"},
        {"canonical_url": "https://example.test/2"},
        {"canonical_url": "https://example.test/3"},
    ]
    pool = pipeline._ranked_candidate_pool(articles, topics, limit=3)
    picked = pipeline._pick_article(
        pool,
        {"https://example.test/1"},
        ranked_urls=[article["url"] for article in pool],
    )
    assert picked["url"] == "https://example.test/2"


def test_candidate_selection_excludes_persisted_posted_urls():
    articles = [
        {"url": "https://example.test/repeat", "title": "Pajak APBN Repeat", "source": "cnn_ekonomi", "ts": 1_800_000_000},
        {"url": "https://example.test/fresh", "title": "Pajak APBN Fresh", "source": "cnn_ekonomi", "ts": 1_800_000_000},
    ]
    picked = pipeline._pick_article(
        articles,
        {"https://example.test/repeat?utm_source=rss"},
        ranked_urls=[article["url"] for article in articles],
    )
    assert picked["url"] == "https://example.test/fresh"


def test_exact_posted_candidate_accounting_uses_canonical_urls():
    urls = [
        "https://example.test/repeat?utm_source=rss",
        "https://example.test/fresh",
    ]
    assert pipeline._count_exact_posted_candidates(
        urls, {"https://example.test/repeat"}
    ) == 1


def test_posted_canonical_urls_reads_article_and_source_urls():
    data = {"topics": [{
        "article_url": "https://example.test/story?utm_source=rss",
        "slides": {"post_7": "Sumber: https://example.test/other?utm_campaign=x"},
    }]}
    assert pipeline.posted_canonical_urls(data) == {
        "https://example.test/story", "https://example.test/other"
    }


def test_duplicate_guard_matches_all_ledger_url_fields():
    data = {"topics": [{
        "canonical_url": "https://example.test/story",
    }]}
    assert pipeline.duplicate_ledger_match(data, "https://example.test/story?utm_medium=rss") == "https://example.test/story"


def test_rss_thumbnail_fallback_survives_empty_media_content(monkeypatch):
    xml = (
        '<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>'
        '<title>Pertumbuhan ekonomi nasional melambat</title><link>https://example.test/story</link>'
        '<pubDate>Mon, 17 Aug 2026 12:00:00 +0000</pubDate>'
        '<media:content/><media:thumbnail url="https://example.test/thumb.jpg"/>'
        '</item></channel></rss>'
    )
    monkeypatch.setattr(pipeline, "_http_get", lambda *_: (200, xml))
    rows = pipeline._scrape_rss("https://example.test/feed", "test", 1)
    assert rows[0]["og_image"] == "https://example.test/thumb.jpg"


def test_discovery_hot_score_prefers_editorially_valid_story(monkeypatch):
    now = 1_800_000_000
    articles = [
        {"title": "Investor Hadiri Forum Bisnis dan Kenalkan Produk Baru", "url": "https://example.test/noise", "source": "cnn_ekonomi", "ts": now - 60},
        {"title": "Pemerintah Tetapkan APBN Rp11 Triliun untuk Belanja Negara", "url": "https://example.test/policy", "source": "antara_ekonomi", "ts": now - 120},
    ]
    body = "Pemerintah Indonesia menetapkan kebijakan APBN senilai Rp11 triliun untuk belanja negara dan dampaknya bagi masyarakat. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda url: (body, None, now - 60))
    monkeypatch.setattr(pipeline, "_is_eligible_candidate", lambda title, body, source: (
        ("Pemerintah" in title, "test_gate") if "Pemerintah" in title else (False, "low_value_corporate_story")
    ))

    topics = pipeline.scout_hot_topics(articles, now=now, limit=2, per_source_limit=2)

    assert topics[0]["canonical_url"] == "https://example.test/policy"


def test_discovery_pool_keeps_later_editorially_eligible_candidate():
    articles = [
        {"url": f"https://example.test/{i}", "title": f"Rupiah dan APBN seri {i}",
         "source": "cnn_ekonomi", "ts": 1_800_000_000}
        for i in range(1, 12)
    ]
    articles[-1]["title"] = "Pemerintah Tetapkan APBN Rp11 Triliun"
    topics = [
        {"canonical_url": article["url"], "_body": (
            "Pemerintah Indonesia membahas layanan publik dan kegiatan masyarakat. " * 12
            if i < 11 else
            "Pemerintah Indonesia menetapkan kebijakan APBN senilai Rp11 triliun untuk anggaran negara. " * 12
        )}
        for i, article in enumerate(articles, 1)
    ]
    pool = pipeline._ranked_candidate_pool(articles, topics, limit=15)

    assert len(pool) == 11
    assert pipeline._is_eligible_candidate(
        pool[-1]["title"], pool[-1]["body"], pool[-1]["source"]
    )[0] is True


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




def test_runtime_has_one_active_system_prompt():
    source = Path(__file__).with_name("pipeline-v3.py").read_text()
    assert source.count("SYSTEM_PROMPT = \"\"\"") == 1


def test_writer_prompt_contains_claim_map_and_grounding_contract():
    body = "Pemerintah menetapkan subsidi energi senilai Rp1 triliun. " * 12
    prompt = pipeline.build_user_prompt({"body": body})
    assert "CLAIM MAP S1-S6" in prompt
    assert "Jangan menambah klaim di luar CLAIM MAP" in prompt
    assert "Jangan membuat fakta baru" in prompt


def test_writer_prompt_encodes_high_signal_creator_voice_without_weakening_grounding():
    assert "VOICE CONTRACT — TECHBRO" in pipeline.SYSTEM_PROMPT
    assert "Satu post satu pukulan" in pipeline.SYSTEM_PROMPT
    assert "status gap" in pipeline.SYSTEM_PROMPT
    assert "Gaya tajam bukan izin untuk mengarang dampak" in pipeline.SYSTEM_PROMPT
    assert "semua klaim tetap literal dari artikel" in pipeline.SYSTEM_PROMPT


def test_writer_prompt_uses_conversational_personal_story_pattern_without_fabrication():
    prompt = pipeline.SYSTEM_PROMPT
    assert "conversational, tajam, konkret" in prompt
    assert "Kalibrasi referensi positif" in prompt
    assert "belokan conversational" in prompt
    assert "judgment kecil yang terasa personal" in prompt
    assert "Jangan mulai dengan konteks panjang atau ringkasan headline" in prompt
    assert "Orang pertama hanya untuk opini editorial" in prompt
    assert "Jangan memaksa lo/gue di setiap slide" in prompt
    assert "masalah nyata di artikel" in prompt
    assert "CTA promosi" not in prompt
    assert "Jangan menyalin frase referensi" in prompt


def test_writer_prompt_encodes_winning_contradiction_escalation_and_low_friction_cta():
    prompt = pipeline.SYSTEM_PROMPT
    assert "dua fakta literal yang saling menekan" in prompt
    assert "jangan cuma melaporkan perubahan satu angka" in prompt
    assert "menaikkan tensi dengan bukti baru" in prompt
    assert "Jangan mengulang premis dengan sinonim" in prompt
    assert "jadikan keputusan aktor itu objek penilaian" in prompt
    assert "satu sumbu judgment yang gampang dijawab" in prompt
    assert "Jangan mengubah CTA menjadi soal ujian kebijakan" in prompt

    runtime_prompt = pipeline.build_user_prompt({"body": "Ekonomi tumbuh, tetapi konsumsi rumah tangga turun. Pemerintah memangkas belanja." * 20})
    assert "PROGRESI: tiap slide menaikkan tensi dengan bukti baru" in runtime_prompt
    assert "CTA: minta satu judgment sederhana" in runtime_prompt


def test_literal_entity_prompt_forbids_new_name_phrases():
    prompt = pipeline.build_user_prompt({"body": "The Fed menahan suku bunga."})
    assert "dilarang membuat frasa nama baru" in prompt


def test_topic_entities_extract_named_economy_entities():
    entities = pipeline._topic_entities("Purbaya Bahas Beban APBD Guru PPPK di Bawah Danantara")
    assert {"purbaya", "pppk", "danantara"} <= entities


def test_discovery_rejects_non_economic_geopolitical_story():
    body = "Perang menyebabkan pabrik dan infrastruktur rusak. " * 30
    assert pipeline._is_eligible_candidate(
        "Perang Rusia dan Ukraina Memanas, Pabrik Baja-Gandum Jadi Sasaran", body, "cnbc_market"
    ) == (False, "non_economic_geopolitical_story")


def test_discovery_rejects_routine_product_announcement():
    body = "Bank meluncurkan kartu kredit baru untuk transaksi nasabah. " * 30
    assert pipeline._is_eligible_candidate(
        "BI Luncurkan Kartu Kredit Indonesia, Bisa Digunakan Transaksi QRIS", body, "cnn_ekonomi"
    ) == (False, "routine_product_announcement")


def test_discovery_rejects_stock_picks():
    body = "Analis merekomendasikan saham untuk pendapatan dividen. " * 30
    assert pipeline._is_eligible_candidate(
        "Top Wall Street Analysts Like These 3 Dividend Stocks for Steady Income", body, "cnbc_global"
    ) == (False, "investment_advice")


def test_discovery_keeps_public_finance_story():
    body = "Pemerintah menganggarkan pembayaran bunga utang dan menjelaskan rinciannya untuk ekonomi Indonesia. " * 30
    ok, reason = pipeline._is_eligible_candidate(
        "Pemerintah RI Bayar Bunga Utang Rp650,3 Triliun pada 2027", body, "cnbc_market"
    )
    assert ok, reason


def test_fresh_rss_timestamp_is_bounded_fallback():
    now = datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc).timestamp()
    ts, source, reason = pipeline._resolve_published_timestamp(0, now - 60, now)
    assert ts == now - 60
    assert source == "rss_fallback"
    assert reason == "ok"


def test_stale_article_timestamp_does_not_use_rss_fallback():
    now = datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc).timestamp()
    ts, source, reason = pipeline._resolve_published_timestamp(now - 90000, now - 60, now)
    assert ts == 0
    assert source == "article"
    assert reason == "stale"


def test_topic_cohort_separates_explicit_current_from_legacy():
    assert pipeline.topic_cohort({"cohort": pipeline.CURRENT_COHORT}) == pipeline.CURRENT_COHORT
    assert pipeline.topic_cohort({"title": "old"}) == pipeline.LEGACY_COHORT








def test_hot_topic_scout_accepts_global_story_without_indonesia_connection(monkeypatch):
    now = 1_800_000_000
    article = {"title": "The Fed Naikkan Suku Bunga, Pasar Global Bergejolak", "url": "https://global.test/1", "source": "cnn_global", "ts": now - 60}
    body = "Federal Reserve menaikkan suku bunga dan pasar global bereaksi terhadap inflasi Amerika Serikat. " * 12
    monkeypatch.setattr(pipeline, "_fetch_article_body", lambda _url: (body, None, now - 60))

    topics = pipeline.scout_hot_topics([article], now=now)
    assert topics and topics[0]["indonesia_relevance"] == "international"


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
    assert "cnbc_global" in pipeline.SOURCES
    assert "bbc_business" in pipeline.SOURCES
    assert "tempo_bisnis" in pipeline.SOURCES
    assert "republika_ekonomi" in pipeline.SOURCES


def test_cnbc_body_selector_reads_generated_article_body(monkeypatch):
    html = ('<html><article class="ArticleBody-articleBody"><p>'
            + ('Revenue rose after a major investment decision. ' * 20)
            + '</p></article></html>')
    monkeypatch.setattr(pipeline, "_http_get", lambda *_args, **_kwargs: (200, html))
    pipeline._BODY_CACHE.pop("https://www.cnbc.com/test-body", None)

    body, _, _ = pipeline._fetch_article_body("https://www.cnbc.com/test-body")

    assert len(body) > 500


def test_jsonld_article_body_selector_reads_publisher_body(monkeypatch):
    body_text = "Anggaran pemerintah berubah setelah keputusan resmi. " * 20
    html = ('<html><head><script type="application/ld+json">'
            + json.dumps({"@type": "NewsArticle", "articleBody": body_text,
                          "datePublished": "2026-08-17T10:30:00+07:00"})
            + '</script></head><body><div id="unrelated">menu</div></body></html>')
    monkeypatch.setattr(pipeline, "_http_get", lambda *_args, **_kwargs: (200, html))
    pipeline._BODY_CACHE.pop("https://example.test/jsonld-body", None)

    body, _, published_ts = pipeline._fetch_article_body("https://example.test/jsonld-body")

    assert len(body) > 500
    assert published_ts > 0


def test_economic_foreign_story_has_no_indonesia_anchor_penalty():
    article = {"title": "American stocks rally after company revenue jump", "url": "https://x", "ts": 0}
    global_score = pipeline._score_article({**article, "source": "cnbc_global"})[0]
    local_score = pipeline._score_article({**article, "source": "bbc_business"})[0]

    assert global_score == local_score




def test_source_config_invalid_json_falls_back_to_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "SOURCES_FILE", tmp_path / "sources.json")
    pipeline.SOURCES_FILE.write_text("not json")
    assert pipeline.load_sources() == {}


def test_katadata_rss_is_admitted_as_economy_source():
    cfg = pipeline.SOURCES["katadata_ekonomi"]
    assert cfg["url"] == "https://katadata.co.id/rss"
    assert cfg["type"] == "rss"
    assert pipeline.SOURCE_TIERS["katadata_ekonomi"][1] == 8


def test_short_keyword_matching_does_not_match_inside_another_word():
    assert not pipeline._matches_keyword("kemasan makanan", "emas")
    assert pipeline._matches_keyword("harga emas naik", "emas")


def test_reject_keyword_does_not_match_inside_longer_word():
    assert not pipeline._matches_keyword("Partisipasi Pembuatan Kebijakan", "artis")
    assert pipeline._matches_keyword("Promo Bank", "promo")


def test_eligibility_reapplies_hard_reject_before_llm():
    body = "Kegiatan ekonomi dan kebijakan pemerintah dibahas dalam laporan ini. " * 20
    ok, reason = pipeline._is_eligible_candidate(
        "Acara Ekonomi Pemerintah", body, "katadata_ekonomi"
    )
    assert not ok
    assert reason == "hard_reject:acara"


def test_html_scraper_resolves_relative_links(monkeypatch):
    monkeypatch.setattr(pipeline, "_http_get", lambda *_: (200, '<a href="/rilis/ekonomi">Rilis ekonomi terbaru yang relevan</a>'))
    articles = pipeline._scrape_html("https://example.go.id/siaran-pers", "official", 10, "example.go.id/")
    assert articles[0]["url"] == "https://example.go.id/rilis/ekonomi"




def test_hook_metadata_is_deterministic_and_not_market_default():
    title = "Danantara Targetkan 4 BUMN IPO, Ada Pegadaian-Pelindo"
    body = "Danantara menargetkan empat BUMN melakukan IPO dalam 6-12 bulan."
    pattern, arc, hook = pipeline._content_metadata(title, body)
    assert pattern == "PASAR"
    assert arc != "market_shock"
    assert hook in {"number_shock", "decision_impact", "wallet_impact", "named_decision"}




def test_score_rewards_concrete_public_impact():
    routine = {"title": "Rupiah Menguat Tajam Hari Ini", "url": "https://x.test/a"}
    concrete = {"title": "Prabowo Tetapkan Subsidi Rp80 Triliun, Beban APBN Berubah", "url": "https://x.test/b"}
    assert pipeline._score_article(concrete)[0] > pipeline._score_article(routine)[0]


def test_score_demotes_explainer_headlines():
    real_news = {"title": "Prabowo Naikkan Tunjangan Guru Rp50 Triliun", "url": "https://x.test/a"}
    explainer = {"title": "Kenali Tips Investasi Emas yang Aman", "url": "https://x.test/b"}
    # explainer has weak economy signal (signals < 2) -> demoted below real policy news
    assert pipeline._score_article(real_news)[0] > pipeline._score_article(explainer)[0]


def test_score_boosts_number_shock_and_wallet_impact():
    shock = {"title": "Harga Beras Tembus Rp18.000 per Kg, Daya Beli Tertekan", "url": "https://x.test/a"}
    plain = {"title": "Rupiah Bergerak Hari Ini", "url": "https://x.test/b"}
    assert pipeline._score_article(shock)[0] > pipeline._score_article(plain)[0]


def test_number_grounding_allows_source_decimal_rounding():
    body = "Total pembiayaan UMKM mencapai Rp 1.948,72 triliun. Kredit bank Rp 1.519,35 triliun."
    posts = {
        "post_1": "Pembiayaan mencapai Rp 1.948 triliun.",
        "post_2": "Kredit bank mencapai Rp 1.519 triliun.",
    }
    assert pipeline._validate_numbers(posts, body) == []


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
# ── Performance feedback loop & duplicate guards ────────────────────────────

def test_duplicate_title_match_blocks_similar_within_24h():
    from datetime import timedelta
    recent = (datetime.now(pipeline.WIB) - timedelta(hours=2)).isoformat()
    data = {"topics": [{
        "title": "Purbaya Bakal Tarik Pajak Baru Tahun Depan, tapi Tergantung Ini",
        "timestamp": recent,
    }]}
    similar = "Purbaya Bakal Tarik Pajak Baru Tahun Depan tapi Tergantung Kondisi"
    assert pipeline.duplicate_title_match(data, similar)


def test_duplicate_title_match_allows_different_topic():
    data = {"topics": [{
        "title": "Prabowo Kebut Swasembada Pangan Siapkan Rp195 T",
        "timestamp": "2026-08-17T15:04:25+07:00",
    }]}
    assert pipeline.duplicate_title_match(data, "IHSG Ditutup Menguat Hari Ini") is None


def test_duplicate_title_match_ignores_old_rows():
    data = {"topics": [{
        "title": "Purbaya Bakal Tarik Pajak Baru Tahun Depan",
        "timestamp": "2026-08-01T15:04:25+07:00",
    }]}
    assert pipeline.duplicate_title_match(data, "Purbaya Bakal Tarik Pajak Baru Tahun Depan") is None


def test_jaccard_zero_on_disjoint_tokens():
    assert pipeline._jaccard(["pajak"], ["subsidi"]) == 0.0
    assert pipeline._jaccard([], ["subsidi"]) == 0.0


def test_performance_medians_empty_when_no_views():
    data = {"topics": [{"pattern": "PASAR", "lane": "national", "views": None}]}
    stats = pipeline.performance_medians(data)
    assert stats == {"pattern_avg": {}, "lane_avg": {}}


def test_performance_medians_computes_medians():
    data = {"topics": [
        {"pattern": "KEBIJAKAN", "lane": "national", "views": 100},
        {"pattern": "KEBIJAKAN", "lane": "national", "views": 300},
        {"pattern": "KEBIJAKAN", "lane": "national", "views": 500},
        {"pattern": "PASAR", "lane": "international", "views": 40},
    ]}
    stats = pipeline.performance_medians(data)
    assert stats["pattern_avg"]["KEBIJAKAN"] == 300
    assert stats["pattern_avg"]["PASAR"] == 40
    assert stats["lane_avg"]["national"] == 300
    assert stats["lane_avg"]["international"] == 40


def test_performance_bias_bounded_and_zero_without_stats():
    assert pipeline._performance_bias({}, {}) == 0
    stats = {"pattern_avg": {"KEBIJAKAN": 40000}, "lane_avg": {"national": 20000}}
    bias = pipeline._performance_bias({"pattern": "KEBIJAKAN", "lane": "national"}, stats)
    assert 0 <= bias <= 10  # capped, never negative for strong performers


def test_remaining_candidates_excludes_posted_urls_when_data_given():
    candidates = [{"url": "https://example.com/1"}, {"url": "https://example.com/2"}]
    data = {"topics": [{"article_url": "https://example.com/1"}]}
    remaining = pipeline._remaining_eligible_candidates(candidates, "https://example.com/x", data)
    assert [c["url"] for c in remaining] == ["https://example.com/2"]


def test_sync_ledger_metrics_skips_rows_without_posted_timestamp(monkeypatch):
    monkeypatch.setattr(pipeline, "THREADS_TOKEN", "test-token")
    called = []
    monkeypatch.setattr(pipeline, "_fetch_engagement_metrics",
                        lambda pid: called.append(pid) or {"views": 100, "likes": 2,
                                                           "replies": 1, "reposts": 0, "quotes": 0})
    monkeypatch.setattr(pipeline, "save_data", lambda *a, **k: None)
    data = {"topics": [
        {"post_id": "111", "views": None, "posted": "2026-07-21T10:00:00+07:00",
         "timestamp": "2026-07-21T10:00:00+07:00"},  # legit: has posted -> fetch
        {"post_id": "222", "views": None, "timestamp": "2026-07-21T10:00:00+07:00"},  # legacy: no posted -> skip
        {"post_id": "333", "views": None, "posted": "2026-07-22T10:00:00+07:00",
         "timestamp": "2026-07-22T10:00:00+07:00"},  # legit
        {"post_id": "444", "views": 50, "posted": "2026-07-23T10:00:00+07:00"},  # already has views -> skip
    ]}
    updated, fetched_total, failed = pipeline.sync_ledger_metrics(data, max_fetch=40)
    assert called == ["333", "111"]  # newest-first; only rows with posted + null views
    assert fetched_total == 2
    assert updated == 2
    assert failed == 0
    assert data["topics"][0]["views"] == 100
    assert data["topics"][1].get("views") is None  # legacy row untouched, no API call burned
