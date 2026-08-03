# LLM Chess Arena

Aplikasi **desktop** (PyQt6 + QWebEngineView) untuk mengadu kepintaran dua LLM antar-provider
lewat catur. Pilih dua model dari OpenRouter, klik mulai, lalu kedua AI bermain otomatis tanpa
intervensi manusia sampai salah satu menang **2 kali** (mutlak; remis tidak dihitung & diulang).

Papan catur visual beranimasi, scoreboard "VS" gaya siaran, riwayat langkah (PGN), log langkah
ilegal/retry, dan tema gelap/terang.

![arsitektur singkat](#)

## Fitur

- **Retrieve model OpenRouter** — daftar model diambil langsung dari API; pemilihan lewat
  combobox ter-filter sehingga tidak mungkin salah ketik nama model.
- **Sesi best-to-2** — warna (putih/hitam) diacak ulang tiap game; putih jalan pertama.
- **Otomatis end-to-end** — AI memutuskan langkah sendiri; jalan sampai ada pemenang sesi.
- **Validasi langkah** via python-chess; langkah ilegal/format salah → retry (maks 3x) dengan
  pesan koreksi; gagal terus → forfeit game itu. Semua dicatat di log.
- **Posisi yang dibaca model** — tiap giliran model menerima **FEN + diagram papan ASCII 8×8
  (sudut pandang Putih) + daftar lengkap langkah legal (UCI)**. Posisi persis, bukan tebakan.
- **Mode penalaran + self-evaluation (iterasi diputuskan AI)** — tiap giliran 2 fase: (1) model
  menganalisis taktik & ancaman lalu mengusulkan langkah; (2) model **mengkritik langkahnya sendiri
  dan memutuskan sendiri berapa kali mengulang** (`"decision": "continue"` / `"final"`) sampai
  yakin itu langkah terbaiknya, lalu melangkah. Cap pengaman: `MAX_SELF_EVAL_ROUNDS` di
  `app/llm_player.py` (default 4).
- **Log Hasil Berpikir AI (panel Penalaran)** — model membalas JSON terstruktur
  `{"reasoning": "...", "move": "<uci>"}`; parser robust (JSON dulu, fallback ke prosa UCI/SAN).
  Panel **Penalaran AI** di samping papan menampilkan tiap langkah: nomor, warna, model (di-warnai
  A=azure/B=copper), langkah (SAN), **waktu berpikir**, jumlah putaran self-eval, dan teks alasannya.
  Tiap kartu bisa di-lipat/buka (atau lipat semua), auto-scroll ke langkah terbaru, dan bisa
  **diekspor ke TXT/JSON**. Bila model tak memberi alasan → ditandai "Tidak ada alasan".
- **Animasi pergerakan halus** — bidak meluncur dengan easing (≈320ms, `transform`), efek "lift"
  saat diangkat, highlight kotak asal & tujuan, serta penanganan khusus **makan (fade-out), rokade
  (dua bidak serempak), en passant, dan promosi (pion meluncur lalu "pop" jadi bidak baru)**.
  Animasi disinkronkan dengan giliran (langkah berikutnya menunggu animasi selesai) tanpa memblok
  worker. Hormati `prefers-reduced-motion`.
- **Efek suara opsional** — "tock" langkah, "clack" makan, "ding" skak, motif skakmat; di-generate
  via WebAudio (tanpa file aset), **disinkronkan dengan akhir animasi**. Default **mati**; toggle
  lewat ikon 🔇/🔊 di kanan atas (tersimpan lokal).
- **Threading** — panggilan LLM di thread terpisah (QThread); UI tidak freeze; update papan
  thread-safe lewat sinyal Qt + QWebChannel. Animasi di-serialkan di sisi JS (antrean render),
  bukan dengan memblok worker Python.
- **Aman** — API key dibaca dari environment / `.env` / `config.json` lokal; tidak pernah hardcode.
  Semua teks dinamis (reasoning/model/SAN/log) masuk DOM via `textContent` (anti-XSS).

## Prasyarat

- Python 3.10+ (teruji di 3.14).
- Akun & API key OpenRouter: https://openrouter.ai/keys

## Instalasi

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Konfigurasi API key

Pilih salah satu:

1. **Environment variable**
   ```bash
   # Windows (PowerShell)
   $env:OPENROUTER_API_KEY = "sk-or-..."
   # macOS/Linux
   export OPENROUTER_API_KEY="sk-or-..."
   ```
2. **File `.env`** — salin `.env.example` menjadi `.env`, isi `OPENROUTER_API_KEY`.
3. **Lewat aplikasi** — saat dijalankan tanpa key, aplikasi menampilkan kolom untuk menempel key,
   atau buka menu **Pengaturan (ikon ⚙ di kanan atas)**. Key disimpan lokal di `config.json`
   (ber-`.gitignore`) secara **permanen sampai Anda ganti** lewat Settings.

## Pengaturan (menu ⚙) & provider LLM lain

Ikon gigi di kanan atas membuka panel Pengaturan:

- **API Key** — disimpan permanen di `config.json`. Kosongkan saat menyimpan untuk mempertahankan
  key yang sudah ada (key tersimpan tidak pernah ditampilkan balik demi keamanan).
- **Base URL Provider LLM** — default OpenRouter (`https://openrouter.ai/api/v1`). Isi endpoint
  **OpenAI-compatible** lain (provider sendiri, gateway, dll.) bila perlu; kosongkan untuk kembali
  ke default. Setelah disimpan, daftar model otomatis dimuat ulang dari provider tersebut.

Base URL juga bisa diset via env `OPENROUTER_BASE_URL` (override config.json). Tombol **Refresh**
di bawah pemilih model memuat ulang katalog kapan saja.

## Menjalankan

```bash
python main.py
```

1. Window terbuka, daftar model OpenRouter termuat di kedua combobox.
2. Pilih model A dan model B (contoh matchup ter-preselect bila tersedia).
3. Klik **Mulai Pertandingan**.
4. Tonton kedua AI bertanding otomatis sampai ada yang menang 2x.

> Catatan biaya: setiap langkah memanggil OpenRouter (berbayar). Tombol **Hentikan** selalu
> tersedia. Counter "Panggilan API" menampilkan perkiraan jumlah panggilan berjalan.

## Menjalankan test

```bash
pytest
```

Test mencakup parsing langkah (UCI/SAN/output verbose/ilegal/retry), deteksi outcome
(skakmat/remis/stalemate/insufficient/forfeit/cap), dan scoring sesi best-to-2 (OpenRouter di-mock).

## Struktur

```
main.py                  Entry point (QApplication + MainWindow)
app/
  config.py              Manajemen API key (env/.env/config.json)
  openrouter.py          Client OpenRouter (list models, chat, retry/backoff)
  chess_game.py          Wrapper python-chess (state, outcome, PGN)
  llm_player.py          Prompt engineering + parsing langkah robust + retry
  match_engine.py        QThread worker: loop sesi best-to-2, scoring
  bridge.py              QWebChannel bridge (sinyal -> JS, slot <- JS)
  ui/main_window.py      QMainWindow + QWebEngineView
  ui/web/                UI web (board renderer, scoreboard, panel)
tests/                   pytest (logika inti, tanpa jaringan/GUI)
```

## Paket jadi .exe (Windows)

Sudah teruji build & jalan (window terbuka, papan ter-render, QWebChannel aktif).

```bash
pip install pyinstaller
```

```powershell
# Opsi 1 — onedir (folder, start lebih cepat). Hasil: dist\LLMChessArena\LLMChessArena.exe
.\build.ps1

# Opsi 2 — single file portabel. Hasil: dist\LLMChessArena-portable.exe (~194 MB)
.\build.ps1 -OneFile
```

Atau perintah PyInstaller langsung (pemisah `--add-data` = `;` di Windows, `:` di macOS/Linux):

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed `
  --name LLMChessArena --add-data "app/ui/web;app/ui/web" main.py
```

**API key untuk versi .exe:** letakkan file `.env` (berisi `OPENROUTER_API_KEY=...`) di sebelah
`.exe`, atau set environment variable, atau tempel key lewat UI saat aplikasi jalan (tersimpan ke
`config.json` di sebelah `.exe`).

Catatan:
- Onedir lebih disarankan untuk distribusi: start lebih cepat dan ukuran on-disk lebih jelas.
  Untuk dibagikan, zip seluruh folder `dist\LLMChessArena\`.
- Single-file lebih portabel (1 file) tapi start pertama lebih lambat (ekstraksi ke folder temp).
- QtWebEngine besar; ukuran ~194 MB (single) / ~513 MB (onedir) adalah wajar.
- Self-check opsional: jalankan dengan env `ARENA_SELFTEST=1` untuk memverifikasi render papan
  (hasil ditulis ke file `ARENA_SELFTEST_OUT`), berguna untuk CI/diagnostik build.

## Lisensi aset

Bidak catur memakai set **cburnett** (oleh Colin M.L. Burnett) dari proyek lichess
(GPLv2+). Font: Fraunces, Inter, JetBrains Mono (Open Font License).
