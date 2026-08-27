#!/usr/bin/env python3
"""Write each SCJN collection's own title catalogue -- `nombre` plus `abrev`
only, never Diputados' own reform history -- to
``<outdir>/<coleccion>/catalogo.json``.

Issue #123 corrected the SCJN-leyes design so Diputados' `historial` (a list
of `codNota`, from `download_legal_provisions_provenance_ids`) is never
consulted to link a reform to the `codNota` that published it -- only an
instrument's `nombre` matters, to search the SCJN
(`fetch_scjn_legislacion.py`) and to test same-day title mentions
(`enlaza_scjn_legislacion.py`, issue #126). This script is now the only
place in the SCJN pipeline that calls
`download_legal_provisions_provenance_ids`: every other script
(`fetch_scjn_legislacion.py`, `enlaza_scjn_legislacion.py`,
`audita_scjn_legislacion.py`, `empaqueta_scjn_leyes.py`) reads the catalogue
this writes instead, so `historial` never reaches them, and the
`historial-legislativo` release is downloaded once per collection, not once
per script.

    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion
    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion --coleccion reglamentos --coleccion tratados

`abrev` is present only for collections that have one (leyes, reglamentos --
see `nota2md.scjn.slug_instrumento`); tratados are identified by `nombre`
alone.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md import download_legal_provisions_provenance_ids  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "tratados")


def extract_catalog(coleccion: str) -> list[dict]:
    """`coleccion`'s own instruments, projected down to `nombre` (+ `abrev`
    when the catalogue has one) -- Diputados' `historial` is dropped here,
    not just left unread by callers downstream."""
    entries = download_legal_provisions_provenance_ids(coleccion)
    return [
        {"nombre": entry["nombre"], **({"abrev": entry["abrev"]} if entry.get("abrev") else {})}
        for entry in entries
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument(
        "--coleccion",
        choices=COLECCIONES,
        action="append",
        help="repeatable; defaults to all three",
    )
    args = parser.parse_args(argv)

    for coleccion in args.coleccion or COLECCIONES:
        catalog = extract_catalog(coleccion)
        destination = args.outdir / coleccion
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "catalogo.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"{coleccion}: {len(catalog)} instrumento(s) -> {destination / 'catalogo.json'}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
