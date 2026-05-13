#!/usr/bin/env python3
"""Translate a Spanish EPUB to bilingual ES+EN using free Google Translate.

Each Spanish sentence is followed by its English translation, rendered smaller
and italic. The output remains a valid EPUB. A SQLite cache makes runs
resumable across interruptions and rate-limit pauses.

Usage:
    python3 translate_epub.py INPUT.epub [OUTPUT.epub] [--chapters c1.xhtml,c2.xhtml] [--skip cubierta.xhtml]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REQUIRED_PACKAGES = ["beautifulsoup4", "lxml", "deep-translator", "tqdm"]
VENV_DIR_NAME = ".venv-translate-epub"


def _in_target_venv() -> bool:
    return os.environ.get("TRANSLATE_EPUB_VENV") == "1"


def _bootstrap_venv_and_reexec() -> None:
    """Create a local venv, install deps, and re-exec this script inside it."""
    venv_dir = Path(__file__).resolve().parent / VENV_DIR_NAME
    py = venv_dir / "bin" / "python"
    if not py.exists():
        print(f"[bootstrap] creating venv at {venv_dir}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
        subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        print(f"[bootstrap] installing: {', '.join(REQUIRED_PACKAGES)}")
        subprocess.check_call([str(py), "-m", "pip", "install", *REQUIRED_PACKAGES])
    env = os.environ.copy()
    env["TRANSLATE_EPUB_VENV"] = "1"
    os.execve(str(py), [str(py), __file__, *sys.argv[1:]], env)


if not _in_target_venv():
    _bootstrap_venv_and_reexec()


# --- Imports that require the venv ----------------------------------------
from bs4 import BeautifulSoup, NavigableString  # noqa: E402
from deep_translator import GoogleTranslator  # noqa: E402
from tqdm import tqdm  # noqa: E402


# --- Sentence splitting ----------------------------------------------------
# Common Spanish abbreviations that end with a period but don't end a sentence.
_ABBREVS = {
    "sr", "sra", "srta", "sres", "dr", "dra", "drs", "d", "dn", "dna",
    "dña", "ud", "uds", "vd", "vds", "etc", "vs", "núm", "no", "pág",
    "p", "pp", "vol", "cap", "art", "fig", "ej", "av", "ave", "c", "cía",
    "s", "ss", "ap", "tel", "ext", "min", "máx", "izq", "der", "ed",
    "san", "sto", "sta", "sgte", "lic", "ing", "arq", "prof", "gral",
}
_ABBREV_RE = re.compile(
    r"(?<!\w)(" + "|".join(sorted(_ABBREVS, key=len, reverse=True)) + r")\.",
    re.IGNORECASE,
)
_PLACEHOLDER = "\x00ABBR\x00"


_EN_ABBREVS = {
    "mr", "mrs", "ms", "dr", "drs", "st", "sr", "jr", "prof", "rev",
    "hon", "gen", "col", "capt", "lt", "sgt", "cpl", "etc", "vs", "ave",
    "blvd", "rd", "no", "vol", "pp", "ed", "eds", "fig", "figs", "ca",
    "approx", "inc", "ltd", "co", "corp", "ph", "min", "max",
}
_EN_ABBREV_RE = re.compile(
    r"(?<!\w)(" + "|".join(sorted(_EN_ABBREVS, key=len, reverse=True)) + r")\.",
    re.IGNORECASE,
)


def split_sentences_es(text: str) -> list[str]:
    """Split Spanish prose into sentences. Conservative — preserves dialogue."""
    return _split_sentences(text, _ABBREV_RE, r'[«¿¡"\'(\[\d—A-ZÁÉÍÓÚÜÑ]')


def split_sentences_en(text: str) -> list[str]:
    """Split English prose into sentences."""
    return _split_sentences(text, _EN_ABBREV_RE, r'["\'(\[\d—A-Z]')


def _split_sentences(text: str, abbrev_re: re.Pattern, start_class: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    protected = abbrev_re.sub(lambda m: m.group(1) + _PLACEHOLDER, text)
    parts = re.split(rf'(?<=[.!?…])\s+(?={start_class})', protected)
    out = []
    for p in parts:
        s = p.replace(_PLACEHOLDER, ".").strip()
        if s:
            out.append(s)
    return out


# --- Translation cache ------------------------------------------------------
class TranslationCache:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (src TEXT PRIMARY KEY, dst TEXT NOT NULL)"
        )
        self.conn.commit()

    def get(self, src: str) -> str | None:
        row = self.conn.execute(
            "SELECT dst FROM cache WHERE src = ?", (src,)
        ).fetchone()
        return row[0] if row else None

    def put(self, src: str, dst: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache (src, dst) VALUES (?, ?)", (src, dst)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# --- Translator wrapper ----------------------------------------------------
class Translator:
    def __init__(self, cache: TranslationCache):
        self.cache = cache
        self._client = GoogleTranslator(source="es", target="en")
        self._backoffs = [2, 5, 15, 60]

    def translate(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        cached = self.cache.get(text)
        if cached is not None:
            return cached
        # deep_translator's Google endpoint truncates ~5000 chars; chunk longer.
        if len(text) > 4500:
            chunks = self._chunk_long(text, 4500)
            translated = " ".join(self._translate_one(c) for c in chunks)
        else:
            translated = self._translate_one(text)
        self.cache.put(text, translated)
        # Tiny baseline delay to be polite to the free endpoint.
        time.sleep(0.05)
        return translated

    def translate_paragraph(self, es_text: str) -> tuple[list[str], list[str]]:
        """Translate a paragraph in one call; fall back to per-sentence on misalignment.

        Returns (spanish_sentences, english_sentences) of equal length.
        """
        es_sentences = split_sentences_es(es_text)
        if not es_sentences:
            return [], []
        if len(es_sentences) == 1:
            return es_sentences, [self.translate(es_sentences[0])]

        # Avoid paragraph-level for very long paragraphs — chunked translation
        # can re-merge sentences across chunk boundaries.
        if len(es_text) <= 4500:
            en_text = self.translate(es_text)
            en_sentences = split_sentences_en(en_text)
            if len(en_sentences) == len(es_sentences):
                return es_sentences, en_sentences
            # Misalignment: fall through to per-sentence below. Don't waste the
            # paragraph-level result — it stays in the cache for later runs.

        en_sentences = [self.translate(s) for s in es_sentences]
        return es_sentences, en_sentences

    def _translate_one(self, text: str) -> str:
        last_err = None
        for delay in [0, *self._backoffs]:
            if delay:
                time.sleep(delay)
            try:
                result = self._client.translate(text)
                if result is None:
                    raise RuntimeError("translator returned None")
                return result
            except Exception as e:
                last_err = e
                continue
        print(f"[warn] giving up on sentence ({last_err}): {text[:80]}…", file=sys.stderr)
        return f"[untranslated] {text}"

    @staticmethod
    def _chunk_long(text: str, limit: int) -> list[str]:
        chunks, buf = [], []
        size = 0
        for piece in re.split(r"(?<=[.!?…])\s+", text):
            piece_len = len(piece) + 1
            if size + piece_len > limit and buf:
                chunks.append(" ".join(buf))
                buf, size = [], 0
            buf.append(piece)
            size += piece_len
        if buf:
            chunks.append(" ".join(buf))
        return chunks


# --- XHTML processing -------------------------------------------------------
def _paragraph_text(p) -> str:
    """Get the textual content of a <p>, flattening inline tags."""
    return p.get_text(" ", strip=True)


def _replace_paragraph_contents(p, sentences_es: list[str], sentences_en: list[str], soup) -> None:
    """Replace a <p>'s children with interleaved es/en <span> blocks."""
    p.clear()
    for es, en in zip(sentences_es, sentences_en):
        span_es = soup.new_tag("span", **{"class": "es"})
        span_es.string = es
        p.append(span_es)
        span_en = soup.new_tag("span", **{"class": "en"})
        span_en.string = en
        p.append(span_en)


