# DRY RUN — Prompt v5.0 (Post-Fix)

**Source:** Republika — Mengenal Bot Komersial, Mesin di Balik Masifnya Spam Judol di Medsos
**URL:** https://ameera.republika.co.id/berita/thjzng425/mengenal-bot-komersial-mesin-di-balik-masifnya-spam-judol-di-medsos
**Image:** https://static.republika.co.id/uploads/images/kanal_slide/ilustrasi-judi_250710153221-914.jpg
**Provider:** Mistral
**Hook Score:** 7/10 (A/B winner dari 2 variants)

---

## Slide 1 — HOOK
Lo tau gak...

**128% lebih banyak** komentar spam judi online di medsos sejak Piala Dunia 2026?

## Slide 2 — PROBLEM
Kesalahan terbesar kita: kira ini cuma akun-akun iseng.

Faktanya, ini **sistem bot komersial** yang dijual bebas, bisa pantau trending real-time & banjiri komentar otomatis.

## Slide 3 — REVELATION
Cara kerjanya sadis:

1. Bot pantau postingan **viral/trending** (misal Piala Dunia).
2. Operator kasih perintah.
3. **Ratusan akun palsu** langsung komentar spam judi.

> ⚠️ Grounding nangkep "ratusan" — tidak ada di artikel, langsung di-strip.

## Slide 4 — DEEP DIVE
Alatnya udah canggih banget:

- **Monitoring real-time** (deteksi trending dalam hitungan detik).
- **Jaringan akun palsu** (dikelola terpusat, kayak tentara siap tempur).
- **Dijual bebas** (siapa aja bisa beli, gak perlu jago coding).

## Slide 5 — OPINI
Kenapa ini bahaya?

- **Anak di bawah umur** gampang kena paparan.
- **Korban penipuan** makin banyak (promo judi palsu).
- **Medsos jadi sarang** (Instagram/FB udah kerja sama dengan pemerintah buat bersihin).

## Slide 6 — CTA
Pertanyaan buat lo: Kalo lo liat komentar spam judi di postingan viral, lo **laporkan** atau **diem aja**?

Share jawaban lo di kolom komentar, gue penasaran!

---

## Caption
Bot judi online udah jadi industri gelap, dan Piala Dunia 2026 jadi ajang mereka merajalela.

## Hashtags
#JudiOnline

---

## Fix Verification

| Bug | Before | After | Status |
|-----|--------|-------|--------|
| Caption em dash | "—dan ini baru awal." | Clean, no em dash | ✅ Fixed |
| Banned CTA phrase | "Komen pendapat lo di bawah" | Not in code banned list → added 10 new patterns | ✅ Fixed |
| Grounding "5 tahun" | Skipped (int <= 5) | Now checks time-unit pairing | ✅ Fixed |
| Grounding "ratusan" | Not caught | Caught & stripped | ✅ Working |

## Remaining Notes

1. **Slide 6 CTA**: "Share jawaban lo di kolom komentar" — not an exact banned phrase but still template-ish. Ceiling of regex banning; prompt-level instruction "variasikan CTA" is the real fix.
2. **Slide 3 artifact**: After stripping "ratusan", formatting left "** akun palsu**" with stray space. Minor, needs post-strip cleanup.
3. **Hook quality**: "Lo tau gak..." is functional but generic. Hook variation system picks patterns but doesn't guarantee creativity.
