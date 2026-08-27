#!/usr/bin/env python3
"""Offline audit of the corpus `fetch_scjn_legislacion.py` already crawled —
issue #115's "Hallazgo C": Fase 2's original `enlaza_historial` only checked
that a snapshot's date was in the right instrument's Diputados `historial`,
never that the document itself really was that instrument. A wrong document
whose date happened to line up with the correct `historial` linked just
fine, with no sign of the mistake in any enlazamiento percentage — issue
#123 later replaced that design with `nota2md.scjn.enlaza_por_titulo`
(matching only by same-day title mention, never by `historial`), but this
offline audit — recomputing `elige_candidato`'s own guards against an
already-crawled corpus — is unaffected by which linking function runs
afterwards.

This never re-crawls the SCJN. The title `elige_candidato` chose for each
instrument is already sitting in the `ordenamiento:` field of every snapshot
it wrote (`nota2md.scjn.lee_cabecera`) — one already-downloaded snapshot per
instrument is enough to recompute, offline, how well that title actually
matches the catalogue's own `nombre` for it (`nota2md.scjn.ratio_similitud`),
and whether it would trip either of the two hard guards `elige_candidato`
gained for issue #115 (`es_acuerdo_interno`, `grupo_instrumento`). Every
instrument with a directory is scored this way and printed sorted from least
to most confident — the priority list issue #115 asked for, in place of a
blind sample over the ~600 instruments crawled so far.

A guard tripping here does not mean a past crawl actually got a wrong
document: the guards did not exist yet when `leyes`/`reglamentos`/`tratados`
were crawled, so this is exactly the retroactive check that tells the two
apart. `acuerdo_interno`/`grupo_incompatible` are near-certain misses (the 5
issue #115 confirmed by hand all fall in one of those two, or below
`bajo_umbral`); `sospechoso` is the zone `elige_candidato` itself declines to
resolve by text alone and flags for a human instead.

    ./scripts/audita_scjn_legislacion.py --outdir scjn-legislacion
    ./scripts/audita_scjn_legislacion.py --outdir scjn-legislacion --coleccion tratados --json auditoria.json
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md import download_legal_provisions_provenance_ids  # noqa: E402
from nota2md.scjn import (  # noqa: E402
    UMBRAL_CONFIANZA_SIMILITUD,
    UMBRAL_MINIMO_SIMILITUD,
    es_acuerdo_interno,
    grupo_instrumento,
    lee_cabecera,
    ratio_similitud,
    slug_instrumento,
    versiones_de_directorio,
)

COLECCIONES = ("leyes", "reglamentos", "tratados")


@dataclass
class Hallazgo:
    """One instrumento already crawled, scored against its own catalogue
    entry — `motivo` is the strongest guard it trips, least confident
    first: `acuerdo_interno` and `grupo_incompatible` are the two hard
    exclusions `elige_candidato` now applies before ever comparing text;
    `bajo_umbral`/`sospechoso` are `ratio` alone, split at
    `UMBRAL_MINIMO_SIMILITUD`/`UMBRAL_CONFIANZA_SIMILITUD`; `confiable` is
    everything else."""

    coleccion: str
    slug: str
    nombre_catalogo: str
    ordenamiento_guardado: str
    ratio: float
    motivo: str


def clasifica(nombre_catalogo: str, ordenamiento_guardado: str, ratio: float) -> str:
    if es_acuerdo_interno(ordenamiento_guardado):
        return "acuerdo_interno"
    grupo_nombre = grupo_instrumento(nombre_catalogo)
    grupo_titulo = grupo_instrumento(ordenamiento_guardado)
    if grupo_nombre is not None and grupo_titulo is not None and grupo_nombre != grupo_titulo:
        return "grupo_incompatible"
    if ratio < UMBRAL_MINIMO_SIMILITUD:
        return "bajo_umbral"
    if ratio < UMBRAL_CONFIANZA_SIMILITUD:
        return "sospechoso"
    return "confiable"


def audita_coleccion(coleccion: str, outdir: Path) -> list[Hallazgo]:
    instrumentos = download_legal_provisions_provenance_ids(coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s) en el catalogo", file=sys.stderr)
    hallazgos = []
    for entrada in instrumentos:
        destino = outdir / coleccion / slug_instrumento(entrada)
        if not destino.is_dir():
            continue
        versiones = versiones_de_directorio(destino)
        if not versiones:
            continue
        ordenamiento = lee_cabecera(versiones[0].archivo).get("ordenamiento")
        if not ordenamiento:
            continue
        ratio = ratio_similitud(ordenamiento, entrada["nombre"])
        hallazgos.append(
            Hallazgo(
                coleccion=coleccion,
                slug=slug_instrumento(entrada),
                nombre_catalogo=entrada["nombre"],
                ordenamiento_guardado=ordenamiento,
                ratio=ratio,
                motivo=clasifica(entrada["nombre"], ordenamiento, ratio),
            )
        )
    return hallazgos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="donde fetch_scjn_legislacion.py ya escribio cada coleccion",
    )
    p.add_argument(
        "--coleccion", choices=COLECCIONES, action="append",
        help="repetible; por defecto las tres",
    )
    p.add_argument(
        "--json", type=Path,
        help="ademas del reporte en stdout, escribe la lista completa de hallazgos aqui",
    )
    args = p.parse_args(argv)

    hallazgos = [
        h
        for coleccion in (args.coleccion or COLECCIONES)
        for h in audita_coleccion(coleccion, args.outdir)
    ]
    hallazgos.sort(key=lambda h: h.ratio)

    por_motivo: dict[str, int] = {}
    for h in hallazgos:
        por_motivo[h.motivo] = por_motivo.get(h.motivo, 0) + 1
    print(f"{len(hallazgos)} instrumento(s) auditado(s): {por_motivo}", file=sys.stderr)

    for h in hallazgos:
        print(
            f"{h.ratio:.3f}  {h.motivo:<20} {h.coleccion:<11} {h.slug:<25} "
            f"catalogo={h.nombre_catalogo!r} guardado={h.ordenamiento_guardado!r}"
        )

    if args.json:
        args.json.write_text(
            json.dumps([asdict(h) for h in hallazgos], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
