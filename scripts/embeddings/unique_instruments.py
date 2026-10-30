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
* Treaties (`tratados`) are not grouped: measured on the live `scjn-tratados`
  index (2026-10-12), 0 of its 1,373 names repeat under the folding below, so
  the rule has nothing to do for them (issue #282).
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

* **Identical texts (review fix-2 of #300 and #301), for every collection.** The SCJN
  files one treaty under several ids when a later instrument supersedes it
  (`tratados/11151`, `16145` and `23742`: the 1950 Convention that replaced the
  1921 and 1933 ones is the *latest* snapshot of all three, so their current
  texts are one text). Instruments whose compared excerpts -- the unit rows
  the matrix searches: no heading, table of contents, transitorio or envelope --
  are the same multiset of `text_sha1` form a group, and the one kept is the one
  whose **original publication** is that text, that is, whose *oldest* snapshot is
  word for word its newest (the body, once the provenance header and the outline
  markers are stripped). A tie, and a group in which no member qualifies, go to the
  larger id (an integer where the key is one). An instrument with no compared
  excerpt at all is never grouped. Run after the same-name rule, on what it kept.

A snapshot's `fecha_publicacion` is `DD-MM-YYYY`: it is parsed here and never
compared as a string (`"02-04-2014"` is later than `"30-08-2004"`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

#: The collections whose instruments are reissued under a new id. Treaties are
#: not among them: measured on the live `scjn-tratados` index (2026-10-12),
#: 0 of its 1,373 names repeat under this folding (issue #282).
GROUPED_COLLECTIONS = ("reglamentos", "lineamientos")

#: The collections the identical-text rule is applied to: all of them.
IDENTICAL_COLLECTIONS = ("leyes", "reglamentos", "lineamientos", "tratados")

REASON_SAME_NAME = "same-name"
REASON_IDENTICAL_TEXT = "identical-text"


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
            dropped.append({**record, "replaced_by": replaced_by, "reason": REASON_SAME_NAME})
    return kept, dropped


def _id_key(clave: str):
    """A key's order: an integer id as an integer, a slug as its string."""
    return (0, int(clave), "") if str(clave).isdigit() else (1, 0, str(clave))


def snapshot_body(markdown: str) -> str:
    """A snapshot with its provenance header and its outline markers removed: what is the
    instrument's text and nothing the SCJN or this project says about it."""
    import re

    text = markdown
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    text = re.sub(r"^<!-- scjn:block .*? -->[ \t]*\n(?:[ \t]*\n)?", "", text, flags=re.MULTILINE)
    return text.strip()


def original_is_current(snapshots: list[dict]) -> bool:
    """Whether an instrument's oldest snapshot is word for word its newest: its original
    publication *is* the text it has now. One snapshot is trivially so."""
    ordered = sorted(snapshots, key=lambda s: parse_date(s["fecha_publicacion"]))
    return snapshot_body(ordered[0]["markdown"]) == snapshot_body(ordered[-1]["markdown"])


def select_identical(records: list[dict], *, reader=None, cache_dir=None) -> tuple[list[dict], list[dict]]:
    """Split `records` into the ones to keep and the ones dropped because another instrument
    of the same collection has the same compared text.

    Each record carries `coleccion`, `clave` and `compared`, the `text_sha1` of every
    compared excerpt (a list, repeats kept: the multiset is the identity). Records with
    no compared excerpt are never grouped. Each dropped record is a copy with
    `replaced_by` (the group's winner), `reason` and `first_publication`. `reader(coleccion)`
    returns the function that reads one instrument's corpus by key (default: the
    disk-first `scjn.download_scjn_*_corpus`), and is called only for the members of a
    group.
    """
    reader = reader or _default_reader
    groups: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    for record in records:
        compared = record.get("compared") or []
        if compared:
            groups.setdefault((record["coleccion"], tuple(sorted(compared))), []).append(record)

    dropped_by_clave: dict[tuple[str, str], tuple[str, date]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        snapshots = {
            m["clave"]: reader(m["coleccion"])(m["clave"], cache_dir=cache_dir)["snapshots"]
            for m in members
        }
        originals = [m for m in members if original_is_current(snapshots[m["clave"]])]
        winner = max(originals or members, key=lambda r: _id_key(r["clave"]))
        for member in members:
            if member is not winner:
                first = min(parse_date(s["fecha_publicacion"]) for s in snapshots[member["clave"]])
                dropped_by_clave[(member["coleccion"], member["clave"])] = (winner["clave"], first)

    kept: list[dict] = []
    dropped: list[dict] = []
    for record in records:
        hit = dropped_by_clave.get((record["coleccion"], record["clave"]))
        if hit is None:
            kept.append(record)
        else:
            dropped.append({**record, "replaced_by": hit[0], "reason": REASON_IDENTICAL_TEXT,
                            "first_publication": hit[1]})
    return kept, dropped


def _default_reader(coleccion: str):
    import scjn

    return {
        "leyes": scjn.download_scjn_leyes_corpus,
        "reglamentos": scjn.download_scjn_reglamentos_corpus,
        "lineamientos": scjn.download_scjn_lineamientos_corpus,
        "tratados": scjn.download_scjn_tratados_corpus,
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
