"""Aggregate the titles of the local DOF note archive (notas-archivo/).

Walks the daily JSON files downloaded by scripts/descargar_notas.py and writes
small CSV files to website/data/, which are what the Quarto site's pages
consume.  The raw archive (notas-archivo/) is never committed; these
aggregates are, so the site can be rebuilt without the raw data.

Usage:
    python website/scripts/aggregate_titles.py [--archive notas-archivo] [--out website/data]
"""

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

EDITIONS = {
    "NotasMatutinas": "morning",
    "NotasVespertinas": "evening",
    "NotasExtraordinarias": "extraordinary",
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default="notas-archivo", type=Path)
    parser.add_argument("--out", default="website/data", type=Path)
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

    years = sorted(
        p for p in args.archive.iterdir() if p.is_dir() and int(p.name) >= args.since
    )
    for year_dir in years:
        year = int(year_dir.name)
        for path in sorted(year_dir.glob("*-notas.json")):
            with open(path) as fh:
                day = json.load(fh)
            any_notes = False
            for key, edition in EDITIONS.items():
                for note in day.get(key) or []:
                    title = (note.get("titulo") or "").strip()
                    if not title:
                        continue
                    any_notes = True
                    notes_year_ed[year, edition] += 1
                    len_chars[year].append(len(title))
                    words = list(tokens(title))
                    len_words[year].append(len(WORD_RE.findall(title)))
                    first_words[year, first_word(title)] += 1
                    heading = (note.get("nombreCodOrgaUno") or "").strip().upper()
                    headings[year, heading or "(NO HEADING)"] += 1
                    decade = year - year % 10
                    for word in words:
                        terms[decade, word] += 1
            if any_notes:
                days_with_notes[year] += 1
        print(f"{year}: {sum(v for (y, _), v in notes_year_ed.items() if y == year):,} notes")

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
