#!/usr/bin/env python3
"""Re-crawl every snapshot of one SCJN corpus with the Markdown writer that
keeps the API's outline (review fix-2 of #291), without ever leaving an
instrument half-converted.

`scjn.api.articulos_a_markdown(..., outline=True)` writes a marker line before
each block the API returns (`scjn.outline`), so a snapshot on disk now says
where its toc, containers, reform decrees and articles are instead of making
`md2akn` guess. The marker lives in the *body*; nothing else changes, so this
script **keeps the provenance header of a snapshot that already exists**
verbatim and only replaces what follows it. A reform the SCJN published since
the last crawl is new: it gets a header of its own.

It follows issue #255's procedure -- the latest answer of the SCJN, every
snapshot, `estado.json`/`indice.json`/`notas/` never touched, no
`--reintenta` -- with one safeguard `fetch_scjn_legislacion.py` does not
need: an instrument is first crawled into a staging directory and swapped in
only when **every snapshot already on disk came back**. A transient failure
(the API answers HTTP 500 for a reform now and then) would otherwise delete a
snapshot that was there; here the instrument is recorded as `incomplete` in
the ledger and retried on the next run, with the old files untouched.

Resumable. ``<outdir>/<coleccion>/.recrawl-outline.jsonl`` (or ``--ledger NAME``) gets one line per
instrument attempted (``done`` or ``incomplete``, its counts and seconds); a
second run skips every ``done`` key. What the API answered is kept, gzipped,
under ``--raw-dir`` (default ``<outdir>/raw-api``), so the writer can change
again without a second crawl: ``scripts/rewrite_scjn_from_raw.py``.

    ./scripts/recrawl_scjn_outline.py --coleccion tratados --limit 50
    ./scripts/recrawl_scjn_outline.py --coleccion leyes --instrumento ccom   # again, even if done

Since issue #300 the API client refuses an incomplete answer (`orden` gap, fewer
rows than `tamanio`), so a reform that still comes back partial after its
retries is not written: the instrument is ``incomplete`` and keeps its old files.

Nothing is published and no release asset is touched: packaging
(`empaqueta_scjn_leyes.py`/`empaqueta_scjn_coleccion.py`) is the step after.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "scripts"))
sys.path.insert(0, str(_RAIZ / "packages" / "nota2md"))
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

import fetch_scjn_legislacion as fetch  # noqa: E402
from scjn import api as scjn_api  # noqa: E402
from scjn.cache import COLECCIONES_POR_ID  # noqa: E402
from scjn.catalog import search_name, slug_instrumento  # noqa: E402
from scjn.state import lee_estado  # noqa: E402

LEDGER = ".recrawl-outline.jsonl"


def instruments(outdir: Path, coleccion: str) -> list[tuple[str, str, str]]:
    """``(key, directory name, name to search/write)`` for every instrument of
    `coleccion`, sorted the way `fetch_scjn_legislacion.py` sorts them."""
    if coleccion == "leyes":
        return [
            (slug_instrumento(e), slug_instrumento(e), search_name(e))
            for e in fetch._load_catalog(outdir)
        ]
    return [
        (e["id_ordenamiento"], e["id_ordenamiento"], e["nombre"])
        for e in fetch._load_catalog_por_id(outdir, coleccion)
    ]


def done_keys(base: Path, name: str = LEDGER) -> set[str]:
    """Every key a ledger of `base` (the plain one, and the ``--part`` ones)
    records as ``done``, a later failed attempt cancelling an earlier success."""
    keys = set()
    for ledger in sorted(base.glob(f"{name}*")):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row["status"] == "done":
                    keys.add(row["key"])
                else:
                    keys.discard(row["key"])
    return keys


def split_header(markdown: str) -> tuple[str, str]:
    """A snapshot's provenance header (fence to fence) and its body."""
    header, _, body = markdown.partition("\n\n")
    return header, body


