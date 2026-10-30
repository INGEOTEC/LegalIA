#!/usr/bin/env python3
"""Classify every `numbering_gap` of the corpora (review fix-6 of #291).

A `numbering_gap` (`export_instrument_files.excerpt_flags`) is an article whose
integer number is not the previous integer number of its own container plus one.
It is either a *parse miss* -- `md2akn` lost an article the API serves -- or a
fact about the source. Every flagged article of every current instrument
(`scjn.iter_current_*`, the same corpora `test_units_release_sweep.py` sweeps) is
looked at against the API's own blocks, the markers between the previous article
and this one, and put in exactly one class:

* `source-omits-number`: the API serves no block between the two articles --
  a text that prints only the amended or the commented articles (an amending
  protocol, a list of `MOD`/`ADD` articles), or a repealed one not printed;
* `source-sic`: the SCJN itself marks the source's numbering with `(SIC)` at
  that position (`ARTÍCULO  18 (SIC)`: the source skips or misprints a number;
  `integer_number` cannot read `18 (SIC)`, so the gap shows at the next article);
* `letter-read-as-ordinal`: a lettered article (`10 O`) whose capital letter
  `integer_number` takes for the ordinal mark `º`, so it repeats the integer
  before it -- the articles exist;
* `api-order`: the SCJN serves the articles out of order (`ARTÍCULO 1` after `4`);
* `numbering-continues-across-containers`: the numbering goes on through
  sibling sections the API's `other` labels open (`DEL SERVICIO TELEGRÁFICO`),
  and the flag, which reads one container at a time, sees the jump;
* `index-without-marker`: an index with no `Índice` word in it, read as
  structure (`overrides` below).

The output is the fixture `packages/md2akn/tests/fixtures_units/genuine_gaps.json`
the release sweep reads: one entry per flagged article, with the previous and the
flagged number and the raw evidence. A parse miss would not be listed -- it would
be fixed -- so the fixture is the whole of what remains, and the sweep fails on a
gap that is not in it and on an entry that no longer is a gap.

    uv run --group viz python scripts/embeddings/classify_numbering_gaps.py --write
    uv run --group viz python scripts/embeddings/classify_numbering_gaps.py   # summary only

Read-only apart from `--write`; no network.
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_instrument_files as X  # noqa: E402
import instrument_matrix  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from md2akn.outline import lee_marcador  # noqa: E402
from md2akn.patterns import ARTICULO  # noqa: E402
from scjn import (  # noqa: E402
    iter_current_federal_laws,
    iter_current_lineamientos,
    iter_current_reglamentos,
    iter_current_tratados,
)

FIXTURE = ROOT / "packages" / "md2akn" / "tests" / "fixtures_units" / "genuine_gaps.json"

#: `(collection, key) -> (kind, note)` for the instruments whose class a rule over
#: the markers cannot tell.
OVERRIDES = {
    ("tratados", "18141"): (
        "index-without-marker",
        "the block `FIRMAS` lists the amended articles of the ITU Convention as an index "
        "(`PARTE I - Prefacio`, `ARTÍCULO 2 Elecciones...`, `4 El Consejo`, ...) with no "
        "`Índice` word, so rule 15 does not read it as one",
    ),
}

_SIC = re.compile(r"\(\s*sic\s*\)", re.IGNORECASE)
_LETRA = re.compile(r"^\d+\s*[A-Za-z]$")
_MARKER = re.compile(r"^<!-- scjn:block .*? -->[ \t]*$", re.MULTILINE)


def _markers_between(markdown: str, start: int, end: int):
    return [lee_marcador(m.group(0)) or {} for m in _MARKER.finditer(markdown, start, end)]


def _marker_of(markdown: str, offset: int):
    last = None
    for m in _MARKER.finditer(markdown, 0, offset + 1):
        last = lee_marcador(m.group(0)) or {}
    return last or {}


def _block_text_at(markdown: str, offset: int) -> tuple[str, str]:
    """The label and the text of the API block that holds `offset` (marker line left out)."""
    start = label = None
    for m in _MARKER.finditer(markdown, 0, offset + 1):
        start, label = m.end(), (lee_marcador(m.group(0)) or {}).get("label", "")
    if start is None:
        return "", ""
    nxt = _MARKER.search(markdown, start)
    return label, markdown[start:nxt.start() if nxt else len(markdown)]


def _printed_openers(text: str) -> set[int]:
    """The integer article numbers a block's text opens an article with."""
    found = set()
    for line in text.splitlines():
        m = ARTICULO.match(line.replace("**", "").strip())
        if m and re.fullmatch(r"\d+", m.group("num") or ""):
            found.add(int(m.group("num")))
    return found


