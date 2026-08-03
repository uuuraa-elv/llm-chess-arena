# Spec: Prompt Catur "Kualitas Maksimal" (Bundle C)

- **Tanggal:** 2026-06-22
- **Status:** Desain disetujui user, siap rencana implementasi
- **Project:** LLM Chess Arena (`C:\Users\fuadn\Downloads\Project\llm-chess-arena`)
- **Skala:** Perubahan prompt-engineering terbatas + plumbing kecil. Bukan perubahan arsitektur.

## 1. Konteks (kondisi saat ini)

Tiap giliran, `app/llm_player.py` mengirim ke LLM:
- `system_prompt(color)` — peran "strong chess engine", tactics-first, output JSON `{"reasoning","move"}` (+`decision` di self-eval).
- `user_prompt(game, last_opponent_move)` — FEN + diagram papan ASCII + `You are` + `Move number` + langkah lawan terakhir (UCI mentah) + daftar langkah legal (UCI saja).
- `self_eval_prompt(game, move)` — kritik diri + `decision: continue|final`.
- `correction_prompt(...)` — saat jawaban tak berisi langkah legal.

`request_move()` menjalankan 2 fase (PROPOSE → SELF-EVAL berulang sesuai keputusan model, cap `MAX_SELF_EVAL_ROUNDS=4`). Parser `parse_response()`: JSON dulu, fallback prosa robust (`extract_move`). `match_engine._play_game()` memanggil `request_move`, mengukur `thinking_ms`, dan mengirim payload board (termasuk `reasoning`) ke UI panel "Penalaran AI".

## 2. Tujuan / Non-tujuan

**Tujuan:** menaikkan kekuatan main LLM lewat prompt yang lebih kaya konteks & terstruktur, tanpa mengubah arsitektur, aturan, atau format I/O.

**Non-tujuan (TIDAK berubah):** aturan catur & deteksi outcome, sesi best-of-2, randomisasi warna, antrean animasi/board.js, suara, keamanan API key, **format JSON output** (`{"reasoning","move","decision"}`), jalur parsing & fallback, struktur sinyal Qt/QWebChannel, UI panel.

## 3. Detail desain

### A. Input baru per giliran
1. **Daftar langkah legal SAN** — utama (mis. `Nf3 Bc4 exd5`), plus daftar UCI ringkas tetap disertakan agar pemilihan tak ambigu. Parser sudah menerima SAN & UCI (tak berubah).
2. **Riwayat partai (movetext SAN)** — game berjalan, mis. `1. e4 e5 2. Nf3 Nc6`. Dikirim PENUH (game di app ini umumnya < 60 langkah; backstop `MAX_PLIES_HARD_CAP=400`). Bila kosong (langkah pertama), baris riwayat tidak ditampilkan.
3. **Langkah lawan terakhir dalam SAN** (bukan UCI mentah) — lebih natural untuk model.
4. **Konteks pertandingan** — baris skor dari sudut pandang mover, mis. `Match (best of 2): you 1 - 0 opponent.`

### B. Metode penalaran (CCT + bandingkan kandidat)
- Instruksi diubah agar model: (1) **mendaftar Checks, Captures, Threats** untuk KEDUA sisi; (2) **mengajukan 2-3 kandidat langkah**; (3) menyebut **balasan terbaik lawan** untuk tiap kandidat; (4) baru memilih.
- `self_eval_prompt` diperkuat: wajib membandingkan **minimal 1 alternatif** dan memverifikasi langkah tidak menggantung material / kena taktik, sebelum `decision: final`.

### C. Framing pertandingan (di system prompt, statis)
- Tambah arahan strategis: main untuk MENANG; saat posisi cenderung seri padahal butuh kemenangan, pilih langkah dengan peluang menang terbesar (hindari penyederhanaan ke remis); saat unggul material, sederhanakan dengan aman. Skor live disuplai lewat `match_context` (bagian A.4).

### D. Panjang reasoning
- `REASONING_MAX_CHARS`: 600 → **1500**, agar analisis CCT + perbandingan kandidat muat. Panel UI sudah scroll/collapse (tak berubah). Prompt mengganti "1-3 sentences" → "concise but complete: forcing moves, candidates, and the opponent's best reply".

## 4. Perubahan kode (per file, dengan signature)

### `app/chess_game.py` (tambah 2 method, murni baca)
- `legal_moves_san(self) -> list[str]` → `[self.board.san(m) for m in self.board.legal_moves]`.
- `movetext(self) -> str` → replay `board.move_stack` dari papan baru, bangun string `"1. e4 e5 2. Nf3 ..."`; kembalikan `""` bila stack kosong. Tidak mengubah state board (pakai papan replay terpisah).

