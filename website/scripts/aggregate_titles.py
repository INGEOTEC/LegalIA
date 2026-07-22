"""Aggregate the titles of every DOF note into the site's per-year CSVs.

Downloads every asset of the `notas-archivo` GitHub release straight into
memory (via `dofjson.titulos.listar_assets`) and writes small CSV files to
website/data/, which are what the Quarto site's pages consume. Nothing
downloaded touches disk, so there is no local raw archive to keep around or
to fall out of date: each run reflects every note the release has, up to
its most recent monthly asset.

Usage:
    python website/scripts/aggregate_titles.py [--out website/data]
"""

import argparse
import csv
import io
import json
import re
import tarfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

import requests
from dofjson.titulos import listar_assets

EDITIONS = {
    "NotasMatutinas": "morning",
    "NotasVespertinas": "evening",
    "NotasExtraordinarias": "extraordinary",
}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)",
    "Accept": "application/vnd.github+json",
}

# Minimal Spanish stopword list, plus frequent functional tokens in DOF titles
# that add nothing to the lexical analysis ("ref" is the administrative folio
# "REF:NNNNNN" carried by procurement and notice titles).
STOPWORDS = set(
    """a al algo ante antes como con contra cual cuales cuando de del desde
    donde dos durante e el ella ellas ellos en entre era es esa ese eso esta
    estas este esto estos fue ha han hasta la las le les lo los mas más me mi
    mediante muy no nos o os otra otras otro otros para pero por que qué se
    ser si sin sobre son su sus tras un una unas uno unos y ya ref""".split()
)

WORD_RE = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def first_word(title: str) -> str:
    m = WORD_RE.search(title)
    return strip_accents(m.group(0).upper()) if m else ""


def tokens(title: str):
    for m in WORD_RE.finditer(title.lower()):
        word = m.group(0)
        if len(word) > 2 and word not in STOPWORDS:
            yield strip_accents(word)


def dias_de_tgz(contenido: bytes):
    """Yield (year, [(edition, titulo, heading), ...]) for every day-index file
    inside a notas-YYYY[-MM].tgz, reading it straight out of `contenido` in
    memory.

    The year comes from the member's own path (e.g. "1980/02011980-notas.json"
    -> 1980) rather than each note's `fecha` field: the two occasionally
    disagree (a handful of notes carry a `fecha` from the neighbouring year),
    and grouping by the day-file's own folder is what the local-archive walk
    this replaces has always done.
    """
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            year = int(member.name.split("/")[0])
            dia = json.load(tar.extractfile(member))
            notas = []
            for key, edition in EDITIONS.items():
                for nota in dia.get(key) or []:
                    title = (nota.get("titulo") or "").strip()
                    if not title:
                        continue
                    heading = (nota.get("nombreCodOrgaUno") or "").strip().upper()
                    notas.append((edition, title, heading or "(NO HEADING)"))
            yield year, notas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=Path("website/data"), type=Path)
    parser.add_argument("--since", default=1917, type=int)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    notes_year_ed = Counter()  # (year, edition) -> notes
    days_with_notes = Counter()  # year -> days with at least one note
    len_chars = defaultdict(list)  # year -> title lengths in characters
    len_words = defaultdict(list)  # year -> title lengths in words
    first_words = Counter()  # (year, first word) -> notes
    headings = Counter()  # (year, top-level heading) -> notes
    terms = Counter()  # (decade, term) -> occurrences

    assets = listar_assets()
    for i, asset in enumerate(assets, 1):
        response = requests.get(asset["url"], headers=_HEADERS, timeout=60)
        response.raise_for_status()
        n = 0
        for year, notas in dias_de_tgz(response.content):
            if year < args.since:
                continue
            if notas:
                days_with_notes[year] += 1
            for edition, title, heading in notas:
                n += 1
                notes_year_ed[year, edition] += 1
                len_chars[year].append(len(title))
                words = list(tokens(title))
                len_words[year].append(len(WORD_RE.findall(title)))
                first_words[year, first_word(title)] += 1
                headings[year, heading] += 1
                decade = year - year % 10
                for word in words:
                    terms[decade, word] += 1
        print(f"[{i}/{len(assets)}] {asset['name']}: {n} notas")

    def write(name: str, header, rows):
        path = args.out / name
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print(f"-> {path}")

    write(
        "notes_per_year.csv",
        ["year", "edition", "notes"],
        [(y, e, n) for (y, e), n in sorted(notes_year_ed.items())],
    )
    write(
        "days_with_notes.csv",
        ["year", "days"],
        sorted(days_with_notes.items()),
    )
    write(
        "title_length.csv",
        ["year", "notes", "chars_mean", "chars_median", "words_mean", "words_median"],
        [
            (
                y,
                len(len_chars[y]),
                round(mean(len_chars[y]), 2),
                median(len_chars[y]),
                round(mean(len_words[y]), 2),
                median(len_words[y]),
            )
            for y in sorted(len_chars)
        ],
    )

    # First words: keep, per year, the 30 most frequent overall; fold the rest
    # into OTHER to keep the CSV small.
    total_first = Counter()
    for (_, word), n in first_words.items():
        total_first[word] += n
    top_first = {w for w, _ in total_first.most_common(30)}
    first_rows = Counter()
    for (year, word), n in first_words.items():
        first_rows[year, word if word in top_first else "OTHER"] += n
    write(
        "first_words.csv",
        ["year", "word", "notes"],
        [(y, w, n) for (y, w), n in sorted(first_rows.items())],
    )

    write(
        "headings.csv",
        ["year", "heading", "notes"],
        [(y, h, n) for (y, h), n in sorted(headings.items())],
    )

    term_rows = []
    for decade in sorted({d for d, _ in terms}):
        top = Counter({t: n for (d, t), n in terms.items() if d == decade})
        term_rows.extend((decade, t, n) for t, n in top.most_common(60))
    write("terms_by_decade.csv", ["decade", "term", "count"], term_rows)


if __name__ == "__main__":
    main()
