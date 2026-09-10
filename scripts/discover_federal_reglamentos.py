#!/usr/bin/env python3
"""Discover every federal *reglamento* the SCJN lists and report a reviewable
seed list for the ``scjn-reglamentos`` corpus (issue #220, Fase 1) -- and
stop: this script never writes anything, exactly like
``discover_federal_laws.py``.

A thin wrapper (issue #222's Fase 0) over `scjn.discovery`, naming this
collection's own category and phrases; the phrase-union paging, the
reform-category rescue rule and the coverage audit itself live there,
shared with ``discover_federal_lineamientos.py``.

    ./scripts/discover_federal_reglamentos.py
    ./scripts/discover_federal_reglamentos.py --json candidatos.json
    ./scripts/discover_federal_reglamentos.py --auditoria-cobertura   # ~65 min, once

## Inclusion rule

An instrument is in the corpus when ``categoriaOrdenamiento == "REGLAMENTO"``,
or any row of its own reform table has ``categoriaReforma == "REGLAMENTO"``.
The second half is load-bearing (issue #220): 6 instruments the SCJN
classifies ``ACUERDO (S)``/``ESTATUTO``/``RESULTADOS`` are rescued this way,
while none of the other 181 non-REGLAMENTO federal hits for the phrase
"reglamento" is.

## Two passes, one rule

1. **The union of a phrase search over ``categoriaF=REGLAMENTO``+``FEDERAL``**
   (`scjn.discovery.discover_by_category`): ``q="reglamento"`` alone reaches
   1080 of the 1081 instruments the SCJN classifies REGLAMENTO (99.9%,
   measured 2026-09-09); a handful of short, common Spanish words catch the
   straggler whose title happens not to contain "reglamento" at all.
2. **The reform-category rescue** (`scjn.discovery.rescue_by_reform_category`),
   applied to every *other* federal hit of ``q="reglamento"`` (187 of them,
   measured 2026-09-09): each one's own reform table is fetched once and
   checked for a REGLAMENTO row. Six are genuine rescues; the rest (mostly
   ~174 acuerdos generales of the CJF/INE that merely *reglamentan*
   something) are correctly rejected.

## The one-time coverage audit (decision 5, ``--auditoria-cobertura``)

The two passes above only ever look at instruments the phrase "reglamento"
already turns up. An instrument whose title never says "reglamento" at all,
but whose reform table the SCJN classifies as one somewhere in its history,
would be invisible to them -- so ``--auditoria-cobertura`` sweeps every
*other* federal instrument's reform table too (~7900 of them, measured
2026-09-09, at ~2 requests/second with the default ``--espera`` -> ~65
minutes) and applies the same rescue rule. This is exhaustive, one-time, and
its result is meant to be frozen into whatever it finds -- it is not re-run
on a schedule, and finding nothing new is itself the audit's result, not a
reason to distrust it. Off by default because of its cost; the two passes
above are what a routine discovery run needs. Since issue #222's decision 8,
every reform table this sweep fetches is cached under ``--cache-dir``
(``scjn.discovery.coverage_audit``), so a sibling collection's own audit
(``discover_federal_lineamientos.py --auditoria-cobertura``) reuses this
sweep's tables instead of paying for them twice.

## Reports and stops, never writes anything

Same posture as ``discover_federal_laws.py``: 1087 reglamentos is too many
to hand-review one at a time the way ~315 laws were, so the seed list this
prints (or, with ``--json``, writes as a reviewable file) is Fase 1's whole
deliverable. Turning it into ``estado.json`` files is Fase 2
(``seed_federal_reglamentos.py``); nothing here writes corpus state.

Needs network to the SCJN (the faceted search plus, for the rescue rule and
the coverage audit, one ``Reforma`` request per candidate). No DOF
confirmation step at all (issue #220's own Scope) -- unlike
``discover_federal_laws.py``, a reglamento's own reform table is what
confirms it, not the DOF titles stream.
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

CATEGORIA = "REGLAMENTO"

#: `BusquedaFrase` has no "list everything" mode, so the REGLAMENTO category
#: is paged by phrase union instead -- "reglamento" alone reaches 1080/1081
#: (issue #220); the rest are short, common Spanish words that catch a
#: title not containing "reglamento" at all.
FRASES_UNION = ("reglamento", "de", "a", "la", "y", "para", "el")

#: The one phrase whose *other-category* hits the rescue rule is applied to.
FRASE_RESCATE = "reglamento"

#: Every other federal category worth sweeping for the coverage audit
#: (decision 5) -- the same "the category's own word is the phrase" trick
#: `discover_federal_laws.py` uses for LEY/CODIGO/CONSTITUCION, extended to
#: every category the federal universe carries (issue #220's own table),
#: except REGLAMENTO itself (already covered above) and TRATADO (measured
#: zero federal instruments -- and out of scope regardless, issue #220's
#: Scope).
FRASES_AUDITORIA_COBERTURA = (
    ("ley", "LEY"), ("codigo", "CODIGO"), ("constitucion", "CONSTITUCION"),
    ("acuerdo", "ACUERDO"), ("estatuto", "ESTATUTO"), ("lineamientos", "LINEAMIENTOS"),
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
            "federal (~7900 instrumentos, ~65 min) buscando un REGLAMENTO "
            "invisible al titulo (decision 5, issue #220) -- una sola vez"
        ),
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path(__file__).resolve().parent / "scjn",
        help=(
            "donde cachear las tablas de reforma que la auditoria de cobertura "
            "barre (issue #222's decision 8), para que una coleccion hermana no "
            "las vuelva a pagar (default: %(default)s, gitignored)"
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

    log(f"\ndescubrimiento: {len(candidatos)} reglamento(s) -- NO se escribio nada:")
    for c in candidatos:
        log(
            f"    {c['id_ordenamiento']}  {c['categoria_ordenamiento']}  "
            f"{c['vigencia']}  {c['reformas']} reforma(s), {c['reformas_con_texto']} con texto"
        )
        log(f"        {c['nombre']}")
    log(
        "  revisa la lista; escribir el estado.json de cada uno es "
        "scripts/seed_federal_reglamentos.py, y no se hace aqui"
    )

    if args.json:
        args.json.write_text(
            json.dumps(candidatos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log(f"\nlista escrita en {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
