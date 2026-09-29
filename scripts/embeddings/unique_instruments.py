"""Keep one instrument per name in the id-keyed collections (issue #259).

The SCJN does not reform a reglamento or a lineamiento into a new version: it
reissues it under a new `idOrdenamiento` and keeps the old one (issue #220).
So `scjn-reglamentos` holds several instruments with the same name, and the
Atlas would draw each as its own point, counting the same regulation several
times. What the Atlas compares is the **unique** laws, regulations and
guidelines, and this module is the rule that picks them:

* Only `reglamentos` and `lineamientos` are grouped. `leyes` are keyed by
  `abrev`, a curated key, and pass through untouched (`lamn` and `lamni` both
  read "LEY DE AMNISTÍA" and both stay). Names are never compared *across*
  collections.
* Two instruments share a name when they are equal after NFKD, dropping the
  combining marks, collapsing whitespace and casefolding. Nothing looser: no
  fuzzy matching.
* In a group, **the newest original publication date wins**: the date of the
  instrument's own *oldest* snapshot. A tie goes to the larger integer
  `id_ordenamiento`. The newest *latest*-snapshot date is not usable — an
  abrogated instrument's last snapshot can carry the date of the decree that
  replaced it, so it ties often.
* Only instruments that have vectors compete (`prepare_umap_input.py` hands
  in what `legalvec` published), so a text-less instrument can never win a
  group and erase one the Atlas can draw.

A snapshot's `fecha_publicacion` is `DD-MM-YYYY`: it is parsed here and never
compared as a string (`"02-04-2014"` is later than `"30-08-2004"`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

#: The collections whose instruments are reissued under a new id.
GROUPED_COLLECTIONS = ("reglamentos", "lineamientos")


def name_key(nombre: str) -> str:
    """`nombre` folded for comparison: accents, whitespace and case removed."""
    decomposed = unicodedata.normalize("NFKD", nombre or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def parse_date(text: str) -> date:
    """A snapshot's `DD-MM-YYYY` as a date."""
    return datetime.strptime(text, "%d-%m-%Y").date()


def _as_date(value) -> date:
    return value if isinstance(value, date) else parse_date(value)


def select_unique(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split `records` into the ones to keep and the ones dropped.

    Each record carries `coleccion`, `clave`, `nombre` and `first_publication`
    (a `date`, or a `DD-MM-YYYY` string). The kept records are the input's own
    dicts, in their original order; each dropped record is a copy with
    `replaced_by`, the `clave` of the group's winner, added.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        if record["coleccion"] in GROUPED_COLLECTIONS:
            groups.setdefault((record["coleccion"], name_key(record["nombre"])), []).append(record)

    dropped_by_clave: dict[tuple[str, str], str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        winner = max(members, key=lambda r: (_as_date(r["first_publication"]), int(r["clave"])))
        for member in members:
            if member is not winner:
                dropped_by_clave[(member["coleccion"], member["clave"])] = winner["clave"]

    kept: list[dict] = []
    dropped: list[dict] = []
    for record in records:
        replaced_by = dropped_by_clave.get((record["coleccion"], record["clave"]))
        if replaced_by is None:
            kept.append(record)
        else:
            dropped.append({**record, "replaced_by": replaced_by})
    return kept, dropped


def _default_reader(coleccion: str):
    import scjn

    return {
        "reglamentos": scjn.download_scjn_reglamentos_corpus,
        "lineamientos": scjn.download_scjn_lineamientos_corpus,
    }[coleccion]


def first_publication_dates(records: list[dict], *, reader=None, cache_dir=None) -> list[dict]:
    """`records` with `first_publication` added: the date of each id-keyed
    instrument's oldest snapshot in the SCJN corpus.

    `reader(coleccion)` returns the function that reads one instrument's
    corpus by id (default: `scjn.download_scjn_*_corpus`, disk-first). A
    collection that is not grouped is not looked up. A tarball that is not
    cached raises `scjn.AssetNotCached`, whose message names the `scjn download
    --coleccion ...` command that fixes it: a silent fallback would pick
    winners from partial data.
    """
    reader = reader or _default_reader
    out = []
    for record in records:
        if record["coleccion"] not in GROUPED_COLLECTIONS:
            out.append(record)
            continue
        read = reader(record["coleccion"])
        corpus = read(record["clave"], cache_dir=cache_dir)
        snapshots = corpus["snapshots"]
        if not snapshots:
            raise SystemExit(f"{record['coleccion']} {record['clave']} has no snapshots")
        first = min(parse_date(s["fecha_publicacion"]) for s in snapshots)
        out.append({**record, "first_publication": first})
    return out
