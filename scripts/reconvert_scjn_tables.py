#!/usr/bin/env python3
"""Re-convert, in place, exactly the SCJN snapshots that contain a tab —
the fix issue #253 gave `scjn.api.articulos_a_markdown` (a table row wrapped
over several lines now comes back as one `| cell | cell |` paragraph
instead of one paragraph per wrapped line) only reaches an already-written
snapshot by asking the SCJN again: the raw `contenido` the API answers is
never cached, so nothing short of a fresh `articulos_of_reforma` call picks
up the new conversion for a law/reglamento/lineamiento that already has one
on disk.

Works over any of the three id-addressable collections, laid out under
``<outdir>/<coleccion>/<key>/*.md`` the way `fetch_scjn_legislacion.py`
already writes them (``key`` is a slug for `leyes`,
`scjn.catalog.slug_instrumento`, and an `id_ordenamiento` otherwise,
`scjn.catalog.instrumento_key`).

Neither existing CLI flag fits this job (both live on
`fetch_scjn_legislacion.py`): ``--instrumento`` only fetches *missing*
reforms, so it would also pull in any new reform published since the crawl
— scope creep, an unlinked snapshot; ``--reintenta`` is `leyes`-only,
deletes every snapshot plus `indice.json`/`estado.json` and re-searches
*without* the recorded `id_ordenamiento` — issue #115's wrong-document path,
for a job that already knows exactly which document and which reform it
wants.

## What this script does instead

Each snapshot's own provenance header already records `id_ordenamiento` and
`reforma_id` (`scjn.header.parse_header`), and `scjn.api.snapshot` is
nothing but ``cabecera(...) + "\\n\\n" + articulos_a_markdown(articulos)``.
So for every ``<key>/*.md`` that contains a tab:

1. split the file into its header (through the closing ``---`` line) and its
   body, exactly the way `snapshot()` joins them;
2. read `id_ordenamiento`/`reforma_id` back out of the header;
3. call `scjn.api.ScjnApi(espera=...).articulos_of_reforma(id_ordenamiento,
   reforma_id)` — one request, no search, no reform table;
4. rebuild the body with `articulos_a_markdown` and compare it, word for
   word, against the old one.

The header is never recomputed (`cabecera()` is never called) — it
describes the reform, not the conversion, and every other sibling
(`estado.json`, `indice.json`, `notas/`, the file name itself) is left
untouched.

## Verification, and why a mismatch is not written

`words(text)` strips `*`, `|` and `\\` (table markup and the bold marker,
neither part of the content) and splits on whitespace. A rewrite only
happens when ``words(old_body) == words(new_body)`` and the new body itself
carries no tab; otherwise the file is left byte-identical and the snapshot
is reported as `mismatch`; with the first differing word position and a
short excerpt of both sides. If the SCJN edited the text upstream since the
snapshot was written, that is a content change beyond this issue, and a
human decides — this script never guesses. An `ScjnApiError` (including the
WAF subclass) is `failed` and does not stop the run; a header missing
either id is `no_header_ids`.

**Resumability comes for free**: a rewritten file has no tab, so re-running
this script over the same collection makes zero requests for anything
already fixed — only newly-tab-bearing snapshots (from a law crawled again
after this ran) would be picked up. The JSON report (`--report`, default
``<outdir>/<coleccion>-reconvert-report.json``) is rewritten after every
instrument, so a killed run still leaves a usable report.

``--control N`` re-converts the first N *tab-less* snapshots (sorted by
instrument, then chronologically) without writing anything, and asserts the
rebuilt ``header + "\\n\\n" + new_body`` is byte-identical to the file on
disk. That is the one way to prove the header/body split and the API still
reproduce an *unaffected* snapshot exactly, before trusting the real pass on
an affected one. Run it before the real pass, on each collection.

    ./scripts/reconvert_scjn_tables.py --coleccion leyes --dry-run
    ./scripts/reconvert_scjn_tables.py --coleccion leyes --control 3
    ./scripts/reconvert_scjn_tables.py --coleccion leyes 2>&1 | tee scripts/scjn/reconvert-leyes.log

## Out of scope

Publishing (issue #115, Hallazgo C — a human runs the `gh release upload`
commands `empaqueta_scjn_leyes.py`/`empaqueta_scjn_coleccion.py` print,
never this script); fetching a new reform (`--instrumento` on
`fetch_scjn_legislacion.py`); re-linking codNotas
(`enlaza_scjn_legislacion.py`); repackaging into a `.tgz` (that is still
`empaqueta_scjn_leyes.py`/`empaqueta_scjn_coleccion.py`, run afterwards, over
only the instruments this script actually rewrote).
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first —
# the same convention `fetch_scjn_legislacion.py` already uses.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import ScjnApi, ScjnApiError, articulos_a_markdown  # noqa: E402
from scjn.header import parse_header, versiones_de_directorio  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "lineamientos")

# A snapshot is `header + "\n\n" + body` (`scjn.api.snapshot`); the header is
# whatever is bounded by the opening and the first closing "---" line, the
# same boundary `scjn.header.parse_header` reads by. Non-greedy so a value
# that happens to contain "---" — none does today — cannot swallow the body.
_HEADER_BODY = re.compile(r"\A(---\n.*?\n---)\n\n(.*)\Z", re.DOTALL)

# `words()` removes exactly the markup a table recovery can introduce or
# remove (the bold `**`, the `|` cell separator, a `\|`-escaped literal pipe)
# — never the text itself.
_WORDS_STRIP = str.maketrans("", "", "*|\\")


def split_header_body(text: str) -> tuple[str, str]:
    """`text` split into its header (through the closing `---` line,
    verbatim) and its body — the inverse of how `scjn.api.snapshot` joins
    them. Raises `ValueError` when `text` carries no such header."""
    m = _HEADER_BODY.match(text)
    if not m:
        raise ValueError("no provenance header found")
    return m.group(1), m.group(2)


def words(text: str) -> list[str]:
    """`text` reduced to its word sequence: `*`, `|` and `\\` removed, then
    split on whitespace — what a table recovery must preserve even though it
    reshapes how a wrapped cell is laid out on the page."""
    return text.translate(_WORDS_STRIP).split()


def _mismatch_detail(old_words: list[str], new_words: list[str]) -> str:
    i = 0
    while i < len(old_words) and i < len(new_words) and old_words[i] == new_words[i]:
        i += 1
    old_excerpt = " ".join(old_words[max(0, i - 3) : i + 5])
    new_excerpt = " ".join(new_words[max(0, i - 3) : i + 5])
    return f"first differing word at position {i}: old={old_excerpt!r} new={new_excerpt!r}"


def instrument_dirs(outdir: Path, coleccion: str, keys: list[str] | None) -> list[Path]:
    """Every ``<outdir>/<coleccion>/<key>`` to look at — every subdirectory
    when `keys` is None, else exactly the named ones, in the order given
    (deduplicated). Never touches the network or reads a file."""
    base = outdir / coleccion
    if keys is None:
        if not base.is_dir():
            raise SystemExit(f"{base} no existe")
        return sorted(p for p in base.iterdir() if p.is_dir())
    vistos: dict[str, Path] = {}
    for key in keys:
        vistos.setdefault(key, base / key)
    faltantes = [key for key, ruta in vistos.items() if not ruta.is_dir()]
    if faltantes:
        raise SystemExit(f"{sorted(faltantes)} no tiene(n) directorio en {base}")
    return list(vistos.values())


def tabbed_snapshots(directorio: Path) -> list[Path]:
    """Every snapshot of `directorio` that contains a tab, oldest first —
    exactly the set issue #253 changed the conversion of."""
    return [
        v.archivo
        for v in versiones_de_directorio(directorio)
        if "\t" in v.archivo.read_text(encoding="utf-8")
    ]


