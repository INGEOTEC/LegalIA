#!/usr/bin/env python3
"""Verify the contract between the SCJN Markdown writer and its readers
(issue #303), over the four local corpora and their raw API answers. Read-only:
no network, nothing under ``scripts/scjn/`` is opened for writing; the only
files written are the ones named by ``--json`` and ``--write-vocabulary``.

Since review fix-2 of #291 every snapshot carries one marker line,
``<!-- scjn:block orden=N numero=N kind=<kind> [type=] [num=] label="..." -->``,
before each API block that yields text (`scjn.outline`), and the raw answer of
every reform is kept beside it (``scripts/scjn/raw-api/<c>/<key>/<snapshot>.json.gz``).
For each ``scripts/scjn/<c>/<key>/<snapshot>.md`` this script reports four things:

1. **Writer drift.** The body rebuilt from the raw answer with
   ``articulos_a_markdown(articulos_de_crudo(raw), outline=True)`` against the body
   on disk (the provenance header kept, as ``rewrite_scjn_from_raw.py`` does):
   ``identical``, ``differs`` or ``no-raw`` (no raw answer was kept). Drift is a
   finding for the repository owner, never a failure: nothing is rewritten.
2. **Marker completeness.** Every raw block that yields text has exactly one marker
   with its ``orden``; every ``orden`` with no marker is a block that wrote no text
   (a block whose whole text is an editorial ``N. DE E.`` aside, the *explained* gap
   of ``measure_block_gaps.py``, whose predicate is imported, not copied). A gap the
   raw answer does not explain, or one with no raw answer, is *unexplained*.
3. **Attribute round-trip.** ``parse_marker(marker_line(orden, numero, label))`` equals
   the attributes on disk and ``kind``/``type``/``num``/``label`` equal
   ``classify(label)``. A violation names the collection, key, snapshot and ``orden``.
   Where a raw answer exists the marker's ``orden``, ``numero`` and ``label`` are also
   compared with the raw block's (``mismatch`` column): that is drift made visible
   (a snapshot older than its raw answer), reported, never a violation.
4. **Text invariance.** ``strip_markers`` of the rebuilt body equals
   ``articulos_a_markdown(..., outline=False)`` of the same raw blocks.

Exit status 1 only on a contract violation (a round-trip or an invariance one), never
on drift, ``no-raw`` or a gap.

    ./scripts/verify_scjn_outline.py                          # all four collections
    ./scripts/verify_scjn_outline.py --coleccion tratados --jobs 4 --json /tmp/v.json
    ./scripts/verify_scjn_outline.py --compare output/outline-before.json
    ./scripts/verify_scjn_outline.py --guard                  # audit the completeness guard
    ./scripts/verify_scjn_outline.py --write-vocabulary \\
        packages/md2akn/tests/fixtures_units/marker_vocabulary.json

``--guard`` audits ``scjn.api.completeness_defects`` and ``_sin_filas_repetidas`` over every
raw answer kept (what a raw file does not record is the ``tamanio`` the API declared,
so the short-count condition is not checkable from it). ``--write-vocabulary`` writes the
marker vocabulary both ``scjn`` and ``md2akn`` tests read (see ``build_vocabulary``).
"""

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_block_gaps import is_explained, ledger_status, missing_orden, served_orden  # noqa: E402
from scjn.api import (  # noqa: E402
    Articulo,
    _sin_filas_repetidas,
    articulos_a_markdown,
    articulos_de_crudo,
    completeness_defects,
)
from scjn.cache import COLECCIONES_POR_ID  # noqa: E402
from scjn.outline import (  # noqa: E402
    CONTAINER_TYPES,
    KINDS,
    MARKER,
    MARKER_PREFIX,
    classify,
    marker_line,
    parse_marker,
    strip_markers,
)

COLLECTIONS = ("leyes", *COLECCIONES_POR_ID)

#: How many violations of each class a collection keeps in the full list.
_SHOWN = 20


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None


def _split(text: str) -> tuple[str, str]:
    """``(header, body)`` of a snapshot, split the way `rewrite_scjn_from_raw.py` does."""
    header, _, body = text.partition("\n\n")
    return header, body


