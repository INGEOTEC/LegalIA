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

Resumable: a file already on disk is left alone and its row's download
skipped (see nota2md.scjn.descarga_ordenamiento), so a run interrupted
partway — or a later re-run picking up new reforms — only fetches what is
missing, instead of starting the whole collection over. Rate-limited:
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
import sys
import time
from pathlib import Path

from nota2md import download_legal_provisions_provenance_ids
from nota2md.scjn import descarga_ordenamiento, nueva_sesion, slug_instrumento

COLECCIONES = ("leyes", "reglamentos", "tratados")


def rastrea_coleccion(coleccion: str, outdir: Path, espera: float) -> None:
    instrumentos = download_legal_provisions_provenance_ids(coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    for i, entrada in enumerate(instrumentos, 1):
        nombre = entrada["nombre"]
        destino = outdir / coleccion / slug_instrumento(entrada)
        print(f"[{coleccion} {i}/{len(instrumentos)}] {nombre}", file=sys.stderr)
        sesion = nueva_sesion()
        try:
            escritos = descarga_ordenamiento(sesion, nombre, destino, espera=espera)
        except Exception as exc:
            print(f"  aviso: {nombre!r} fallo: {exc}", file=sys.stderr)
            continue
        if not escritos:
            print(f"  aviso: sin resultados en la SCJN para {nombre!r}", file=sys.stderr)
        time.sleep(espera)


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
    args = p.parse_args(argv)

    for coleccion in args.coleccion or COLECCIONES:
        rastrea_coleccion(coleccion, args.outdir, args.espera)
    return 0


if __name__ == "__main__":
    sys.exit(main())
