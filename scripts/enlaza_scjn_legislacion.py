#!/usr/bin/env python3
"""Match every SCJN snapshot `fetch_scjn_legislacion.py` already downloaded
to the `codNota` of the DOF note that published it — Fase 2 of the crawl plan
in issue #105.

For each instrument `download_legal_provisions_provenance_ids(coleccion)`
knows about, its own `historial` (the `codNota` of its reforms, oldest first)
is paired by publication date against the snapshots already sitting under
``<outdir>/<coleccion>/<abrev-o-nombre>/`` (see
`nota2md.scjn.versiones_de_directorio`), using a dofjson titles dataset
(`dofjson.download_legal_provisions_titles`) for the fecha of every candidate
codNota — see `nota2md.scjn.enlaza_historial` for how ties and misses are
resolved. Writes one ``indice.json`` per instrument directory, listing each
snapshot's own file, `fecha_publicacion`, and the `codNota` matched to it
(``null`` when no match was found).

Needs a titles dataset already built:

    python -c "from nota2md import download_legal_provisions_titles as d; d('titulos.jsonl.gz')"
    ./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion --titulos titulos.jsonl.gz

Only instrument directories `fetch_scjn_legislacion.py` already crawled are
touched; a coleccion+instrumento pair with no directory yet is skipped
silently — nothing to match until that instrument has been crawled.
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

from nota2md import download_legal_provisions_provenance_ids
from nota2md.scjn import (
    enlaza_historial,
    lee_cabecera,
    slug_instrumento,
    versiones_de_directorio,
)

COLECCIONES = ("leyes", "reglamentos", "tratados")


def carga_porf(titulos: Path) -> dict:
    """Every dofjson title record in `titulos` (a gzipped JSONL from
    `dofjson.download_legal_provisions_titles`), grouped by `fecha` — the
    same shape `leyesmx.dof.notas_por_fecha` builds, reused here as
    `enlaza_historial`'s own `porf` argument."""
    porf: dict[str, list] = {}
    with gzip.open(titulos, "rt", encoding="utf-8") as f:
        for linea in f:
            nota = json.loads(linea)
            porf.setdefault(nota["fecha"], []).append(nota)
    return porf


def _confianza(archivo: Path) -> dict:
    """The `ratio_similitud`/`sospechoso` fields `nota2md.scjn._cabecera`
    writes into a snapshot's own header (issue #115), read back into
    `indice.json` so a packaging step can quarantine `sospechoso` entries
    without re-reading every snapshot file itself. Both come back `None`
    for a snapshot a crawl wrote before issue #115 added them — an older
    corpus is not re-crawled just to backfill this; `audita_scjn_legislacion.py`
    recomputes the ratio offline for exactly that case."""
    campos = lee_cabecera(archivo)
    ratio = campos.get("ratio_similitud")
    return {
        "ratio_similitud": float(ratio) if ratio is not None else None,
        "sospechoso": (campos.get("sospechoso") == "true") if "sospechoso" in campos else None,
    }


def enlaza_coleccion(coleccion: str, outdir: Path, porf: dict) -> None:
    instrumentos = download_legal_provisions_provenance_ids(coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    for i, entrada in enumerate(instrumentos, 1):
        destino = outdir / coleccion / slug_instrumento(entrada)
        if not destino.is_dir():
            continue
        versiones = versiones_de_directorio(destino)
        if not versiones:
            continue
        enlazadas = enlaza_historial(versiones, entrada["historial"], porf)
        indice = [
            {
                "archivo": v.archivo.name,
                "fecha_publicacion": v.fecha_publicacion,
                "codNota": v.codNota,
                **_confianza(v.archivo),
            }
            for v in enlazadas
        ]
        (destino / "indice.json").write_text(
            json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        enlazados = sum(1 for v in enlazadas if v.codNota is not None)
        print(
            f"[{coleccion} {i}/{len(instrumentos)}] {entrada['nombre']}: "
            f"{enlazados}/{len(enlazadas)} enlazadas",
            file=sys.stderr,
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="donde fetch_scjn_legislacion.py ya escribio cada coleccion",
    )
    p.add_argument(
        "--titulos", type=Path, required=True,
        help="dataset de dofjson.download_legal_provisions_titles (gzip JSONL)",
    )
    p.add_argument(
        "--coleccion", choices=COLECCIONES, action="append",
        help="repetible; por defecto las tres",
    )
    args = p.parse_args(argv)

    porf = carga_porf(args.titulos)
    for coleccion in args.coleccion or COLECCIONES:
        enlaza_coleccion(coleccion, args.outdir, porf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