def rebuild(blocks: list[Articulo]) -> tuple[str, str, list[Articulo]]:
    """``(body with markers, body without, blocks that yield text)`` rebuilt from raw
    blocks. A block yields text when the writer, run on it alone, writes anything: the
    same blocks the marker is attached to, found without reading a marker. Each block is
    converted on its own and the pieces are joined the way `articulos_a_markdown` joins
    its paragraphs, so the result is the writer's own output for the whole answer."""
    plain: list[str] = []
    outlined: list[str] = []
    with_text: list[Articulo] = []
    for block in blocks:
        piece = articulos_a_markdown([block], outline=False)
        if piece == "\n":
            continue
        with_text.append(block)
        plain.append(piece[:-1])
        outlined.append(articulos_a_markdown([block], outline=True)[:-1])
    return "\n\n".join(outlined) + "\n", "\n\n".join(plain) + "\n", with_text


def verify_snapshot(path: Path, raw_path: Path) -> dict:
    """Every check of one snapshot, as a small picklable record (see the module
    docstring). `raw_path` need not exist."""
    key, snapshot = path.parent.name, path.name
    _, body = _split(path.read_text(encoding="utf-8"))
    lines = [line for line in body.split("\n") if line.startswith(MARKER_PREFIX)]
    markers = [(line, parse_marker(line)) for line in lines]
    out: dict = {
        "key": key, "snapshot": snapshot, "markers": len(markers),
        "kinds": Counter(), "types": Counter(),
        "round_trip": [], "invariance": [], "unmarked_with_text": [], "extra_markers": [],
        "block_mismatch": [],
    }
    for line, attrs in markers:
        if attrs is None:
            out["round_trip"].append(f"orden ?: a marker line that does not parse: {line[:80]}")
            continue
        out["kinds"][attrs.get("kind", "?")] += 1
        if "type" in attrs:
            out["types"][attrs["type"]] += 1
        orden = attrs.get("orden", "?")
        label = attrs.get("label", "")
        if marker_line(_int_or_none(attrs.get("orden")), _int_or_none(attrs.get("numero")), label) != line:
            out["round_trip"].append(f"orden {orden}: marker_line(parse_marker(line)) differs from the line")
        classified = classify(label)
        got = {k: attrs[k] for k in ("kind", "type", "num") if k in attrs}
        if got != classified:
            out["round_trip"].append(f"orden {orden}: attributes {got} are not classify({label!r}) = {classified}")

    faltan = missing_orden(body)
    out["gap_orden"] = faltan
    if not raw_path.exists():
        out.update(drift="no-raw", gaps_explained=[], gaps_unexplained=faltan)
        return out

    with gzip.open(raw_path, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    served = served_orden(rows)
    rebuilt, plain, with_text = rebuild(articulos_de_crudo(raw_path))
    out["drift"] = "identical" if rebuilt == body else "differs"
    out.update(
        gaps_explained=[o for o in faltan if is_explained([o], served)],
        gaps_unexplained=[o for o in faltan if not is_explained([o], served)],
    )
    if strip_markers(rebuilt) != plain:
        out["invariance"].append("strip_markers(rebuilt body) != the outline=False body")
    if strip_markers(body) != plain and out["drift"] == "identical":
        out["invariance"].append("strip_markers(body on disk) != the outline=False body of an identical snapshot")

    expected = Counter(b.orden for b in with_text)
    found = Counter(_int_or_none(a.get("orden")) for _, a in markers if a is not None)
    out["unmarked_with_text"] = sorted((expected - found).elements(), key=lambda o: (o is None, o))
    out["extra_markers"] = sorted((found - expected).elements(), key=lambda o: (o is None, o))
    if len(with_text) == len(markers):
        for block, (_, attrs) in zip(with_text, markers):
            if attrs is None:
                continue
            if (("" if block.orden is None else str(block.orden)), str(block.numero if block.numero is not None else ""),
                    block.referencia) != (attrs.get("orden"), attrs.get("numero"), attrs.get("label")):
                # Not a violation: a snapshot written from an older answer than the raw
                # one kept (`drift` is then `differs`) disagrees with it by construction.
                out["block_mismatch"].append(
                    f"orden {block.orden}: the marker says orden={attrs.get('orden')} "
                    f"numero={attrs.get('numero')} label={attrs.get('label')!r}, the raw block "
                    f"{block.orden}/{block.numero}/{block.referencia!r}"
                )
    return out


def _work(args: tuple[str, str]) -> dict:
    path, raw = args
    return verify_snapshot(Path(path), Path(raw))


def verify_collection(outdir: Path, raw_dir: Path, collection: str, jobs: int = 1) -> dict:
    """The four tables of one collection plus the full list of what was found."""
    base = outdir / collection
    work = [
        (str(p), str(raw_dir / collection / p.parent.name / f"{p.stem}.json.gz"))
        for p in sorted(base.glob("*/*.md"))
    ]
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as pool:
            results = list(pool.map(_work, work, chunksize=8))
    else:
        results = [_work(w) for w in work]
    ledger = ledger_status(base, ".recrawl-contenido.jsonl")
    not_done = {k for k, r in ledger.items() if r.get("status") != "done"}
    drift = Counter(r["drift"] for r in results)
    unexplained = [r for r in results if r["gaps_unexplained"]]
    summary = {
        "collection": collection,
        "snapshots": len(results),
        "instruments": len({r["key"] for r in results}),
        "drift": {k: drift.get(k, 0) for k in ("identical", "differs", "no-raw")},
        "markers": sum(r["markers"] for r in results),
        "gaps_explained_snapshots": sum(1 for r in results if r["gaps_explained"]),
        "gaps_explained_blocks": sum(len(r["gaps_explained"]) for r in results),
        "gaps_unexplained_snapshots": len(unexplained),
        "gaps_unexplained_blocks": sum(len(r["gaps_unexplained"]) for r in unexplained),
        "gaps_unexplained_in_ledger_not_done": sorted(
            {r["key"] for r in unexplained if r["key"] in not_done}),
        "gaps_unexplained_outside_ledger": sorted(
            {r["key"] for r in unexplained if r["key"] not in not_done}),
        "unmarked_with_text": sum(len(r["unmarked_with_text"]) for r in results),
        "extra_markers": sum(len(r["extra_markers"]) for r in results),
        "block_mismatches": sum(len(r["block_mismatch"]) for r in results),
        "round_trip_violations": sum(len(r["round_trip"]) for r in results),
        "invariance_violations": sum(len(r["invariance"]) for r in results),
        "kinds": dict(sum((r["kinds"] for r in results), Counter())),
        "types": dict(sum((r["types"] for r in results), Counter())),
    }
    findings = {
        "differs": [f"{r['key']}/{r['snapshot']}" for r in results if r["drift"] == "differs"],
        "no_raw": [f"{r['key']}/{r['snapshot']}" for r in results if r["drift"] == "no-raw"],
        "gaps_unexplained": {f"{r['key']}/{r['snapshot']}": r["gaps_unexplained"] for r in unexplained},
        "gaps_explained": {f"{r['key']}/{r['snapshot']}": r["gaps_explained"]
                           for r in results if r["gaps_explained"]},
        "unmarked_with_text": {f"{r['key']}/{r['snapshot']}": r["unmarked_with_text"]
                               for r in results if r["unmarked_with_text"]},
        "extra_markers": {f"{r['key']}/{r['snapshot']}": r["extra_markers"]
                          for r in results if r["extra_markers"]},
        "block_mismatch": [f"{collection}/{r['key']}/{r['snapshot']}: {m}"
                           for r in results for m in r["block_mismatch"][:3]],
        "round_trip": [f"{collection}/{r['key']}/{r['snapshot']}: {m}" for r in results for m in r["round_trip"]],
        "invariance": [f"{collection}/{r['key']}/{r['snapshot']}: {m}" for r in results for m in r["invariance"]],
    }
    return {"summary": summary, "findings": findings}


# -- the completeness guard, audited -------------------------------------------


def audit_answer(rows: list[dict]) -> dict:
    """What `completeness_defects` and `_sin_filas_repetidas` make of one raw answer.

    A raw file is written after the repeats were dropped and does not record the
    `tamanio` the API declared, so ``total`` is unknown (0 here): a short count is not
    checkable. `shared_orden` counts `orden` values carried by two *different* blocks (real
    and kept, issue #300); `no_orden` the rows with none."""
    filas = [
        Articulo(r.get("numero"), r.get("orden"), (r.get("referencia") or "").strip(), r.get("contenido") or "")
        for r in rows
    ]
    unicas = _sin_filas_repetidas(filas)
    por_orden = Counter(f.orden for f in unicas if f.orden is not None)
    defects = completeness_defects(filas, 0)
    return {
        "rows": len(filas),
        "repeats": len(filas) - len(unicas),
        "shared_orden": sum(1 for n in por_orden.values() if n > 1),
        "no_orden": sum(1 for f in filas if f.orden is None),
        "gaps": bool(defects),
        "defects": defects,
        "refused": bool(defects),
    }


def audit_guard(outdir: Path, raw_dir: Path, collection: str) -> dict:
    """The guard audit of one collection (every raw answer kept)."""
    base = raw_dir / collection
    status = ledger_status(outdir / collection, ".recrawl-contenido.jsonl")
    not_done = {k for k, r in status.items() if r.get("status") != "done"}
    counts = Counter()
    examples: dict[str, list[str]] = {"repeats": [], "shared_orden": [], "no_orden": [], "gaps": []}
    for path in sorted(base.glob("*/*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            res = audit_answer(json.load(f))
        counts["answers"] += 1
        label = f"{path.parent.name}/{path.name.removesuffix('.json.gz')}"
        for name in ("repeats", "shared_orden", "no_orden"):
            if res[name]:
                counts[f"with_{name}"] += 1
                examples[name].append(f"{label} ({res[name]})")
        if res["gaps"]:
            counts["with_gaps"] += 1
            examples["gaps"].append(f"{label}: {res['defects'][0]}")
            counts["refused_today"] += 1
            if path.parent.name in not_done:
                counts["refused_in_ledger_not_done"] += 1
    return {"collection": collection, "counts": dict(counts), "examples": examples,
            "ledger_not_done": sorted(not_done)}


# -- the marker vocabulary fixture ---------------------------------------------

#: One entry per `classify` branch (and a few that pin the quoting): ``(referencia, orden, numero)``.
VOCABULARY_EXAMPLES = (
    ("ENCABEZADO", 1, 0),
    ("CONSIDERANDO", 2, 0),
    ("CONSIDERANDOS", 3, 0),
    ("PREÁMBULO", 4, 0),
    ("ÍNDICE", 5, 0),
    ("TABLA DE CONTENIDO", 6, 0),
    ("TRANSITORIO", 7, 0),
    ("TRANSITORIOS", 8, 0),
    ("ARTÍCULOS TRANSITORIOS", 9, 0),
    ("DISPOSICIONES TRANSITORIAS", 10, 0),
    ("D.O.F. 10 DE NOVIEMBRE DE 2023", 11, 0),
    ("D.O. 23 DE JULIO DE 1949", 12, 0),
    ("LIBRO PRIMERO", 13, 0),
    ("TÍTULO SEGUNDO", 14, 0),
    ("CAPÍTULO ÚNICO", 15, 0),
    ("CAPÍTULO II", 16, 0),
    ("SECCIÓN 3", 17, 0),
    ("APARTADO A", 18, 0),
    ("PARTE 3", 19, 0),
    ("PRIMERA PARTE", 20, 0),
    ("I PARTE", 21, 0),
    ("PARTE FINAL DEL ACUERDO", 22, 0),
    ("ANEXO", 23, 0),
    ("ANEXO 2-A", 24, 0),
    ("ANEXO ÚNICO", 25, 0),
    ("APÉNDICE", 26, 0),
    ("APÉNDICE 1", 27, 0),
    ("ARTÍCULO 6 TER", 28, 6),
    ("ARTICULO  18 BIS 1", 29, 18),
    ("ARTÍCULO PRIMERO", 30, 1),
    ("ARTÍCULO 1.1", 31, 1),
    ("ARTÍCULO I: ENMIENDA", 32, 1),
    ("PRIMERO", 33, 1),
    ("ÚNICO", 34, 1),
    ("DÉCIMO PRIMERO", 35, 11),
    ("1.1.0", 36, 1),
    ("CONTENIDO", 37, 0),
    ("NOTA EXPLICATIVA", 38, 0),
    ('ANEXO "A" \\ B -- C', 39, 0),
    ("ARTÍCULO 7 \\", 40, 7),
)


def build_vocabulary(distribution: dict | None) -> dict:
    """The marker vocabulary, from `scjn.outline` alone, as the JSON fixture
    ``packages/md2akn/tests/fixtures_units/marker_vocabulary.json``.

    ``examples`` is one marker per `classify` branch with the attributes
    `parse_marker` reads back from it, what `md2akn`'s reader has to agree with.
    ``distribution`` is informative (the kinds and types on the markers on disk, per
    collection) and is the only part a test does not regenerate."""
    examples = []
    for referencia, orden, numero in VOCABULARY_EXAMPLES:
        line = marker_line(orden, numero, referencia)
        examples.append({
            "referencia": referencia, "orden": orden, "numero": numero,
            "line": line, "attributes": parse_marker(line),
        })
    line = marker_line(None, None, "ENCABEZADO")
    examples.append({
        "referencia": "ENCABEZADO", "orden": None, "numero": None,
        "line": line, "attributes": parse_marker(line),
    })
    return {
        "_comment": (
            "Generated by scripts/verify_scjn_outline.py --write-vocabulary from scjn.outline; "
            "never edit by hand. `distribution` is informative (the markers on disk when it "
            "was written) and is not compared by any test."
        ),
        "marker_prefix": MARKER_PREFIX,
        "kinds": list(KINDS),
        "container_types": list(CONTAINER_TYPES),
        "examples": examples,
        "distribution": distribution or {},
    }


def marker_distribution(outdir: Path, collections: tuple[str, ...]) -> dict:
    """``{collection: {"snapshots": N, "markers": N, "kind": {...}, "type": {...}}}`` over
    the markers on disk, read with the marker regular expression alone."""
    out = {}
    for collection in collections:
        kinds: Counter = Counter()
        types: Counter = Counter()
        snapshots = 0
        for path in sorted((outdir / collection).glob("*/*.md")):
            snapshots += 1
            for m in MARKER.finditer(path.read_text(encoding="utf-8")):
                attrs = dict(re.findall(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)', m.group("attrs")))
                kinds[attrs.get("kind", "?")] += 1
                if "type" in attrs:
                    types[attrs["type"]] += 1
        out[collection] = {
            "snapshots": snapshots, "markers": sum(kinds.values()),
            "kind": dict(sorted(kinds.items())), "type": dict(sorted(types.items())),
        }
    return out


# -- command line ----------------------------------------------------------------


def _print_table(summaries: list[dict]) -> None:
    print("\nWriter drift (disk body against the body rebuilt from the raw answer)")
    print(f"{'collection':14}{'snapshots':>10}{'instruments':>13}{'identical':>11}{'differs':>9}{'no-raw':>8}")
    for s in summaries:
        d = s["drift"]
        print(f"{s['collection']:14}{s['snapshots']:>10}{s['instruments']:>13}"
              f"{d['identical']:>11}{d['differs']:>9}{d['no-raw']:>8}")
    print("\nMarker completeness")
    print(f"{'collection':14}{'markers':>10}{'explained':>11}{'(blocks)':>10}{'unexplained':>13}"
          f"{'(blocks)':>10}{'unmarked':>10}{'extra':>7}{'mismatch':>10}")
    for s in summaries:
        print(f"{s['collection']:14}{s['markers']:>10}{s['gaps_explained_snapshots']:>11}"
              f"{s['gaps_explained_blocks']:>10}{s['gaps_unexplained_snapshots']:>13}"
              f"{s['gaps_unexplained_blocks']:>10}{s['unmarked_with_text']:>10}{s['extra_markers']:>7}{s['block_mismatches']:>10}")
    print("\nContract (violations; the exit status is non-zero when any is)")
    print(f"{'collection':14}{'round-trip':>12}{'invariance':>12}")
    for s in summaries:
        print(f"{s['collection']:14}{s['round_trip_violations']:>12}{s['invariance_violations']:>12}")


def _print_guard(audits: list[dict]) -> None:
    print("\nCompleteness guard, over every raw answer kept (`tamanio` is not recorded, so a short count is not checkable)")
    print(f"{'collection':14}{'answers':>9}{'repeats':>9}{'shared orden':>14}{'no orden':>10}{'gaps':>6}{'refused today':>15}")
    for a in audits:
        c = a["counts"]
        print(f"{a['collection']:14}{c.get('answers', 0):>9}{c.get('with_repeats', 0):>9}"
              f"{c.get('with_shared_orden', 0):>14}{c.get('with_no_orden', 0):>10}"
              f"{c.get('with_gaps', 0):>6}{c.get('refused_today', 0):>15}")
        for name in ("gaps",):
            for example in a["examples"][name][:5]:
                print(f"    {name}: {a['collection']}/{example}")


def compare(before: list[dict], after: list[dict]) -> bool:
    """Print what changed between two `--json` outputs; True when the writer-drift
    classes of every collection are identical."""
    same = True
    by_name = {r["summary"]["collection"]: r["summary"] for r in before}
    for r in after:
        s = r["summary"]
        b = by_name.get(s["collection"])
        if b is None:
            print(f"{s['collection']}: not in the first file")
            same = False
            continue
        ok = b["drift"] == s["drift"] and b["snapshots"] == s["snapshots"]
        same &= ok
        print(f"{s['collection']:14} drift before {b['drift']} after {s['drift']} -> {'same' if ok else 'DIFFERENT'}")
    return same


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coleccion", choices=COLLECTIONS, action="append", default=None)
    p.add_argument("--outdir", type=Path, default=_ROOT / "scripts" / "scjn")
    p.add_argument("--raw-dir", type=Path, default=None,
                   help="where the raw API answers are kept (default <outdir>/raw-api)")
    p.add_argument("--jobs", type=int, default=1, help="processes (4 is safe on 15 GB)")
    p.add_argument("--json", type=Path, default=None, help="write the full result here")
    p.add_argument("--compare", type=Path, default=None,
                   help="a --json file from an earlier run: exit 1 unless the writer-drift classes agree")
    p.add_argument("--guard", action="store_true", help="audit the completeness guard instead")
    p.add_argument("--write-vocabulary", type=Path, default=None, metavar="PATH",
                   help="write the marker vocabulary fixture and stop")
    args = p.parse_args(argv)
    collections = tuple(args.coleccion or COLLECTIONS)
    raw_dir = args.raw_dir or args.outdir / "raw-api"

    if args.write_vocabulary:
        distribution = marker_distribution(args.outdir, collections)
        args.write_vocabulary.write_text(
            json.dumps(build_vocabulary(distribution), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.write_vocabulary}", file=sys.stderr)
        return 0

    if args.guard:
        audits = [audit_guard(args.outdir, raw_dir, c) for c in collections]
        _print_guard(audits)
        if args.json:
            args.json.write_text(json.dumps(audits, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0

    results = []
    for collection in collections:
        print(f"{collection} ...", file=sys.stderr, flush=True)
        results.append(verify_collection(args.outdir, raw_dir, collection, args.jobs))
    _print_table([r["summary"] for r in results])
    bad = 0
    for r in results:
        s = r["summary"]
        for name in ("round_trip", "invariance"):
            for line in r["findings"][name][:_SHOWN]:
                print(f"  VIOLATION {name}: {line}")
        for key in s["gaps_unexplained_outside_ledger"]:
            print(f"  unexplained gap outside the ledger: {s['collection']}/{key}")
        for key in s["gaps_unexplained_in_ledger_not_done"]:
            print(f"  unexplained gap, instrument not done in the ledger: {s['collection']}/{key}")
        bad += s["round_trip_violations"] + s["invariance_violations"]
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.compare:
        if not compare(json.loads(args.compare.read_text(encoding="utf-8")), results):
            return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
