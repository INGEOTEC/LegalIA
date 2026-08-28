#!/usr/bin/env python3
"""Write each SCJN collection's own title catalogue -- `nombre` plus `abrev`
and `actualizado`, never Diputados' own reform *history* itself -- to
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
`empaqueta_scjn_leyes.py`) reads the catalogue this writes instead, so
`historial` never reaches them, and the
`historial-legislativo` release is downloaded once per collection, not once
per script.

    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion
    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion --coleccion reglamentos --coleccion tratados

`abrev` is present only for collections that have one (leyes, reglamentos --
see `nota2md.scjn.slug_instrumento`); tratados are identified by `nombre`
alone.

Issue #124's follow-up ("Dos casos disparadores") added two more fields, and
made this script no longer overwrite `catalogo.json` blindly:

`actualizado`
    The ISO date (`YYYY-MM-DD`) of the instrument's own most recent reform
    -- Diputados' `historial`'s own last `codNota`, resolved to a
    publication date via `dofjson` (one request per instrument, so this
    makes a run of this script noticeably slower/more network-bound than
    before). `fetch_scjn_legislacion.py` uses it (Mecanismo 2) to skip
    re-searching the SCJN, on a refresh run, for an instrument nothing has
    changed on since the collection's own last full crawl -- and, since an
    instrument with no snapshot on disk yet is never skipped regardless of
    `actualizado` (`nota2md.scjn.instrument_up_to_date`), this is also what
    lets a brand-new law with no SCJN listing yet keep getting retried
    automatically on every refresh, with nothing to configure by hand.
`nombre_scjn`
    An optional manual override: the exact string to search the SCJN with
    instead of `nombre`, for the rare instrument the SCJN's own full-text
    search never finds under Diputados' own wording (confirmed so far only
    for `lisipl`, whose `nombre` carries a 250+ character trailing
    parenthetical alternate name). Nothing in this script ever *sets*
    `nombre_scjn` -- it is added by hand to `catalogo.json` -- but a fresh
    run now reads back whatever `catalogo.json` already exists before
    overwriting it, and carries every entry's own `nombre_scjn` forward
    (`nota2md.scjn.merge_catalog_overrides`), matched by `abrev`/`nombre`
    (`nota2md.scjn.catalog_key`). Without this, a manual override would
    vanish the next time this script ran.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md import download_legal_provisions_provenance_ids  # noqa: E402
from nota2md.builder import fetch_nota  # noqa: E402
from nota2md.scjn import iso_date_from_note, merge_catalog_overrides  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "tratados")


def _actualizado(nombre: str, historial: list[int]) -> str | None:
    """`historial`'s own last (most recent) codNota, resolved to its ISO
    publication date via `dofjson` -- the catalogue's `actualizado` field.
    A codNota this cannot resolve (network hiccup, or dofjson simply
    lacking it) must never abort the whole catalogue refresh over one
    instrument's own metadata: warned about and left unset instead, the
    same posture `fetch_scjn_legislacion.py` already takes per instrument."""
    if not historial:
        return None
    try:
        nota = fetch_nota(historial[-1])
    except Exception as exc:
        print(f"  warning: could not resolve 'actualizado' for {nombre!r}: {exc}", file=sys.stderr)
        return None
    return iso_date_from_note(nota)


def extract_catalog(coleccion: str, catalogo_previo: list[dict] | None = None) -> list[dict]:
    """`coleccion`'s own instruments, projected down to `nombre` (+ `abrev`
    when the catalogue has one, + `actualizado` when it could be resolved)
    -- Diputados' `historial` itself is dropped here, not just left unread
    by callers downstream. `catalogo_previo` -- the `catalogo.json` this
    script already wrote on a previous run, if any -- has its own entries'
    `nombre_scjn` carried over into the result (`merge_catalog_overrides`,
    issue #124's Mecanismo 1); see the module docstring."""
    entries = download_legal_provisions_provenance_ids(coleccion)
    catalog = [
        {
            "nombre": entry["nombre"],
            **({"abrev": entry["abrev"]} if entry.get("abrev") else {}),
            **(
                {"actualizado": actualizado}
                if (actualizado := _actualizado(entry["nombre"], entry["historial"]))
                else {}
            ),
        }
        for entry in entries
    ]
    return merge_catalog_overrides(catalog, catalogo_previo)


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
        destination = args.outdir / coleccion
        archivo_catalogo = destination / "catalogo.json"
        catalogo_previo = (
            json.loads(archivo_catalogo.read_text(encoding="utf-8"))
            if archivo_catalogo.is_file()
            else None
        )
        catalog = extract_catalog(coleccion, catalogo_previo)
        destination.mkdir(parents=True, exist_ok=True)
        archivo_catalogo.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"{coleccion}: {len(catalog)} instrumento(s) -> {archivo_catalogo}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
