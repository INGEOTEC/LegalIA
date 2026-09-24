#!/usr/bin/env python3
"""Probe every already-crawled instrument's **latest** SCJN version for a
table — the first half of the replacement for issue #255's abandoned
in-place re-conversion (a review fix on that issue).

That first attempt rewrote a tab-bearing snapshot's body in place and kept
the rewrite only when a word-order check passed. The check was too strict:
when a table's column header wraps over several source lines,
`scjn.api.articulos_a_markdown`'s merge rule (issue #253) rebuilds it as one
row, and the words can come out in a different order than the old
line-by-line dump had them — a faithful reassembly, not an upstream edit,
but indistinguishable from one under a strict word-for-word check. That
rejected roughly half of every affected snapshot. The repository owner
replaced the whole approach: an instrument is judged by its **current**
state, not by trying to reconcile an old dump against a new one.

For every instrument already on disk under ``<outdir>/<coleccion>/`` with an
`id_ordenamiento` recorded in its own `estado.json`:

1. ``scjn.api.ScjnApi.reformas_of_ordenamiento(id)`` — the whole reform
   table, newest first (the SCJN's own row order);
2. the **latest version** is the first row with `tieneArticulos` true (a
   newer row with no text, e.g. a FE DE ERRATAS, is skipped without
   changing what "latest" means — the SCJN's own order already breaks any
   tie on `fecha_publicacion`, never re-sorted here);
3. ``articulos_of_reforma(id, reformaId)`` for that one reform;
4. **has a table** iff any article's raw `contenido` contains a tab — the
   same signal issue #253 itself uses, checked fresh against the API,
   never against what happens to be on disk.

An instrument with no text-bearing reform at all gets `has_table: false`
(nothing to have a table in). An instrument with no `id_ordenamiento` on
file is reported and skipped outright — never searched for by name, which
is exactly the risk issue #115's Hallazgo C guards against.

This script only probes; it changes nothing on disk. What to do with a
`has_table: true` instrument (delete its snapshots, re-download all of them
with ``fetch_scjn_legislacion.py --instrumento``, link, repackage) is a
manual next step, deliberately not chained here — see
`scripts/README.md`'s own section and `CLAUDE.md`'s "Re-downloading
table-bearing SCJN instruments" for the full procedure.

    ./scripts/find_scjn_table_instruments.py --coleccion leyes
    ./scripts/find_scjn_table_instruments.py --coleccion reglamentos
    ./scripts/find_scjn_table_instruments.py --coleccion lineamientos

The output JSON (``--output``, default
``<outdir>/<coleccion>-table-instruments.json``) is rewritten after every
instrument, and a key already present in it is skipped on a later run — so
a killed probe resumes for free, at the cost of one extra
`reformas_of_ordenamiento` call for whichever instrument was in flight.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first —
# the same convention `fetch_scjn_legislacion.py` already uses.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import ScjnApi, ScjnApiError  # noqa: E402
from scjn.state import lee_estado  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "lineamientos")


def instrument_dirs(outdir: Path, coleccion: str, keys: list[str] | None) -> list[Path]:
    """Every ``<outdir>/<coleccion>/<key>`` to probe — every subdirectory
    when `keys` is None, else exactly the named ones, in the order given
    (deduplicated). Never touches the network."""
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


def local_snapshot_count(directorio: Path) -> int:
    return len(list(directorio.glob("*.md")))


def probe_instrument(api: ScjnApi, id_ordenamiento: str, *, snapshots_local: int) -> dict:
    """One instrument's latest version, probed fresh against the SCJN. Never
    touches disk; the caller supplies `snapshots_local` for the report."""
    try:
        reformas = api.reformas_of_ordenamiento(id_ordenamiento)
    except ScjnApiError as exc:
        return {
            "id_ordenamiento": id_ordenamiento,
            "has_table": None,
            "latest_fecha_publicacion": None,
            "latest_reforma_id": None,
            "reformas_scjn": None,
            "snapshots_local": snapshots_local,
            "error": str(exc),
        }

    latest = next((r for r in reformas if r.tieneArticulos), None)
    if latest is None:
        return {
            "id_ordenamiento": id_ordenamiento,
            "has_table": False,
            "latest_fecha_publicacion": None,
            "latest_reforma_id": None,
            "reformas_scjn": len(reformas),
            "snapshots_local": snapshots_local,
            "error": None,
        }

    try:
        articulos = api.articulos_of_reforma(id_ordenamiento, latest.reformaId)
    except ScjnApiError as exc:
        return {
            "id_ordenamiento": id_ordenamiento,
            "has_table": None,
            "latest_fecha_publicacion": latest.fecha_publicacion,
            "latest_reforma_id": latest.reformaId,
            "reformas_scjn": len(reformas),
            "snapshots_local": snapshots_local,
            "error": str(exc),
        }

    has_table = any("\t" in (a.contenido or "") for a in articulos)
    return {
        "id_ordenamiento": id_ordenamiento,
        "has_table": has_table,
        "latest_fecha_publicacion": latest.fecha_publicacion,
        "latest_reforma_id": latest.reformaId,
        "reformas_scjn": len(reformas),
        "snapshots_local": snapshots_local,
        "error": None,
    }


def load_output(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return datos if isinstance(datos, dict) else {}


def write_output(path: Path, resultados: dict) -> None:
    path.write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coleccion", choices=COLECCIONES, required=True)
    p.add_argument(
        "--outdir", type=Path, default=Path("scripts/scjn"),
        help="donde fetch_scjn_legislacion.py ya escribio <coleccion>/<key>/",
    )
    p.add_argument(
        "--instrumento", action="append", metavar="KEY", dest="instrumentos",
        help=(
            "repetible; un slug para leyes, un id_ordenamiento en las demas colecciones. "
            "Default: cada instrumento bajo <outdir>/<coleccion>"
        ),
    )
    p.add_argument("--espera", type=float, default=1.0, help="segundos de espera entre solicitudes (default: 1.0)")
    p.add_argument("--output", type=Path, default=None, help="default: <outdir>/<coleccion>-table-instruments.json")
    return p


def main(argv: list[str] | None = None, api: ScjnApi | None = None) -> int:
    args = build_argparser().parse_args(argv)
    outdir: Path = args.outdir
    coleccion: str = args.coleccion
    output_path = args.output or (outdir / f"{coleccion}-table-instruments.json")

    directorios = instrument_dirs(outdir, coleccion, args.instrumentos)
    resultados = load_output(output_path)

    cliente = api or ScjnApi(espera=args.espera)
    for directorio in directorios:
        key = directorio.name
        if key in resultados:
            continue

        estado = lee_estado(directorio)
        id_ordenamiento = estado.get("id_ordenamiento")
        snapshots_local = local_snapshot_count(directorio)

        if not id_ordenamiento:
            resultados[key] = {
                "id_ordenamiento": None,
                "has_table": None,
                "latest_fecha_publicacion": None,
                "latest_reforma_id": None,
                "reformas_scjn": None,
                "snapshots_local": snapshots_local,
                "error": "no_id_ordenamiento",
            }
            print(f"{key}: no_id_ordenamiento", file=sys.stderr)
            write_output(output_path, resultados)
            continue

        resultado = probe_instrument(cliente, id_ordenamiento, snapshots_local=snapshots_local)
        resultados[key] = resultado
        estado_txt = resultado["error"] or f"has_table={resultado['has_table']}"
        print(f"{key}: {estado_txt}", file=sys.stderr)
        write_output(output_path, resultados)

    con_error = sum(1 for r in resultados.values() if r.get("error"))
    con_tabla = sum(1 for r in resultados.values() if r.get("has_table") is True)
    print(
        f"{len(resultados)} instrumento(s) probado(s) de {coleccion}: "
        f"{con_tabla} con tabla en su version mas reciente, {con_error} con error",
        file=sys.stderr,
    )
    print(f"salida: {output_path}", file=sys.stderr)
    return 1 if con_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
