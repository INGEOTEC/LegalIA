#!/usr/bin/env python3
"""Strip the SCJN's own editorial commentary ("N. DE E." / "NOTA N") from
snapshots `fetch_scjn_legislacion.py` already downloaded — issue #114's
Paso 5, a one-time re-process of what is already on disk.

`nota2md.scjn.docx_a_markdown` now strips this (`quita_notas_editoriales`)
from every future crawl; this repairs what was already written before that
existed, without hitting the SCJN site again. Nothing here re-crawls or
re-links: a snapshot's `codNota` (`enlaza_scjn_legislacion.py`, Fase 2) comes
from its provenance header, which this never touches, only the body — so an
already-built `indice.json` stays valid.

Run with --dry-run first: it reports how many paragraphs each file would
lose, and writes nothing.

    ./scripts/repara_notas_editoriales_scjn.py --outdir scjn-legislacion --dry-run
    ./scripts/repara_notas_editoriales_scjn.py --outdir scjn-legislacion
"""

import argparse
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md.scjn import quita_notas_editoriales  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "tratados")


def repara_cuerpo(cuerpo: str) -> tuple[str, int]:
    """`cuerpo` (everything `docx_a_markdown` wrote after the provenance
    header) with every SCJN editorial insertion removed, and how many
    paragraphs disappeared entirely — an insertion that was a whole
    paragraph on its own, not just a file-level count, since one file can
    lose more than one."""
    parrafos = cuerpo.rstrip("\n").split("\n\n")
    limpios = [quita_notas_editoriales(p) for p in parrafos]
    vacios = sum(1 for original, limpio in zip(parrafos, limpios) if original and not limpio)
    nuevo_cuerpo = "\n\n".join(p for p in limpios if p) + "\n"
    return nuevo_cuerpo, vacios


def repara_archivo(archivo: Path, dry_run: bool) -> int:
    """Rewrites `archivo` in place with its editorial commentary removed
    (unless `dry_run`), leaving its provenance header untouched. Returns how
    many paragraphs disappeared entirely."""
    texto = archivo.read_text(encoding="utf-8")
    cabecera, _, cuerpo = texto.partition("\n\n")
    nuevo_cuerpo, vacios = repara_cuerpo(cuerpo)
    nuevo_texto = f"{cabecera}\n\n{nuevo_cuerpo}"
    if nuevo_texto != texto and not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return vacios


def repara_coleccion(coleccion: str, outdir: Path, dry_run: bool) -> tuple[int, int]:
    destino = outdir / coleccion
    if not destino.is_dir():
        return 0, 0
    archivos_tocados = 0
    parrafos_quitados = 0
    for archivo in sorted(destino.glob("*/*.md")):
        vacios = repara_archivo(archivo, dry_run)
        if vacios:
            archivos_tocados += 1
            parrafos_quitados += vacios
    return archivos_tocados, parrafos_quitados


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
        "--dry-run", action="store_true",
        help="solo reporta cuantos parrafos se quitarian; no escribe nada",
    )
    args = p.parse_args(argv)

    total_archivos = 0
    total_parrafos = 0
    for coleccion in args.coleccion or COLECCIONES:
        archivos, parrafos = repara_coleccion(coleccion, args.outdir, args.dry_run)
        if archivos:
            print(f"{coleccion}: {archivos} archivo(s), {parrafos} parrafo(s)", file=sys.stderr)
        total_archivos += archivos
        total_parrafos += parrafos

    accion = "se quitarian" if args.dry_run else "se quitaron"
    print(
        f"total: {total_parrafos} parrafo(s) de nota editorial {accion} "
        f"en {total_archivos} archivo(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
