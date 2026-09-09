#!/usr/bin/env python3
"""Discover every federal *reglamento* the SCJN lists and report a reviewable
seed list for the ``scjn-reglamentos`` corpus (issue #220, Fase 1) -- and
stop: this script never writes anything, exactly like
``discover_federal_laws.py``.

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
   (`discover_by_category`): ``q="reglamento"`` alone reaches 1080 of the
   1081 instruments the SCJN classifies REGLAMENTO (99.9%, measured
   2026-09-09); a handful of short, common Spanish words catch the
   straggler whose title happens not to contain "reglamento" at all.
2. **The reform-category rescue** (`rescue_by_reform_category`), applied to
   every *other* federal hit of ``q="reglamento"`` (187 of them, measured
   2026-09-09): each one's own reform table is fetched once and checked for
   a REGLAMENTO row. Six are genuine rescues; the rest (mostly ~174 acuerdos
   generales of the CJF/INE that merely *reglamentan* something) are
   correctly rejected.

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
above are what a routine discovery run needs.

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

from scjn.api import Ordenamiento, ScjnApi  # noqa: E402

#: `BusquedaFrase` has no "list everything" mode, so the REGLAMENTO category
#: is paged by phrase union instead -- "reglamento" alone reaches 1080/1081
#: (issue #220); the rest are short, common Spanish words that catch a
#: title not containing "reglamento" at all.
FRASES_UNION_REGLAMENTO = ("reglamento", "de", "a", "la", "y", "para", "el")

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


def _pagina_categoria(api: ScjnApi, frase: str, categoria: str, *, tamanio_pagina: int = 100):
    """Every hit of `frase` under `categoria`+FEDERAL, paged to the end --
    the one pattern every pass below shares."""
    pagina = 1
    while True:
        lote = api.search_ordenamiento(
            frase, tamanio_pagina=tamanio_pagina, categoria=categoria,
            ambito="FEDERAL", pagina=pagina,
        )
        yield from lote
        if len(lote) < tamanio_pagina:
            return
        pagina += 1


def discover_by_category(api: ScjnApi, *, log=None) -> dict[str, Ordenamiento]:
    """Every federal instrument the SCJN itself classifies REGLAMENTO,
    keyed by `idOrdenamiento` -- the union of `FRASES_UNION_REGLAMENTO`
    paged over `categoriaF=REGLAMENTO`+`FEDERAL` (issue #220)."""
    hallados: dict[str, Ordenamiento] = {}
    for frase in FRASES_UNION_REGLAMENTO:
        nuevos = 0
        for hit in _pagina_categoria(api, frase, "REGLAMENTO"):
            if hit.idOrdenamiento not in hallados:
                nuevos += 1
            hallados[hit.idOrdenamiento] = hit
        if log:
            log(f"  q={frase!r}: {nuevos} nuevo(s), union {len(hallados)}")
    return hallados


def candidates_outside_category(
    api: ScjnApi, ya_reglamento: dict[str, Ordenamiento], *, log=None
) -> dict[str, Ordenamiento]:
    """Every other federal hit of `q="reglamento"` -- the ~187
    non-REGLAMENTO instruments the rescue rule is applied to (issue #220)."""
    hallados: dict[str, Ordenamiento] = {}
    for hit in _pagina_categoria(api, "reglamento", ""):
        if hit.idOrdenamiento in ya_reglamento:
            continue
        hallados[hit.idOrdenamiento] = hit
    if log:
        log(f"  {len(hallados)} instrumento(s) federal(es) fuera de la categoria REGLAMENTO")
    return hallados


def _tiene_reforma_reglamento(api: ScjnApi, id_ordenamiento: str) -> bool:
    """Whether any row of `id_ordenamiento`'s own reform table is
    classified REGLAMENTO by the SCJN -- the rescue half of the inclusion
    rule (issue #220)."""
    reformas = api.reformas_of_ordenamiento(id_ordenamiento)
    return any((r.categoria or "").strip().upper() == "REGLAMENTO" for r in reformas)


def rescue_by_reform_category(
    api: ScjnApi, candidatos: dict[str, Ordenamiento], *, log=None
) -> dict[str, Ordenamiento]:
    """Every one of `candidatos` (keyed by `idOrdenamiento`, none of them
    already classified REGLAMENTO) whose own reform table has a REGLAMENTO
    row somewhere in its history -- one request per candidate (issue #220's
    rescue rule). A title merely containing the word "reglamento" is never
    enough on its own; only the SCJN's own reform-table classification is."""
    rescatados: dict[str, Ordenamiento] = {}
    for i, (id_ordenamiento, hit) in enumerate(sorted(candidatos.items()), 1):
        if log:
            log(
                f"  [{i}/{len(candidatos)}] {hit.ordenamiento[:70]!r} "
                f"({hit.categoriaOrdenamiento})"
            )
        if _tiene_reforma_reglamento(api, id_ordenamiento):
            rescatados[id_ordenamiento] = hit
    return rescatados


def coverage_audit(
    api: ScjnApi, ya_incluidos: set, *, log=None
) -> dict[str, Ordenamiento]:
    """The one-time exhaustive sweep (decision 5): every *other* federal
    instrument (~7900, measured 2026-09-09), reached by paging
    `FRASES_AUDITORIA_COBERTURA` the same way `discover_federal_laws.py`
    pages LEY/CODIGO/CONSTITUCION, checked for a REGLAMENTO reform row.

    Expensive (~65 minutes at the default `--espera`) and meant to run
    once, not on a schedule -- finding nothing new is itself the audit's
    result, not evidence it did not run."""
    candidatos: dict[str, Ordenamiento] = {}
    for frase, categoria in FRASES_AUDITORIA_COBERTURA:
        for hit in _pagina_categoria(api, frase, categoria):
            if hit.idOrdenamiento not in ya_incluidos:
                candidatos[hit.idOrdenamiento] = hit
    if log:
        log(
            f"  {len(candidatos)} instrumento(s) fuera de REGLAMENTO a revisar "
            "por fila de reforma"
        )
    return rescue_by_reform_category(api, candidatos, log=log)


def discover(
    api: ScjnApi | None = None, *, auditoria_cobertura: bool = False, log=print
) -> list[dict]:
    """The whole discovery pipeline (issue #220, Fase 1): every federal
    reglamento the SCJN lists, each carrying its own `id_ordenamiento`,
    `nombre`, `categoria_ordenamiento`, `vigencia`, `materia`, `resumen` and
    reform count -- sorted by `nombre`, ready for a human to review before
    `seed_federal_reglamentos.py` turns it into `estado.json` files.

    Never writes anything. See the module docstring for what each pass
    does and why the coverage audit is opt-in."""
    api = api or ScjnApi()

    log("descubriendo por categoria REGLAMENTO...")
    por_categoria = discover_by_category(api, log=log)
    log(f"{len(por_categoria)} instrumento(s) clasificados REGLAMENTO por la SCJN")

    log("buscando candidatos fuera de la categoria REGLAMENTO...")
    otros = candidates_outside_category(api, por_categoria, log=log)
    log(f"aplicando la regla de rescate a {len(otros)} candidato(s)...")
    rescatados = rescue_by_reform_category(api, otros, log=log)
    log(f"{len(rescatados)} rescatado(s) por categoria de reforma")

    incluidos = {**por_categoria, **rescatados}

    if auditoria_cobertura:
        log(
            "auditoria de cobertura (decision 5, ~65 min una sola vez): "
            "barriendo el resto del universo federal..."
        )
        extra = coverage_audit(api, set(incluidos), log=log)
        log(f"{len(extra)} instrumento(s) adicionales encontrados por la auditoria")
        incluidos.update(extra)

    candidatos = []
    for id_ordenamiento, hit in incluidos.items():
        reformas = api.reformas_of_ordenamiento(id_ordenamiento)
        candidatos.append({
            "id_ordenamiento": id_ordenamiento,
            "nombre": hit.ordenamiento,
            "categoria_ordenamiento": hit.categoriaOrdenamiento,
            "vigencia": hit.vigencia,
            "materia": hit.materia,
            "resumen": hit.resumen,
            "reformas": len(reformas),
        })
    return sorted(candidatos, key=lambda c: c["nombre"])


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
        "--json", type=Path, default=None, metavar="ARCHIVO",
        help="ademas de imprimir la lista, escribela como JSON en ARCHIVO",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    candidatos = discover(
        ScjnApi(espera=args.espera), auditoria_cobertura=args.auditoria_cobertura, log=log
    )

    log(f"\ndescubrimiento: {len(candidatos)} reglamento(s) -- NO se escribio nada:")
    for c in candidatos:
        log(
            f"    {c['id_ordenamiento']}  {c['categoria_ordenamiento']}  "
            f"{c['vigencia']}  {c['reformas']} reforma(s)"
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
