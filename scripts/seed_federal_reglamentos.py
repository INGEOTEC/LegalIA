#!/usr/bin/env python3
"""Turn a reviewed `discover_federal_reglamentos.py`/`discover_federal_lineamientos.py`
list into ``<outdir>/<coleccion>/<idOrdenamiento>/estado.json`` -- Fase 2 of
issue #220 (`reglamentos`) and #222 (`lineamientos`), the id-keyed sibling of
hand-writing a law's `abrev`/`nombre` before its first crawl. Shared by both
collections (issue #222's Fase 0) since the seed itself has always been
`coleccion`-agnostic -- only the discovery list it reads from differs.

    ./scripts/discover_federal_reglamentos.py --json candidatos.json
    less candidatos.json   # review it -- this is the human decision point
    ./scripts/seed_federal_reglamentos.py --outdir scripts/scjn candidatos.json
    # or, for the other id-keyed collection:
    ./scripts/seed_federal_reglamentos.py --outdir scripts/scjn --coleccion lineamientos candidatos.json

Unlike a law, no candidate selection is involved at all (issue #220's own
"No candidate selection is involved" section): `id_ordenamiento` already
came out of the search hit, paired with its title, so there is no wrong
candidate here to choose -- the human review already happened over the
discovery list, not over each individual seed.

Each entry becomes ``<outdir>/<coleccion>/<id_ordenamiento>/estado.json``
with ``id_ordenamiento``, ``nombre``, ``categoria_ordenamiento``,
``vigencia``, ``materia``, ``resumen`` (issue #220, Fase 2) --
``clasificado`` (the date this seed was written) is set only the first
time, same reasoning `escribe_estado`'s merge already gives every other
field here. Neither id-keyed collection has an `abrev` or an `actualizado`
at all (see CLAUDE.md's own sections on them): freshness is judged by row
comparison against the SCJN's own reform table
(`scjn.state.reformas_faltantes`), which needs nothing dated.

**Idempotent, and never overwrites a crawled state.** A directory that
already has a snapshot on disk (`*.md`) is left completely untouched --
re-running this after a partial crawl must never reset `estado.json`'s
`rastreado`/other crawl-written fields back to just the seed. A directory
with no snapshot yet, whether never seeded or seeded by a previous run,
gets `scjn.state.escribe_estado`'s merge (adds/updates the seed fields,
never erases anything else already there).
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.cache import COLECCIONES_POR_ID  # noqa: E402
from scjn.catalog import instrumento_key  # noqa: E402
from scjn.state import escribe_estado, lee_estado  # noqa: E402

#: The fields a discovery candidate carries that are worth seeding --
#: `id_ordenamiento`/`nombre` unconditionally, the rest only when present
#: (issue #220: `materia`/`resumen` are half-populated by the SCJN itself,
#: and this corpus records what it gives, inferring nothing).
CAMPOS_SEMILLA = ("id_ordenamiento", "nombre", "categoria_ordenamiento", "vigencia",
                  "materia", "resumen")


def seed(outdir: Path, candidatos: list[dict], *, coleccion: str = "reglamentos", log=print) -> list[str]:
    """Write `estado.json` for every one of `candidatos` that has no
    snapshot on disk yet, and return the `id_ordenamiento` keys actually
    written. A candidate whose directory already has a snapshot is skipped
    outright, printed apart -- never touched, crawled or not.

    `coleccion` is any name in `scjn.cache.COLECCIONES_POR_ID`
    (`"reglamentos"`, `"lineamientos"`) -- both id-keyed collections share
    this exact seeding logic (issue #222's Fase 0)."""
    escritos = []
    saltados = []
    for candidato in candidatos:
        clave = instrumento_key(candidato)
        directorio = outdir / coleccion / clave
        if directorio.is_dir() and any(directorio.glob("*.md")):
            saltados.append(clave)
            continue
        campos = {c: candidato[c] for c in CAMPOS_SEMILLA if candidato.get(c)}
        if not lee_estado(directorio).get("clasificado"):
            campos["clasificado"] = date.today().isoformat()
        escribe_estado(directorio, **campos)
        escritos.append(clave)
    log(f"{coleccion}: {len(escritos)} estado.json escrito(s)/actualizado(s)")
    if saltados:
        log(f"{coleccion}: {len(saltados)} ya rastreado(s) -- no se tocaron: {sorted(saltados)}")
    return escritos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "lista", type=Path,
        help="el JSON que discover_federal_<coleccion>.py --json escribio, ya revisado a mano",
    )
    parser.add_argument(
        "--outdir", type=Path, required=True,
        help="donde escribir <outdir>/<coleccion>/<id_ordenamiento>/estado.json",
    )
    parser.add_argument(
        "--coleccion", choices=tuple(COLECCIONES_POR_ID), default="reglamentos",
        help="que coleccion id-keyed sembrar (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    candidatos = json.loads(args.lista.read_text(encoding="utf-8"))
    seed(args.outdir, candidatos, coleccion=args.coleccion, log=lambda m: print(m, file=sys.stderr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
