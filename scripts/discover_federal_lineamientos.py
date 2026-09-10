#!/usr/bin/env python3
"""Discover every federal *lineamiento* the SCJN lists and report a
reviewable seed list for the ``scjn-lineamientos`` corpus (issue #222,
Fase 1) -- and stop: this script never writes anything, exactly like
``discover_federal_reglamentos.py``.

A thin wrapper over `scjn.discovery` (issue #222's Fase 0), naming this
collection's own category and phrases; the phrase-union paging, the
reform-category rescue rule and the coverage audit itself live there,
shared with ``discover_federal_reglamentos.py``.

    ./scripts/discover_federal_lineamientos.py
    ./scripts/discover_federal_lineamientos.py --json candidatos.json
    ./scripts/discover_federal_lineamientos.py --auditoria-cobertura   # ~70 min, once

## Inclusion rule

Same shape as reglamentos (issue #220): an instrument is in the corpus when
``categoriaOrdenamiento == "LINEAMIENTOS"``, or any row of its own reform
table has ``categoriaReforma == "LINEAMIENTOS"``.

## Two passes, one rule

1. **The union of a phrase search over ``categoriaF=LINEAMIENTOS``+``FEDERAL``**
   (`scjn.discovery.discover_by_category`): unlike REGLAMENTO, ``q="lineamientos"``
   alone reaches all 159 instruments the SCJN classifies LINEAMIENTOS
   (measured 2026-09-10) -- no title in the category fails to contain the
   word. The extra common-word phrases are kept anyway, for the same reason
   `discover_federal_reglamentos.py` keeps them: a future SCJN catalogue
   change could introduce a straggler, and paging one more short phrase
   costs nothing next to a ~340-request crawl.
2. **The reform-category rescue** (`scjn.discovery.rescue_by_reform_category`),
   applied to the other 384 federal hits of ``q="lineamientos"`` (measured
   2026-09-10): each one's own reform table is fetched once and checked for
   a LINEAMIENTOS row. 4 are genuine rescues (3 titled "LINEAMIENTOS ...",
   one a "MANUAL DE LINEAMIENTOS..." with a single LINEAMIENTOS reform row
   and no text at all -- see issue #222's decision 6); the rest (mostly 371
   ACUERDO (S)) are correctly rejected. None of the 159 has a REGLAMENTO
   reform row, so the two id-keyed collections never overlap.

## The one-time coverage audit (decision 8, ``--auditoria-cobertura``)

The two passes above only ever look at instruments the phrase "lineamientos"
already turns up. An instrument whose title never says "lineamientos" at
all, but whose reform table the SCJN classifies as one somewhere in its
history, would be invisible to them -- so ``--auditoria-cobertura`` sweeps
every *other* federal instrument's reform table too (~8800 of them at
2026-09-10's catalogue size, ~65-70 minutes at the default ``--espera``) and
applies the same rescue rule. Exhaustive and one-time, same posture as
`discover_federal_reglamentos.py`'s own audit -- and it shares that one's
cache (``--cache-dir``, default ``scripts/scjn/``): every reform table
either sweep fetches is cached under `idOrdenamiento`
(`scjn.discovery.coverage_audit`), so running this audit after reglamentos'
own reuses whatever it already swept instead of paying for it twice.

## Reports and stops, never writes anything

Same posture as `discover_federal_reglamentos.py`: the seed list this
prints (or, with ``--json``, writes as a reviewable file) is Fase 1's whole
deliverable. Turning it into ``estado.json`` files is Fase 2
(``seed_federal_reglamentos.py --coleccion lineamientos``); nothing here
writes corpus state.

Needs network to the SCJN (the faceted search plus, for the rescue rule and
the coverage audit, one ``Reforma`` request per candidate). No DOF
confirmation step at all (issue #220's own Scope, unchanged by #222).
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import ScjnApi  # noqa: E402
from scjn.discovery import discover  # noqa: E402

CATEGORIA = "LINEAMIENTOS"

#: `q="lineamientos"` alone reaches all 159 category members (measured
#: 2026-09-10); the rest are kept for the same reason
#: `discover_federal_reglamentos.py`'s own union keeps its short words --
#: cheap insurance against a future straggler.
FRASES_UNION = ("lineamientos", "lineamiento", "de", "a", "la", "y", "para", "el")

#: The one phrase whose *other-category* hits the rescue rule is applied to.
FRASE_RESCATE = "lineamientos"

#: Every other federal category worth sweeping for the coverage audit
#: (issue #222's decision 8) -- mirrors
#: `discover_federal_reglamentos.py`'s own list, with REGLAMENTO added back
#: (that script excludes it as already covered by its own primary pass) and
#: LINEAMIENTOS itself removed (covered by this script's own primary pass
#: instead).
FRASES_AUDITORIA_COBERTURA = (
    ("ley", "LEY"), ("codigo", "CODIGO"), ("constitucion", "CONSTITUCION"),
    ("reglamento", "REGLAMENTO"), ("acuerdo", "ACUERDO"), ("estatuto", "ESTATUTO"),
    ("decreto", "DECRETO"), ("instrumento normativo", "INSTRUMENTO NORMATIVO"),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--espera", type=float, default=0.5,
        help="segundos de espera entre solicitudes a la SCJN (default: 0.5)",
    )
    parser.add_argument(
        "--auditoria-cobertura", action="store_true",
        help=(
            "ademas de las dos pasadas normales, barre el resto del universo "
            "federal (~8800 instrumentos, ~65-70 min) buscando un LINEAMIENTOS "
            "invisible al titulo (decision 8, issue #222) -- una sola vez"
        ),
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path(__file__).resolve().parent / "scjn",
        help=(
            "donde cachear las tablas de reforma que la auditoria de cobertura "
            "barre (issue #222's decision 8) -- comparte cache con "
            "discover_federal_reglamentos.py por default (default: %(default)s, "
            "gitignored)"
        ),
    )
    parser.add_argument(
        "--json", type=Path, default=None, metavar="ARCHIVO",
        help="ademas de imprimir la lista, escribela como JSON en ARCHIVO",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    candidatos = discover(
        ScjnApi(espera=args.espera),
        categoria=CATEGORIA,
        frases_union=FRASES_UNION,
        frase_rescate=FRASE_RESCATE,
        auditoria_cobertura=args.auditoria_cobertura,
        frases_auditoria=FRASES_AUDITORIA_COBERTURA,
        cache_dir=args.cache_dir,
        log=log,
    )

    log(f"\ndescubrimiento: {len(candidatos)} lineamiento(s) -- NO se escribio nada:")
    for c in candidatos:
        log(
            f"    {c['id_ordenamiento']}  {c['categoria_ordenamiento']}  "
            f"{c['vigencia']}  {c['reformas']} reforma(s), {c['reformas_con_texto']} con texto"
        )
        log(f"        {c['nombre']}")
    log(
        "  revisa la lista; escribir el estado.json de cada uno es "
        "scripts/seed_federal_reglamentos.py --coleccion lineamientos, y no se hace aqui"
    )

    if args.json:
        args.json.write_text(
            json.dumps(candidatos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log(f"\nlista escrita en {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