def process_xhtml(data: bytes, translator: Translator, progress: tqdm) -> bytes:
    soup = BeautifulSoup(data, "lxml-xml")
    body = soup.find("body")
    if body is None:
        return data
    for p in body.find_all("p"):
        text = _paragraph_text(p)
        if not text:
            continue
        es_sentences, en_sentences = translator.translate_paragraph(text)
        if not es_sentences:
            continue
        _replace_paragraph_contents(p, es_sentences, en_sentences, soup)
        progress.update(len(es_sentences))
    return str(soup).encode("utf-8")


# --- CSS patch -------------------------------------------------------------
_CSS_BLOCK = """

/* translate_epub.py — bilingual overlay */
.es { display: block; }
.en {
    display: block;
    font-style: italic;
    font-size: 0.85em;
    color: #555;
    margin: 0 0 0.6em 0.5em;
}
"""


def patch_css(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    if "translate_epub.py — bilingual overlay" in text:
        return data
    return (text + _CSS_BLOCK).encode("utf-8")


# --- Driver ----------------------------------------------------------------
def _is_text_xhtml(name: str) -> bool:
    return name.startswith("OEBPS/Text/") and name.lower().endswith(".xhtml")


def _count_sentences(data: bytes) -> int:
    soup = BeautifulSoup(data, "lxml-xml")
    body = soup.find("body")
    if body is None:
        return 0
    total = 0
    for p in body.find_all("p"):
        text = _paragraph_text(p)
        total += len(split_sentences_es(text))
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Source Spanish EPUB")
    ap.add_argument("output", type=Path, nargs="?", help="Output EPUB path")
    ap.add_argument(
        "--chapters",
        default="",
        help="Comma-separated XHTML basenames to translate (default: all in OEBPS/Text/ minus --skip).",
    )
    ap.add_argument(
        "--skip",
        default="cubierta.xhtml",
        help="Comma-separated XHTML basenames to NOT translate (default: cubierta.xhtml).",
    )
    args = ap.parse_args()

    input_path: Path = args.input.expanduser().resolve()
    if not input_path.exists():
        ap.error(f"input not found: {input_path}")

    if args.output:
        output_path: Path = args.output.expanduser().resolve()
    else:
        stem = input_path.stem
        output_path = input_path.with_name(f"{stem} -- EN.epub")

    cache_path = input_path.with_name("translate_epub.cache.sqlite3")
    print(f"[info] input:  {input_path.name}")
    print(f"[info] output: {output_path.name}")
    print(f"[info] cache:  {cache_path.name}")

    chapter_filter = {c.strip() for c in args.chapters.split(",") if c.strip()}
    skip_set = {c.strip() for c in args.skip.split(",") if c.strip()}

    cache = TranslationCache(cache_path)
    translator = Translator(cache)

    # Atomic-ish output: write to a tempfile, rename on success.
    tmp_output = output_path.with_suffix(output_path.suffix + ".part")
    if tmp_output.exists():
        tmp_output.unlink()

    with zipfile.ZipFile(input_path, "r") as zin:
        # Pre-pass: count sentences so the progress bar is meaningful.
        print("[info] counting sentences (pre-pass)…")
        total_sentences = 0
        targets: list[str] = []
        for info in zin.infolist():
            if not _is_text_xhtml(info.filename):
                continue
            base = os.path.basename(info.filename)
            if chapter_filter and base not in chapter_filter:
                continue
            if base in skip_set:
                continue
            targets.append(info.filename)
            total_sentences += _count_sentences(zin.read(info.filename))
        print(f"[info] {len(targets)} XHTML files, {total_sentences} sentences to translate")

        progress = tqdm(total=total_sentences, unit="sent", smoothing=0.1)

        with zipfile.ZipFile(tmp_output, "w", zipfile.ZIP_DEFLATED) as zout:
            # EPUB spec: 'mimetype' must be first and stored uncompressed.
            if "mimetype" in zin.namelist():
                mimetype_data = zin.read("mimetype")
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                zout.writestr(info, mimetype_data)

            for info in zin.infolist():
                if info.filename == "mimetype":
                    continue
                data = zin.read(info.filename)
                if info.filename in targets:
                    base = os.path.basename(info.filename)
                    data = process_xhtml(data, translator, progress)
                    progress.write(f"[done] {base}")
                elif info.filename.endswith("Styles/style.css"):
                    data = patch_css(data)
                # Preserve original ZipInfo attributes (date, perms) but write fresh.
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr
                zout.writestr(new_info, data)

        progress.close()

    tmp_output.replace(output_path)
    cache.close()
    print(f"[ok] wrote {output_path}")


if __name__ == "__main__":
    main()
