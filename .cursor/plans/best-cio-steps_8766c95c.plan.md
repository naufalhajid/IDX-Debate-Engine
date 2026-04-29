---
name: best-cio-steps
overview: Ringkas konteks yang dikirim ke CIO dan tambahkan retry parse agar output CIO JSON tetap valid, tanpa mematikan peran LLM.
todos:
  - id: cio-state-add-cio-context
    content: "Tambahkan field `cio_context: str` ke `DebateChamberState` di `schemas/debate.py`, dan inisialisasi di `DebateChamber.run()`/`initial_state` di `services/debate_chamber.py`."
    status: completed
  - id: cio-context-summarizer-node
    content: Buat node `_cio_context_summarizer_node` di `services/debate_chamber.py` (menggunakan `flash_llm`) untuk merangkum bull/bear/devil+bagian penting `state['raw_data']` menjadi plain-text ringkas.
    status: completed
  - id: graph-wire-summarizer-to-cio
    content: Modifikasi `_build_graph()` di `services/debate_chamber.py` agar `devils_advocate` mengarah ke `cio_context_summarizer`, lalu ke `cio_judge`.
    status: completed
  - id: cio-use-summarized-context
    content: Ubah `_cio_judge_node` supaya `user_content` menggunakan `state['cio_context']` (menggantikan pengiriman `state['raw_data']` dan transcript mentah).
    status: completed
  - id: cio-output-moderate-caps
    content: Update `json_schema_hint` di `_cio_judge_node` untuk membatasi panjang `summary`, `weighted_reasoning`, dan jumlah item `key_catalysts`/`key_risks` secara moderat.
    status: completed
  - id: cio-parse-retry
    content: Ubah `_cio_judge_node` agar jika parsing JSON gagal, lakukan 1x retry regen dengan instruksi format JSON ketat. Jika gagal lagi, fallback tetap `confidence=0.0`.
    status: completed
  - id: verify-adro
    content: Jalankan ulang `run_debate.py --tickers ADRO`, pastikan warning parse berkurang dan `confidence` tidak jatuh ke 0 untuk kasus yang sama.
    status: pending
isProject: false
---

## Langkah Terbaik: Ringkas input CIO + retry parse JSON

### A. Ringkas konteks yang dikirim ke CIO
1. Tambahkan node ringkasan khusus CIO di [`services/debate_chamber.py`](c-folder-ajid-idx-fundamental-analysis/services/debate_chamber.py).
   - Gunakan `flash_llm` untuk membuat ringkasan plain-text dari:
     - poin inti bull/bear dari `state['debate_history']`
     - `state['devils_advocate_question']`
     - bagian penting dari `state['raw_data']` (fundamental/technicals/sentiment/exdate)
   - Kontrak output ringkasan: plain-text (tanpa code fence), tetap menyertakan angka penting yang diperlukan CIO (terutama yang sudah ada di *trade envelope*).
2. Simpan hasil ringkasan ke state baru: `state['cio_context']`.
   - Tambahkan field `cio_context: str` pada typed state [`schemas/debate.py`](c-folder-ajid-idx-fundamental-analysis/schemas/debate.py).
3. Ubah `_cio_judge_node` supaya `user_content` tidak mengirim input panjang `state['raw_data']` + join `debate_history` mentah, melainkan memakai `state['cio_context']`.
   - Lokasi perubahan inti: pada pembuatan `hist` dan `user_content` di fungsi `_cio_judge_node` (bagian di `services/debate_chamber.py` saat ini membangun `hist` dan mengisi:
     - `f"Synthesized Market Data:\n{state['raw_data']}\n\n"`
     - `f"Full Debate Transcript:\n{hist}\n\n"`).

### B. Atur output CIO agar tetap kompak namun tidak “super short”
1. Tetap gunakan `json_schema_hint` yang sudah ada di `_cio_judge_node`.
2. Tambahkan pembatasan panjang yang moderat untuk field naratif di `json_schema_hint`:
   - `summary`: maksimal 2–3 kalimat atau <= N karakter
   - `weighted_reasoning`: <= N karakter
   - `key_catalysts` dan `key_risks`: max 2 item per list, tiap item <= N karakter
3. Tujuan: memastikan total panjang output JSON lebih kecil sehingga model lebih berpeluang menyelesaikan JSON secara lengkap.

### C. Tambahkan retry 1x saat parse JSON CIO gagal
1. Wrap blok `json.loads(_sanitize_json(resp.content))` di `_cio_judge_node` menjadi 2 attempt:
   - Attempt #1: panggil `pro_llm` seperti sekarang.
   - Jika parse gagal (exception yang sama dengan log: delimiter/unterminated string), lakukan attempt #2:
     - panggil `pro_llm` lagi dengan prompt tambahan yang meminta: “regenerate ONLY valid JSON, ensure braces are fully closed; do not use markdown fences”.
     - tidak menuntut “super short” (sesuai pilihan kamu), tapi tetap mempertahankan format ketat dan pembatasan panjang moderat di prompt.
2. Kalau attempt #2 juga gagal, baru gunakan fallback existing:
   - `confidence=0.0` dan `rating="HOLD"` seperti di blok `except` saat ini.

### D. Validasi hasil
1. Jalankan ulang:
   - `run_debate.py --tickers ADRO`
2. Verifikasi:
   - jumlah warning `[CIO] Primary JSON parse failed ...` berkurang/0 untuk tickers yang sama
   - `output/debates/*_debate.json` punya `confidence > 0` untuk kasus yang sebelumnya gagal
   - folder `output/debug/cio_json_parse/` tidak bertambah untuk ticker yang seharusnya sukses

### Cuplikan referensi untuk lokasi perubahan (inti)
- Output parse & fallback: blok `except Exception as e:` di `_cio_judge_node` pada `services/debate_chamber.py` (bagian yang sekarang menetapkan `confidence=0.0`).
- Input panjang ke CIO: bagian pembuatan `hist` dan `user_content` di `_cio_judge_node` pada `services/debate_chamber.py` yang saat ini mengirim `state['raw_data']` dan join `debate_history` mentah.
- State typed: `DebateChamberState` di `schemas/debate.py`.

### Mermaid (opsional, alur data)
```mermaid
flowchart TD
  A[Round bull/bear + raw_data + devils_advocate] --> B[CIO Context Summarizer node]
  B --> C[CIO Judge (_cio_judge_node)]
  C -->|valid JSON| D[CIOVerdict parsed -> confidence & rating]
  C -->|parse fail| E[Retry 1x regen ONLY JSON]
  E -->|valid| D
  E -->|fail| F[Fallback HOLD confidence=0.0]
```