### `app/llm_player.py`
- `REASONING_MAX_CHARS = 1500`.
- `system_prompt(color)` — tulis ulang: peran + framing pertandingan (C) + metode CCT (B) + spesifikasi JSON (tak berubah formatnya).
- `user_prompt(game, last_opponent_move, history="", match_context="")` — tambah baris `match_context` (bila ada), `Game so far (SAN): {history}` (bila ada), `Your legal moves (SAN): {san}` + `(UCI): {uci}`, instruksi CCT ringkas. `last_opponent_move` kini diisi SAN oleh pemanggil.
- `self_eval_prompt(game, move)` — perkuat instruksi (bandingkan ≥1 alternatif + balasan terbaik lawan); format JSON `decision` tetap.
- `request_move(..., history="", match_context="")` — teruskan `history` & `match_context` ke `user_prompt`. Signature lama tetap kompatibel (param baru default `""`), jadi test existing tak rusak.

### `app/match_engine.py`
- Di `_play_game`, sebelum memanggil `request_move`:
  - `history = game.movetext()` (langkah-langkah sebelum giliran ini).
  - `mover_is_a = (turn == chess.WHITE) == white_is_a`; `your = score_a if mover_is_a else score_b`; `opp = score_b if mover_is_a else score_a`; `match_context = f"Match (best of {WINS_NEEDED}): you {your} - {opp} opponent."`.
  - `last_move_san` (SAN langkah terakhir; disimpan saat push) diteruskan sebagai `last_opponent_move`.
- Panggil `request_move(game, mover_model, turn, last_move_san, api_key=..., chat_fn=..., history=history, match_context=match_context)`.
- Payload board & sinyal lain TIDAK berubah.

## 5. Aliran data
`match_engine` (movetext + skor + last SAN) → `request_move` → `user_prompt`/`system_prompt` → LLM → JSON `{"reasoning","move","decision"}` → `parse_response` (tak berubah) → `MoveOutcome.reasoning` → payload board → panel UI (tak berubah).

## 6. Rencana testing (TDD)
- `test_chess_game.py`: `legal_moves_san()` start memuat `"Nf3"`; `movetext()` kosong di awal, `"1. e4"` setelah 1.e4, `"1. e4 e5"` setelah balasan; `movetext()` tidak mengubah `board`/`turn`.
- `test_llm_player.py`: `user_prompt(...)` memuat `"Game so far"` saat history non-kosong & tidak saat kosong; memuat baris `match_context`; memuat `"legal moves (SAN)"`; `system_prompt` memuat penanda CCT (mis. `"Checks"`, `"Captures"`, `"Threats"`) & framing menang. Parsing JSON/prosa & self-eval (existing) tetap hijau.
- `test_match_engine.py`: payload board tetap membawa field lama (reasoning/thinking_ms/model/mover_is_a/eval_rounds) — regresi guard.
- Target: SEMUA test existing (60) tetap hijau + test baru hijau.

## 7. Biaya
Token/giliran naik (riwayat + daftar SAN + analisis CCT lebih panjang). Sesuai keputusan user "pengaman biaya minim" — diterima. Cap self-eval tetap 4; tombol Hentikan & counter panggilan API tetap ada.

## 8. Risiko & mitigasi
- **Model "over-talk" / reasoning kepanjangan** → di-clip `REASONING_MAX_CHARS=1500`; prompt minta "concise but complete".
- **movetext salah/format SAN** → unit test replay; SAN dihasilkan python-chess (otoritatif).
- **Regresi parsing** → format JSON output tidak berubah; fallback prosa tetap; test guard.
- **Konteks membengkak di game panjang** → movetext penuh masih kecil (< ~400 ply backstop, praktis < 60); bisa di-cap di iterasi berikut bila perlu (out of scope sekarang).

## 9. Out of scope (iterasi lain)
- Hint materi eksplisit (siapa unggul berapa bidak) — tidak disertakan kecuali diminta.
- Kontrol gaya/level main per model.
- Caching/ringkas riwayat untuk game sangat panjang.

## 10. Verifikasi akhir
- `pytest` hijau (existing 60 + baru).
- Self-test desktop (`ARENA_SELFTEST=1`) OK.
- Rebuild `.exe` (onedir + onefile) + self-test (UI/format tak berubah, tapi tetap diverifikasi).
- (Opsional) review adversarial ringkas pada perubahan prompt & plumbing.
