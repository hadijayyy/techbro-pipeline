#!/usr/bin/env python3
"""Manual regression checks for Techbro v3 scoring & validation rules."""
__test__ = False
import importlib.util
import sys
from pathlib import Path

sys.argv = ["pipeline-v3.py", "--dry-run"]
spec = importlib.util.spec_from_file_location("techbro_v3", Path(__file__).parent.parent / "pipeline-v3.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def score(title, description=""):
    return m._score_article({"title": title, "description": description})[0]


# Economy keyword should get positive score
assert score("Harga Pangan Naik, Daya Beli Tertekan") > 0, "harga pangan should score"

# Number bonus boosts score (Rp10 Triliun with IGNORECASE)
assert score("Bank Catat Laba Rp10 Triliun Kuartal Ini") > 0, "Rp10T should get bonus"
assert score("Bank Catat Laba Rp10 Triliun Kuartal Ini") < 45, "should stay under 45"

# Combined economy + entity boost
assert score("BI Naikkan Suku Bunga, Cicilan Makin Berat") > 0, "BI + cicilan should score"

# Hard reject — zero
assert score("Berita Olahraga Hari Ini") == 0, "hard_reject"

# Out-of-scope non-economic topic must fail closed.
assert score("Gempa Guncang Jakarta") == 0, "out_of_scope"

# Clean posts pass deterministic validation
posts = {
    "post_1": "Minyak Rp100 ribu per barel menekan ongkos impor Indonesia, tapi harga BBM belum tentu langsung naik.",
    "post_2": "Harga minyak memengaruhi biaya energi dan pengiriman bagi perusahaan yang memakai solar. Stok lama bisa menahan dampaknya sementara.",
    "post_3": "Dampaknya ke harga barang bergantung pada stok, kurs, dan keputusan tiap penjual. Tidak semua harga langsung berubah.",
    "post_4": "Usaha yang banyak memakai logistik perlu memantau biaya kirim dan kontrak pembelian berikutnya. Solar ikut menentukan ongkosnya.",
    "post_5": "Cek porsi ongkir dan solar dalam biaya usaha sebelum mengubah harga ke pelanggan. Margin perlu dihitung ulang.",
    "post_6": "Biaya harian lu yang paling cepat terasa saat harga energi naik apa?",
}
assert not m.deterministic_validate(posts), "clean posts should pass"

# Slop detection
assert any("slop" in w for w in m.deterministic_validate({"post_1": "tau gak sih kalo gini?", "post_2": "a", "post_3": "a", "post_4": "a", "post_5": "a", "post_6": "a"})), "slop"

# Techbro keeps its conversational second-person voice; no forced normalization.
assert "anda harus tahu ini." == m._convert_pov("anda harus tahu ini.")

# Empty post detection
assert any("empty" in w for w in m.deterministic_validate({"post_1": "", "post_2": "a", "post_3": "a", "post_4": "a", "post_5": "a", "post_6": "a"})), "empty"

# No contradictory data rules in system prompt
assert "Jangan hitung, konversi, atau menyimpulkan dampak baru" not in m.SYSTEM_PROMPT

print("PASS economy-worker scoring and validation rules")