def recrawl(
    api: scjn_api.ScjnApi,
    destino: Path,
    nombre: str,
    id_ordenamiento: str,
    raw_dir: Path,
    attempts: int = 2,
) -> dict:
    """Crawl one instrument into a staging directory and swap it in. Returns
    the ledger row (without `key`/`seconds`)."""
    old = {p.name: p.read_text(encoding="utf-8") for p in destino.glob("*.md")}
    stage = destino.parent.parent / ".recrawl-stage" / f"{destino.parent.name}-{destino.name}"
    for attempt in range(1, attempts + 1):
        shutil.rmtree(stage, ignore_errors=True)
        resultado = scjn_api.descarga_ordenamiento(
            api, nombre, stage, on_progreso=fetch._imprime_avance,
            id_ordenamiento=id_ordenamiento, raw_dir=raw_dir,
        )
        new = {p.name for p in stage.glob("*.md")}
        missing = sorted(set(old) - new)
        if not missing:
            break
        print(f"  attempt {attempt}: {len(missing)} snapshot(s) did not come back: {missing[:5]}",
              file=sys.stderr)
    row = {
        "snapshots": len(new),
        "reformas": resultado.total_reformas,
        "sin_articulos": len(resultado.reformas_sin_articulos),
        "fallidas": resultado.reformas_fallidas,
        "new_names": sorted(new - set(old)),
    }
    if missing:
        shutil.rmtree(stage, ignore_errors=True)
        return {**row, "status": "incomplete", "missing": missing}
    for name in sorted(new):
        header, body = split_header((stage / name).read_text(encoding="utf-8"))
        if name in old:
            header = split_header(old[name])[0]
        tmp = destino / f"{name}.tmp"
        tmp.write_text(f"{header}\n\n{body}", encoding="utf-8")
        tmp.replace(destino / name)
    shutil.rmtree(stage, ignore_errors=True)
    return {**row, "status": "done"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--coleccion", choices=("leyes", *COLECCIONES_POR_ID), required=True)
    p.add_argument("--outdir", type=Path, default=Path("scripts/scjn"))
    p.add_argument("--raw-dir", type=Path, default=None)
    p.add_argument("--espera", type=float, default=scjn_api.ESPERA_DEFAULT)
    p.add_argument("--limit", type=int, default=None, help="stop after this many instruments")
    p.add_argument("--instrumento", action="append", default=None, help="only these keys")
    p.add_argument("--ledger", default=LEDGER, metavar="NAME",
                   help="ledger file name under the collection's directory; a fresh name "
                        "re-crawls every instrument again (issue #300 used "
                        ".recrawl-contenido.jsonl). Default: %(default)s")
    p.add_argument("--part", default=None, metavar="I/N",
                   help="only every N-th instrument starting at I (0-based), to run N processes")
    args = p.parse_args(argv)

    raw_root = (args.raw_dir or args.outdir / "raw-api") / args.coleccion
    base = args.outdir / args.coleccion
    ledger = base / args.ledger
    todo = instruments(args.outdir, args.coleccion)
    if args.part:
        i, n = (int(x) for x in args.part.split("/"))
        todo = [t for k, t in enumerate(todo) if k % n == i]
        ledger = base / f"{args.ledger}.part{i}of{n}"
    seen = done_keys(base, args.ledger)
    if args.instrumento:
        todo = [t for t in todo if t[0] in set(args.instrumento)]
        # Naming an instrument asks for it again, whatever the ledger says.
        seen -= set(args.instrumento)
    api = scjn_api.ScjnApi(espera=args.espera)
    attempted = failed = 0
    started = time.time()
    for k, (key, dirname, nombre) in enumerate(todo, 1):
        if key in seen:
            continue
        if args.limit is not None and attempted >= args.limit:
            break
        attempted += 1
        t0 = time.time()
        print(f"[{args.coleccion} {k}/{len(todo)}] {key}: {nombre[:80]}", file=sys.stderr)
        destino = base / dirname
        id_ordenamiento = lee_estado(destino).get("id_ordenamiento")
        try:
            row = recrawl(api, destino, nombre, id_ordenamiento, raw_root / dirname)
        except Exception as exc:  # the SCJN is down, a timeout: keep going
            row = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(f"  error: {row['error']}", file=sys.stderr)
        row.update(key=key, seconds=round(time.time() - t0, 1))
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if row["status"] != "done":
            failed += 1
    print(
        f"{args.coleccion}: {attempted} attempted, {failed} not done, "
        f"{time.time() - started:.0f} s",
        file=sys.stderr,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
