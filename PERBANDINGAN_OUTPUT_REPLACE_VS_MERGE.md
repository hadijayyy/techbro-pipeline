# Perbandingan Output Prompt Techbro

**Artikel uji:** Rupiah Melemah dan Industri Melambat, Pengusaha Waspadai Risiko PHK
**Sumber:** money_kompas
**URL:** https://money.kompas.com/read/2026/07/29/111000726/rupiah-melemah-dan-industri-melambat-pengusaha-waspadai-risiko-phk-

> Kedua hasil di bawah adalah output mentah model dari artikel dan waktu uji yang sama. Belum lolos validator Techbro. Jangan publish apa adanya.

---

## 1. Replace SYSTEM_PROMPT

```json
{
  "status": "success",
  "arc": "market_shock",
  "value_mode": "watchlist",
  "angle": "Rupiah melemah dan perlambatan industri bikin pengusaha waspada PHK—tapi dampaknya ke dompet kita lebih dari sekadar angka kurs",
  "thesis": "Selama ini kita mikir pelemahan rupiah cuma soal harga barang impor yang naik, tapi artikel ini nunjukin kalau dampaknya bisa sampai ke meja kerja kita—karena industri yang kesulitan bayar dolar akhirnya harus ngurangi biaya lewat PHK, padahal 70% bahan baku kita masih impor",
  "target_reader": "Karyawan sektor manufaktur, pekerja di industri padat karya, dan siapa pun yang bergantung pada stabilitas lapangan kerja",
  "source": {
    "title": "Rupiah Melemah dan Industri Melambat, Pengusaha Waspadai Risiko PHK",
    "publisher": "Kompas Money",
    "author": "Tidak disebutkan",
    "url": "https://money.kompas.com/read/2026/07/29/111000726/rupiah-melemah-dan-industri-melambat-pengusaha-waspadai-risiko-phk-",
    "published_at": "29 Juli 2026",
    "event_date": "28 Juli 2026"
  },
  "post_1": "Rupiah nyentuh Rp 18.200/dolar tadi. Masalahnya, 70% bahan baku pabrik kita masih impor. Artinya, setiap kali rupiah melemah 1%, biaya produksi langsung naik—padahal gaji kita belum tentu ikut naik.",
  "post_2": "Coba bayangin: pabrik tekstil yang biasanya bayar Rp 10 miliar untuk impor benang, sekarang harus bayar Rp 10,5 miliar. Itu cuma buat bahan baku doang. Belum hitung biaya listrik, sewa mesin, atau gaji karyawan. Kalau penjualan stagnan, yang pertama dikorbankan biasanya jam kerja—atau malah posisi.",
  "post_3": "Kenapa ini bisa terjadi? Tiga alasan utama:\n1️⃣ Rupiah melemah → biaya impor naik → harga jual barang harus naik → permintaan turun.\n2️⃣ PMI manufaktur jatuh ke 46,9 (terendah sejak 2025) → artinya pabrik-pabrik mulai ngurangi produksi.\n3️⃣ 126.000 orang udah jadi nonaktif di BPJS Ketenagakerjaan tahun ini—50.000 di antaranya karena PHK.",
  "post_4": "Siapa yang paling kena?\n- Karyawan pabrik tekstil, elektronik, otomotif (padat karya, banyak impor).\n- Pekerja kontrak atau outsourcing (yang pertama dipotong kalau perusahaan hemat).\n- UMKM yang nyuplai bahan ke pabrik besar (kalau pabriknya lesu, pesanannya juga berkurang).\n\nYang mungkin untung: eksportir komoditas (sawit, batubara) karena dapat dolar lebih banyak. Tapi mereka cuma segelintir.",
  "post_5": "Menurut gue, risiko terbesarnya bukan cuma PHK—tapi PHK yang gak terprediksi. Perusahaan sekarang lagi ngitung-ngitungan: ngurangi jam kerja dulu, merumahkan karyawan, baru PHK. Masalahnya, kalau rupiah terus melemah dan permintaan gak naik, skenario terburuknya bukan cuma 126.000 orang, tapi bisa dua kali lipat tahun depan.",
  "post_6": "Kalo rupiah tembus Rp 19.000/dolar dalam 3 bulan ke depan, dua hal bakal terjadi:\n1️⃣ Harga barang impor (elektronik, obat, bahan pokok) naik 5–10%.\n2️⃣ Perusahaan yang udah ngurangi jam kerja bakal mulai PHK besar-besaran.\n\nLu udah cek posisi lo di ‘peta risiko’ perusahaan? (Cek S5 thread sebelumnya buat indikatornya).",
  "practical_value": {
    "type": "watchlist",
    "items": [
      {
        "action": "Pantau pergerakan rupiah mingguan (cek di Bank Indonesia atau aplikasi keuangan terpercaya).",
        "reason": "Pelemahan rupiah langsung pengaruhi biaya impor bahan baku perusahaan.",
        "basis": "general_safe_practice",
        "caveat": "Fluktuasi harian wajar, tapi perhatikan tren bulanan."
      },
      {
        "action": "Cek apakah perusahaan tempat lu kerja banyak impor bahan baku (tanya HR atau cek laporan tahunan).",
        "reason": "Perusahaan dengan impor tinggi lebih rentan ngurangi biaya lewat PHK.",
        "basis": "general_safe_practice",
        "caveat": "Data impor biasanya ada di laporan keuangan atau presentasi investor."
      },
      {
        "action": "Siapkan dana darurat minimal 3 bulan gaji (kalau belum punya).",
        "reason": "PHK atau pengurangan jam kerja bisa bikin pendapatan turun tiba-tiba.",
        "basis": "general_safe_practice",
        "caveat": "Prioritaskan tabungan likuid (deposito atau reksadana pasar uang)."
      },
      {
        "action": "Ikuti perkembangan PMI manufaktur bulanan (rilis tiap awal bulan oleh S&P Global).",
        "reason": "PMI di bawah 50 artinya industri sedang kontraksi—risiko PHK meningkat.",
        "basis": "official_source",
        "caveat": "PMI hanya indikator, bukan kepastian."
      }
    ]
  },
  "calculation_notes": [],
  "verification_notes": [
    "Angka 70% impor bahan baku dikutip langsung dari pernyataan Shinta Widjaja Kamdani (Ket

---

## 2. Merge SYSTEM_PROMPT Lama + Article-to-Action Layer

**status:** success

**arc:** market_shock

**value_mode:** watchlist

**angle:** Rupiah melemah bikin biaya produksi naik, tapi gaji lo gak ikut naik—risiko PHK udah di depan mata

**thesis:** Pelemahan rupiah dan perlambatan industri manufaktur meningkatkan risiko PHK karena biaya produksi naik tapi permintaan turun. Pemetaan dini perusahaan bisa jadi kunci mitigasi.

### S1

Gaji lo bisa lenyap bulan depan.

Rupiah tembus Rp18.200, tapi 70% bahan baku pabrik lo impor. Biaya produksi melonjak, permintaan lesu—PHK udah di depan mata.

### S2

Bayangin lo beli bahan baku Rp100 juta.

Pas rupiah melemah, jadi Rp110 juta. Tapi harga jual produk lo gak bisa naik—karena konsumen juga lagi ketat.

Itu artinya: keuntungan lo tipis, atau malah rugi.

### S3

Penyebabnya gak cuma rupiah.

PMI manufaktur turun jadi 46,9—terendah sejak 2025. Artinya: pabrik-pabrik udah ngurangin produksi.

Indeks kepercayaan industri juga turun jadi 52,9. Optimisme udah tipis, pesimisme makin tebal.

### S4

Yang paling kena: pekerja pabrik.

126.000 orang udah nonaktif gara-gara PHK Januari-Mei 2026. 50.000 di antaranya ngambil JHT karena gak punya pilihan.

Dan ini baru awal—kalau rupiah makin lemah, angka ini bisa nambah.

### S5

Menurut gw, risiko terbesarnya bukan cuma PHK.

Tapi perusahaan yang bertahan tapi ngurangin jam kerja atau gaji. Lo dapet uang lebih sedikit, tapi beban hidup gak berkurang.

Pemetaan dini perusahaan jadi penting—kalo perusahaan lo udah ngurangin jam kerja, waspada.

### S6

Kalo rupiah tembus Rp19.000, lo perlu pantau 3 hal:

1. Apakah perusahaan lo ngurangin jam kerja?
2. Apakah ada karyawan yang dirumahkan?
3. Apakah gaji atau bonus dipotong?

Kalo jawabannya iya, siap-siap aja—risiko PHK makin dekat.

### practical_value

```json

"Pekerja perlu pantau kondisi perusahaan dan langkah mitigasi yang diambil (pengurangan jam kerja, merumahkan karyawan) sebagai sinyal risiko PHK."

```

### calculation_notes

```json

"Angka PHK dan klaim JHT dari artikel, tanpa penambahan hitungan baru."

```

### verification_notes

```json

"Data PHK dan klaim JHT dari BPJS Ketenagakerjaan, PMI manufaktur dari sumber artikel."

```

### self_score

```json

9

```

---

## Catatan singkat

- **Replace:** lebih lengkap secara metadata dan playbook, tapi banyak klaim/simulasi di luar artikel serta format bertabrakan dengan guard pipeline.
- **Merge:** lebih dekat ke kontrak pipeline, tapi tetap perlu validator blok klaim berlebihan, simulasi tanpa input, dan pelanggaran gaya.