def untabbed_snapshots(directorio: Path) -> list[Path]:
    """Every snapshot of `directorio` that contains no tab, oldest first —
    the control set: #253 changes nothing about how these convert."""
    return [
        v.archivo
        for v in versiones_de_directorio(directorio)
        if "\t" not in v.archivo.read_text(encoding="utf-8")
    ]


@dataclass
class SnapshotResult:
    instrument: str
    file: str
    outcome: str  # "rewritten" | "mismatch" | "failed" | "no_header_ids"
    detail: str | None = None


def reconvert_snapshot(api: ScjnApi, path: Path, instrument: str) -> SnapshotResult:
    """Re-convert one tab-bearing snapshot, writing it only when the new
    body says the same words as the old one and carries no tab itself."""
    text = path.read_text(encoding="utf-8")
    try:
        header, old_body = split_header_body(text)
    except ValueError as exc:
        return SnapshotResult(instrument, path.name, "no_header_ids", str(exc))

    campos = parse_header(header)
    id_ordenamiento = campos.get("id_ordenamiento")
    reforma_id = campos.get("reforma_id")
    if not id_ordenamiento or not reforma_id:
        return SnapshotResult(
            instrument, path.name, "no_header_ids",
            "header carries no id_ordenamiento/reforma_id",
        )

    try:
        articulos = api.articulos_of_reforma(id_ordenamiento, reforma_id)
    except ScjnApiError as exc:
        return SnapshotResult(instrument, path.name, "failed", str(exc))

    new_body = articulos_a_markdown(articulos)
    old_words, new_words = words(old_body), words(new_body)
    if old_words != new_words:
        return SnapshotResult(instrument, path.name, "mismatch", _mismatch_detail(old_words, new_words))
    if "\t" in new_body:
        return SnapshotResult(instrument, path.name, "mismatch", "new body still contains a tab")

    path.write_text(header + "\n\n" + new_body, encoding="utf-8")
    return SnapshotResult(instrument, path.name, "rewritten")


