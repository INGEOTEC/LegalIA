#!/usr/bin/env python3
"""Discover every international *treaty* the SCJN serves and report a
reviewable seed list for the ``scjn-tratados`` corpus (issue #277) -- and
stop: this script never writes corpus state, exactly like
``discover_federal_reglamentos.py``/``discover_federal_lineamientos.py``.

A thin wrapper over `scjn.discovery`, naming this collection's own ambito and
phrases.

    ./scripts/discover_federal_tratados.py
    ./scripts/discover_federal_tratados.py --json candidatos.json

## Inclusion rule: by ambito, not by category

Treaties are not federal instruments in the SCJN's own classification: they
carry ``ambito == "TRATADOS INTERNACIONALES"`` (the ``FEDERAL`` hits of the
word "tratado" are e.g. the *Ley sobre la Celebracion de Tratados*, not
treaties). An instrument is in the corpus when it is in that ambito, **any**
category -- measured 2026-10-08: 1,456 instruments over 23 categories
(CONVENIO 556, ACUERDO (S) 386, CONVENCION 221, TRATADO 153, PROTOCOLO 41,
PROYECTO 18, ACTA 16, CODIGO 11, ESTATUTO 11, CONSTITUCION 8, ARREGLO 8,
REGLAMENTO 6, DECLARACION 5, CONGRESO 3, CARTA 2, PACTO 2, NOTA(S) 2,
MEMORANDUM 2, AVISO 1, REGLA (S) 1, DECRETO 1, BASES 1, MANDATO 1), every
sampled oddity a genuine international instrument (CODIGO = IMO safety codes,
CONSTITUCION = of international organisations, CONGRESO = Universal Postal
Union decisions, PROYECTO = ILO draft conventions). A category whitelist
would have dropped 386 ACUERDO (S) and ~80 others.

## The phrases only page, they never decide

``BusquedaFrase`` has no "list everything" mode, so the ambito is paged by
a union of phrases; membership is the ambito alone. Yield per phrase, in this
order (measured 2026-10-08): tratado 168, convenio 587, convencion 226,
arreglo 10, carta 6, declaracion 6, pacto 3, protocolo 27, **acuerdo 371**,
de 49, a 1, la 1, y 0, para 0, el 1 -- 77 pages, ~110 s at ``--espera 0.3``.
"acuerdo" is the one content word nothing else reaches; the grammatical
articles are the ones the sibling scripts already use ("de" alone catches 49
nobody else does).

## No rescue, no coverage audit

Reglamentos'/lineamientos' rescue rule and coverage audit are predicates on
a reform row's category; a reform row carries no ambito, so neither can say
"international" (hence no ``--auditoria-cobertura`` flag here). Instead this
script *reports* -- under its own heading, never in the ``--json`` list --
the instruments **outside** the ambito whose own ``categoriaOrdenamiento``
is a treaty word (TRATADO, CONVENIO, ...), for a human to look at.

## Reports and stops

The seed list this prints (or, with ``--json``, writes as a reviewable file)
is the whole deliverable; turning it into ``estado.json`` files is
``seed_federal_reglamentos.py --coleccion tratados``. Needs network to the
SCJN (the faceted search plus one ``Reforma`` request per candidate). No DOF
linking at all (issue #220's Scope, unchanged).
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import ScjnApi  # noqa: E402
from scjn.discovery import discover, report_outside_ambito  # noqa: E402

AMBITO = "TRATADOS INTERNACIONALES"

#: Membership is by ambito, so there is no category to page: `""` is the
#: by-ambito mode of `scjn.discovery.discover`.
CATEGORIA = ""

#: Phrases that page the ambito (measured 2026-10-08, see the module
#: docstring). They never decide membership.
FRASES_UNION = (
    "tratado", "convenio", "convencion", "arreglo", "carta", "declaracion",
    "pacto", "protocolo", "acuerdo", "de", "a", "la", "y", "para", "el",
)

#: The categories that, *outside* the ambito, are worth a human's glance
#: (never included): the SCJN's own `categoriaOrdenamiento` says treaty.
CATEGORIAS_FUERA_DE_AMBITO = (
    "TRATADO", "CONVENIO", "CONVENCION", "PROTOCOLO", "ARREGLO", "CARTA",
    "DECLARACION", "PACTO",
)

#: Phrases the outside-ambito report pages over every ambito.
FRASES_FUERA_DE_AMBITO = (
    "tratado", "convenio", "convencion", "protocolo", "arreglo", "carta",
    "declaracion", "pacto",
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
        "--json", type=Path, default=None, metavar="ARCHIVO",
        help="ademas de imprimir la lista, escribela como JSON en ARCHIVO",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    api = ScjnApi(espera=args.espera)
    candidatos = discover(
        api,
        categoria=CATEGORIA,
        frases_union=FRASES_UNION,
        ambito=AMBITO,
        log=log,
    )

    log("\nbuscando instrumentos FUERA del ambito con categoria de tratado (solo informe)...")
    fuera = report_outside_ambito(
        api, AMBITO, CATEGORIAS_FUERA_DE_AMBITO, FRASES_FUERA_DE_AMBITO, log=log
    )

    log(f"\ndescubrimiento: {len(candidatos)} tratado(s) -- NO se escribio nada:")
    for c in candidatos:
        log(
            f"    {c['id_ordenamiento']}  {c['categoria_ordenamiento']}  "
            f"{c['vigencia']}  {c['reformas']} reforma(s), {c['reformas_con_texto']} con texto"
        )
        log(f"        {c['nombre']}")

    log(
        f"\nFUERA DEL AMBITO {AMBITO} (informe para revision humana, NO incluidos): "
        f"{len(fuera)} instrumento(s)"
    )
    for hit in fuera:
        log(f"    {hit.idOrdenamiento}  {hit.ambito}  {hit.categoriaOrdenamiento}  {hit.vigencia}")
        log(f"        {hit.ordenamiento}")

    log(
        "\n  revisa la lista; escribir el estado.json de cada uno es "
        "scripts/seed_federal_reglamentos.py --coleccion tratados, y no se hace aqui"
    )

    if args.json:
        args.json.write_text(
            json.dumps(candidatos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log(f"\nlista escrita en {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