def classify(collection, key, markdown):
    """The flagged gaps of one instrument, each as a dict with its class and evidence."""
    tree = parse_markdown(markdown)
    units = text_units(tree, split_articles=False)
    repeated = {n.eId for n in tree.walk() if n.repeated}
    flags = X.excerpt_flags(units, set(), repeated)
    previous, previous_unit, found = {}, {}, []
    for unit, names in zip(units, flags):
        if unit.unit_type != "article" or instrument_matrix.is_transitorio_path(unit.path) \
                or unit.num is None:
            continue
        value = X.integer_number(unit.num)
        if value is None:
            continue
        before = previous.get(unit.path)
        if "numbering_gap" in names:
            prev_unit = previous_unit[unit.path]
            between = _markers_between(markdown, prev_unit.end_char, unit.start_char + 1)
            own = _marker_of(markdown, unit.start_char)
            labels = [m.get("label", "") for m in between]
            sic = [lab for lab in [*labels, own.get("label", ""), own.get("num", "")] if _SIC.search(lab)]
            articles = [m for m in between if m.get("kind") in ("article", "provision") and m is not own]
            override = OVERRIDES.get((collection, key))
            if override:
                kind, note = override
            elif sic or _SIC.search(markdown[unit.start_char:unit.start_char + 60]):
                kind, note = "source-sic", f"the SCJN's own label carries (SIC): {sic or [own.get('label')]}"
            elif _LETRA.match(str(unit.num)) and value == before:
                kind, note = ("letter-read-as-ordinal",
                              f"`{unit.num}` is a lettered article; `integer_number` reads its letter as the "
                              f"ordinal mark and gets {value}, the integer before it")
            elif value < before:
                kind, note = "api-order", f"the API serves article {unit.num} after article {before}"
            elif articles:
                kind, note = ("numbering-continues-across-containers",
                              f"{len(articles)} article block(s) of the API lie between the two, in other "
                              f"containers: {[m.get('num') for m in articles][:8]}")
            else:
                kind, note = ("source-omits-number",
                              f"no article block of the API between article {before} and {unit.num}"
                              + (f" (blocks between: {labels})" if labels else ""))
                # Issue #300: when both articles are printed inside one API block (a
                # `CONTENIDO` block holds a whole text), "no article block between"
                # proves nothing; look at what that block's own text opens.
                label, text = _block_text_at(markdown, unit.start_char)
                if text and _marker_of(markdown, prev_unit.start_char) == own and isinstance(value, int):
                    skipped = list(range(before + 1, value))
                    printed = _printed_openers(text)
                    inside = [n for n in skipped if n in printed]
                    if inside:
                        kind, note = ("numbering-continues-across-containers",
                                      f"the API block `{label}` prints articles {inside} of the skipped range "
                                      f"{before + 1}..{value - 1} in other sections of its own text")
                    else:
                        kind, note = ("source-omits-number",
                                      f"the API block `{label}` prints article openers {sorted(printed)[:12]} and "
                                      f"none of the skipped {before + 1}..{value - 1}")
            found.append({
                "collection": collection, "key": str(key), "eId": unit.eId,
                "after": before, "num": str(unit.num), "kind": kind, "evidence": note,
            })
        previous[unit.path] = value
        previous_unit[unit.path] = unit
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help=f"write {FIXTURE.relative_to(ROOT)}")
    args = p.parse_args(argv)
    corpora = (("leyes", "slug", iter_current_federal_laws),
               ("reglamentos", "id_ordenamiento", iter_current_reglamentos),
               ("lineamientos", "id_ordenamiento", iter_current_lineamientos),
               ("tratados", "id_ordenamiento", iter_current_tratados))
    entries = []
    for collection, key, iterator in corpora:
        for instrument in iterator():
            entries.extend(classify(collection, instrument[key], instrument["markdown"]))
    kinds = collections.Counter(e["kind"] for e in entries)
    instruments = {(e["collection"], e["key"]) for e in entries}
    print(f"{len(entries)} flagged articles in {len(instruments)} instruments")
    for kind, n in kinds.most_common():
        print(f"  {kind}: {n} in {len({(e['collection'], e['key']) for e in entries if e['kind'] == kind})} instruments")
    if args.write:
        FIXTURE.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("wrote", FIXTURE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
