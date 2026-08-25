#!/usr/bin/env python3
"""Crawl the SCJN Buscador for every ley, reglamento and tratado that
`download_legal_provisions_provenance_ids` already knows about, and save each
one's reform-dated snapshots as Markdown under
``<outdir>/<coleccion>/<abrev-o-nombre>/<fecha_publicacion>.md`` — Fase 1 of
the crawl plan in issue #105.

Only these three collections: issue #105's Fase 0 spike found that the SCJN
does not catalogue NOM technical standards as ordenamientos of their own at
all (8 real NOM codes, zero hits, even searching "Norma Oficial Mexicana"
generically), so "normas" has nothing here to crawl.

Resumable at two levels. Within one instrumento, a file already on disk is
left alone and its row's download skipped (see
nota2md.scjn.descarga_ordenamiento), so a later re-run picking up new
reforms only fetches what is missing. Across a whole collection, the index
of the last instrumento fully attempted is checkpointed to
``<outdir>/<coleccion>/.progreso.json`` and cleared once the collection
finishes; a run killed partway (crash, network drop, Ctrl-C) resumes right
after that index instead of re-walking every already-done instrumento's
reform table from the top — pass ``--reiniciar`` to discard that checkpoint
and sweep the collection from the beginning again (e.g. after the upstream
catalogue itself changed). Rate-limited:
`--espera` seconds between requests, since this is an unofficial site with
no public API (the same posture as leyesmx.diputados/dofjson.dofweb
elsewhere in this repo) — a single ley or reglamento search-then-detail-
then-per-row walk cannot be parallelized across instruments either, since
the SCJN scopes a detail page's own URL to the session that requested it.

    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --coleccion tratados

Needs the "scjn" extra installed (``pip install packages/nota2md[scjn]``) for
python-docx.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from nota2md import download_legal_provisions_provenance_ids
from nota2md.scjn import descarga_ordenamiento, nueva_sesion, slug_instrumento

COLECCIONES = ("leyes", "reglamentos", "tratados")


def _archivo_progreso(outdir: Path, coleccion: str) -> Path:
    return outdir / coleccion / ".progreso.json"


def _lee_progreso(outdir: Path, coleccion: str) -> int:
    """The 1-based index (in `download_legal_provisions_provenance_ids`'s own
    order) of the last instrumento a previous, interrupted run of
    `coleccion` fully attempted — 0 when there is no checkpoint (first run,
    or a collection that already finished and had its checkpoint cleared).
    A malformed/unreadable checkpoint is treated the same as none, rather
    than raising: worst case a finished instrumento gets re-attempted, which
    `descarga_ordenamiento`'s own file-level skip already makes cheap."""
    try:
        return json.loads(_archivo_progreso(outdir, coleccion).read_text(encoding="utf-8"))["indice"]
    except (OSError, json.JSONDecodeError, KeyError):
        return 0


def _guarda_progreso(outdir: Path, coleccion: str, indice: int) -> None:
    archivo = _archivo_progreso(outdir, coleccion)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"indice": indice}), encoding="utf-8")


def rastrea_coleccion(coleccion: str, outdir: Path, espera: float, *, reiniciar: bool = False) -> None:
    instrumentos = download_legal_provisions_provenance_ids(coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    inicio = 0 if reiniciar else _lee_progreso(outdir, coleccion)
    if inicio:
        print(
            f"  reanudando en el instrumento {inicio + 1}/{len(instrumentos)} "
            "(progreso guardado de una corrida anterior)",
            file=sys.stderr,
        )
    for i, entrada in enumerate(instrumentos, 1):
        if i <= inicio:
            continue
        nombre = entrada["nombre"]
        destino = outdir / coleccion / slug_instrumento(entrada)
        print(f"[{coleccion} {i}/{len(instrumentos)}] {nombre}", file=sys.stderr)
        sesion = nueva_sesion()
        try:
            escritos = descarga_ordenamiento(sesion, nombre, destino, espera=espera)
        except Exception as exc:
            print(f"  aviso: {nombre!r} fallo: {exc}", file=sys.stderr)
        else:
            if not escritos:
                print(f"  aviso: sin resultados en la SCJN para {nombre!r}", file=sys.stderr)
        _guarda_progreso(outdir, coleccion, i)
        time.sleep(espera)
    _archivo_progreso(outdir, coleccion).unlink(missing_ok=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--outdir", type=Path, required=True)
    p.add_argument(
        "--coleccion",
        choices=COLECCIONES,
        action="append",
        help="repetible; por defecto rastrea las tres",
    )
    p.add_argument(
        "--espera",
        type=float,
        default=1.0,
        help="segundos de espera entre solicitudes a la SCJN (default: 1.0)",
    )
    p.add_argument(
        "--reiniciar",
        action="store_true",
        help="ignora el progreso guardado de una corrida anterior y rastrea la coleccion desde el principio",
    )
    args = p.parse_args(argv)

    for coleccion in args.coleccion or COLECCIONES:
        rastrea_coleccion(coleccion, args.outdir, args.espera, reiniciar=args.reiniciar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
