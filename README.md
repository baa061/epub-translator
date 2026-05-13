# EPUB Translator

A small toolkit for turning any EPUB into a **bilingual** EPUB
— each original sentence followed by its English (or other-language)
translation, styled however you like. Designed for language learners who want
to read literature in the original while keeping a translation a glance away.

There are two ways to use it:

1. **`index.html`** — a single-file browser UI. Drop an EPUB in, pick a
   translator (Gemini, Claude, OpenAI, Groq, DeepSeek, Mistral, OpenRouter,
   Lingva, or MyMemory — the last two need no API key), tweak the
   translation style live, and run the full translation in the browser.
   No install.
2. **`translate_epub.py`** — a standalone Python script that translates an
   EPUB via free Google Translate (no API key, no cost) with a SQLite cache
   so interrupted runs resume cleanly. Sentence-level, paragraph-batched.

Both produce a valid EPUB with the source text preserved and translations
inserted inline.

> ⚠️ **Please read [`DISCLAIMER.md`](./DISCLAIMER.md) before use.** This tool
> is for EPUBs you legally own or that are in the public domain — not for
> copyrighted material you don't have the right to modify.

---

## `index.html` — browser UI

### Quick start

> **Use Google Chrome.** Safari's stricter CORS and fetch behavior causes
> intermittent failures when calling the AI provider APIs directly from the
> browser. Chrome is the tested/recommended browser.

1. Open `index.html` in **Google Chrome** (`file://` works, no server needed).
2. Drag an EPUB into the dropzone.
3. The page auto-detects the source language from the EPUB metadata and
   defaults the target to English (or Spanish if the book is in English).
4. Pick a translator and paste the API key:
   - **Gemini** — [get a key](https://aistudio.google.com/apikey) (free tier available)
   - **Claude** — [get a key](https://console.anthropic.com/settings/keys)
   - **OpenAI** — [get a key](https://platform.openai.com/api-keys)
   - **Groq** — [get a key](https://console.groq.com/keys) (free tier, fast Llama 3.3 70B)
   - **DeepSeek** — [get a key](https://platform.deepseek.com/api_keys) (very cheap, strong multilingual)
   - **Mistral** — [get a key](https://console.mistral.ai/api-keys/) (good European languages)
   - **OpenRouter** — [get a key](https://openrouter.ai/keys) (one key, access to many models)
   - **Lingva** — no key. Public proxy to Google Translate. Rate-limited; best for previews.
   - **MyMemory** — no key (optional email raises limit). ~10K chars/day per IP. Translation-memory based.

   The no-key options work for previewing and short books. For a full novel,
   use `translate_epub.py` (below) — same free Google Translate quality with
   retries, paragraph batching, and a resumable cache.
5. Pick style: **layout** (inline / block-below), **size** (75/85/100%),
   **italic** toggle, **color** picker.
6. Click **Translate preview** — translates the first 6 paragraphs so you can
   see exactly how the output will look. Style controls update the preview
   live without re-translating.
7. Click **Translate full book** — runs in the browser. Progress bar shows
   chapters and paragraphs. When done, click **Download .epub**.

### What it does under the hood

- Reads the EPUB with [JSZip](https://stuk.github.io/jszip/) entirely in the
  browser. Nothing is uploaded anywhere.
- Walks the OPF spine, skipping cover and navigation files, and modifies the
  `<p>` elements in each prose chapter — leaving images, fonts, the manifest,
  and the cover untouched.
- Splits each paragraph into sentences using a regex that respects dialogue
  em-dashes and Spanish/English abbreviations.
- Calls the chosen AI provider sentence-by-sentence with a short translation
  prompt. Results are cached in-memory keyed by provider+model+target+source
  so a stop/restart resumes near where you left off.
- Wraps each Spanish sentence in `<span class="es">…</span>` and inserts a
  styled `<span class="en" style="…your live CSS…">…</span>` immediately
  after, so the on-disk EPUB matches the preview pixel-for-pixel.
- Re-packs the modified EPUB (preserving `mimetype` as STORED per spec) and
  offers it as a download.

### Security notes

- API keys live in `sessionStorage` for the lifetime of the browser tab. They
  are **never** written to disk, sent to any server other than the chosen AI
  provider, or persisted across tab close. There's a "Forget keys" link.
- Claude and OpenAI calls use the providers' browser-direct headers
  (`anthropic-dangerous-direct-browser-access` for Claude, OpenAI's permissive
  CORS). Fine for personal local use; not recommended for production.

---

## `translate_epub.py` — Python script (free Google Translate)

A self-contained alternative for users who don't want to pay for an AI API.
Uses [`deep_translator`](https://github.com/nidhaloff/deep-translator)'s free
Google Translate backend, paragraph-level batching for speed, and a SQLite
cache that survives Ctrl-C.

### Quick start

The script bootstraps its own venv on first run.

```bash
python3 translate_epub.py path/to/book.epub
```

That's it. First invocation creates `.venv-translate-epub/`, installs
`beautifulsoup4`, `lxml`, `deep_translator`, `tqdm`, then re-executes itself.

Output is written next to the input as `<bookname> -- EN.epub`.

### Resume / cache

A SQLite cache (`translate_epub.cache.sqlite3`) is written next to the EPUB.
Each translation is committed immediately. Ctrl-C is safe; re-running the
same command picks up exactly where it stopped — progress bar jumps quickly
through the cached portion.

### Options

```text
python3 translate_epub.py INPUT.epub [OUTPUT.epub]
    --chapters c1.xhtml,c3.xhtml      # translate only these XHTMLs
    --skip cubierta.xhtml             # default; skip cover page
```

### Quality vs. cost trade-off

Free Google Translate is fast and free but noticeably less natural than an
LLM on literary prose. If you have a Gemini or Claude key, the browser UI
will give a meaningfully better read.

---

## Working with [`bilingual_book_maker`](https://github.com/yihong0618/bilingual_book_maker)

The browser UI also generates a ready-to-run terminal command for
`bilingual_book_maker`'s `--sentence_mode` if you'd rather run the
translation from the shell. It's not bundled here — clone it separately:

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then paste the command the UI generates. The `--translation_style "..."`
flag receives the same CSS string the browser preview shows, so the
on-disk output matches the preview.

---

## Credits

- [yihong0618/bilingual_book_maker](https://github.com/yihong0618/bilingual_book_maker)
  — the project that pioneered `--sentence_mode` and inspired the format of
  the bilingual output here.
- [JSZip](https://stuk.github.io/jszip/) — in-browser ZIP reading/writing.
- [Alpine.js](https://alpinejs.dev/) — small reactivity layer for the UI.

## License

MIT — see [LICENSE](./LICENSE).