def write_report(path: Path, coleccion: str, outdir: Path, results: list[SnapshotResult]) -> None:
    by_instrument: dict[str, list[dict]] = {}
    summary: dict[str, int] = {}
    for r in results:
        entry = {"file": r.file, "outcome": r.outcome}
        if r.detail:
            entry["detail"] = r.detail
        by_instrument.setdefault(r.instrument, []).append(entry)
        summary[r.outcome] = summary.get(r.outcome, 0) + 1
    report = {
        "coleccion": coleccion,
        "outdir": str(outdir),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "instruments": by_instrument,
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def run_control(api: ScjnApi, directorios: list[Path], n: int, *, log) -> bool:
    """Re-convert the first `n` tab-less snapshots across `directorios`
    (sorted by instrument, chronologically within it) without writing
    anything, and check the rebuilt text is byte-identical to what is on
    disk. Returns False, and stops, on the first mismatch."""
    checked = 0
    for directorio in sorted(directorios, key=lambda p: p.name):
        for path in untabbed_snapshots(directorio):
            if checked >= n:
                return True
            text = path.read_text(encoding="utf-8")
            try:
                header, old_body = split_header_body(text)
            except ValueError:
                continue
            campos = parse_header(header)
            id_ordenamiento = campos.get("id_ordenamiento")
            reforma_id = campos.get("reforma_id")
            if not id_ordenamiento or not reforma_id:
                continue
            try:
                articulos = api.articulos_of_reforma(id_ordenamiento, reforma_id)
            except ScjnApiError as exc:
                log(f"CONTROL FAILED: {directorio.name}/{path.name}: {exc}")
                return False
            new_text = header + "\n\n" + articulos_a_markdown(articulos)
            checked += 1
            if new_text != text:
                log(f"CONTROL FAILED: {directorio.name}/{path.name} does not round-trip byte for byte")
                return False
            log(f"control ok: {directorio.name}/{path.name}")
    if checked < n:
        log(f"warning: only {checked} tab-less snapshot(s) available for --control {n}")
    return True


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coleccion", choices=COLECCIONES, required=True)
    p.add_argument(
        "--outdir", type=Path, default=Path("scripts/scjn"),
        help="donde fetch_scjn_legislacion.py ya escribio <coleccion>/<key>/*.md",
    )
    p.add_argument(
        "--instrumento", action="append", metavar="KEY", dest="instrumentos",
        help=(
            "repetible; un slug para leyes, un id_ordenamiento en las demas colecciones. "
            "Default: cada instrumento bajo <outdir>/<coleccion>"
        ),
    )
    p.add_argument("--espera", type=float, default=1.0, help="segundos de espera entre solicitudes (default: 1.0)")
    p.add_argument("--dry-run", action="store_true", help="lista lo afectado y termina, sin red")
    p.add_argument(
        "--control", type=int, default=0, metavar="N",
        help="re-convierte N snapshots sin tabulador, sin escribir, y verifica que reproducen byte a byte",
    )
    p.add_argument("--report", type=Path, default=None, help="default: <outdir>/<coleccion>-reconvert-report.json")
    return p


def main(argv: list[str] | None = None, api: ScjnApi | None = None) -> int:
    args = build_argparser().parse_args(argv)
    outdir: Path = args.outdir
    coleccion: str = args.coleccion
    report_path = args.report or (outdir / f"{coleccion}-reconvert-report.json")

    directorios = instrument_dirs(outdir, coleccion, args.instrumentos)

    if args.control > 0:
        cliente = api or ScjnApi(espera=args.espera)
        ok = run_control(cliente, directorios, args.control, log=lambda m: print(m, file=sys.stderr))
        if not ok:
            print("CONTROL FAILED -- nada fue reescrito", file=sys.stderr)
            return 1
        print(f"control ok para {coleccion}", file=sys.stderr)
        return 0

    afectados = {d.name: tabbed_snapshots(d) for d in directorios}
    total_afectados = sum(len(v) for v in afectados.values())

    if args.dry_run:
        for key in sorted(afectados):
            if afectados[key]:
                print(f"{key}: {len(afectados[key])} snapshot(s) con tabulador", file=sys.stderr)
        print(
            f"{total_afectados} snapshot(s) afectado(s) en "
            f"{sum(1 for v in afectados.values() if v)} instrumento(s) de {coleccion}",
            file=sys.stderr,
        )
        return 0

    cliente = api or ScjnApi(espera=args.espera)
    results: list[SnapshotResult] = []
    for key in sorted(afectados):
        paths = afectados[key]
        if not paths:
            continue
        for path in paths:
            resultado = reconvert_snapshot(cliente, path, key)
            results.append(resultado)
            detalle = f" -- {resultado.detail}" if resultado.detail else ""
            print(f"{key}/{path.name}: {resultado.outcome}{detalle}", file=sys.stderr)
        write_report(report_path, coleccion, outdir, results)

    write_report(report_path, coleccion, outdir, results)

    summary: dict[str, int] = {}
    for r in results:
        summary[r.outcome] = summary.get(r.outcome, 0) + 1
    print(f"resumen {coleccion}: {summary or '{}'}", file=sys.stderr)
    print(f"reporte: {report_path}", file=sys.stderr)
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
