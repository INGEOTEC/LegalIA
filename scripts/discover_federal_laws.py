#!/usr/bin/env python3
"""Report federal laws the SCJN lists that the `scjn-leyes` release does not
have yet, and stop -- the one part of the retired `extract_scjn_titles.py
--discover` with no replacement (issue #210, Fase 4 of #206).

    ./scripts/discover_federal_laws.py

Needs the release's own reverse index already on disk (`scjn download`, or
any prior `nota2md`/`scjn` command that populated it) plus network to the
SCJN (the faceted search) and the `notas-archivo` cache for the DOF
confirmation.

`BusquedaFrase` has no "list everything" mode -- an empty `q` answers zero
results -- so this pages a phrase search per category of the `leyes` group
instead, using the category's own word as the phrase. Every federal
LEY/CÓDIGO/CONSTITUCIÓN has that word in its title by construction, and the
SCJN's own `CODIGO` category alone would otherwise contribute ~180 "CÓDIGO
DE CONDUCTA DE ..." administrative documents that are not laws -- which is
why every candidate is confirmed against the DOF the same way an existing
law's own `actualizado_dof` is (a same-day title that names it and opens
with DECRETO/LEY) before being reported.

**Reports and stops, never writes anything.** A new federal law is a
handful a year, and a wrong `abrev` renames a release asset and orphans it
(#186), so the decision to add one is always a human's. Each candidate
prints a suggested `abrev` (`scjn.catalog.mint_abrev`), to be written down
once -- by hand-creating `<outdir>/leyes/<abrev-slug>/estado.json` with at
least `abrev`/`nombre` before the first crawl -- and never recomputed.
"""

import argparse
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "nota2md"))
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from dofjson.titulos import legal_provisions_titles  # noqa: E402
from nota2md.linking import newest_dof_publication_dates  # noqa: E402
from scjn.api import ScjnApi, grupo_de_categoria  # noqa: E402
from scjn.catalog import mint_abrev, slugify  # noqa: E402
from scjn.release import download_scjn_leyes_catalog  # noqa: E402

#: `BusquedaFrase` has no "list everything" mode, so discovery pages a phrase
#: search per category instead, using the category's own word as the phrase.
_FRASES_DESCUBRIMIENTO = (("ley", "LEY"), ("codigo", "CODIGO"), ("constitucion", "CONSTITUCION"))


def discover(
    catalogo: list[dict], *, api: ScjnApi | None = None, cache_dir=None, log=print
) -> list[dict]:
    """Federal laws the SCJN lists that `catalogo` does not have, each
    confirmed against the DOF and carrying a suggested `abrev`.

    Never writes anything. See the module docstring for why the DOF
    confirmation is not optional."""
    api = api or ScjnApi()
    conocidos = {slugify(e["abrev"]) for e in catalogo}
    nombres_conocidos = {e["nombre"].strip().upper() for e in catalogo}

    hallados: dict[str, str] = {}
    for frase, categoria in _FRASES_DESCUBRIMIENTO:
        pagina = 1
        while True:
            lote = api.search_ordenamiento(
                frase, tamanio_pagina=100, categoria=categoria, ambito="FEDERAL",
                vigencia="VIGENTE", pagina=pagina,
            )
            for hit in lote:
                # `grupo_de_categoria` is the SCJN's own answer to "is this a
                # ley/código at all"; the filter is cheap insurance against
                # the category filter being ignored by the API.
                if grupo_de_categoria(hit.categoriaOrdenamiento) != "ley":
                    continue
                hallados[hit.ordenamiento.strip().upper()] = hit.idOrdenamiento
            if len(lote) < 100:
                break
            pagina += 1

    desconocidos = {n: i for n, i in hallados.items() if n not in nombres_conocidos}
    desconocidos = {
        n: i for n, i in desconocidos.items() if slugify(n) not in conocidos
    }
    if not desconocidos:
        return []

    log(f"  confirmando {len(desconocidos)} candidato(s) contra el DOF...")
    confirmadas = newest_dof_publication_dates(
        {n: n for n in desconocidos},
        legal_provisions_titles(log=(lambda *_a, **_k: None))
        if cache_dir is None
        else legal_provisions_titles(cache_dir, log=(lambda *_a, **_k: None)),
    )

    tomados = set(conocidos)
    candidatos = []
    for nombre in sorted(confirmadas):
        abrev = mint_abrev(nombre, tomados)
        tomados.add(abrev)
        candidatos.append({
            "nombre": nombre,
            "abrev": abrev,
            "actualizado": confirmadas[nombre],
            "idOrdenamiento": desconocidos[nombre],
        })
    return candidatos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="scjn-leyes release cache (scjn.cache.CACHE_DIR when not given) -- "
             "must already have indice-global.json.gz; run `scjn download` first",
    )
    parser.add_argument(
        "--dof-cache-dir", default=None, metavar="DIR",
        help="notas-archivo cache the DOF confirmation reads titles from "
             "(dofjson.titulos.CACHE_DIR when not given)",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    catalogo = download_scjn_leyes_catalog(freshness=False, cache_dir=args.cache_dir)
    candidatos = discover(catalogo, cache_dir=args.dof_cache_dir, log=log)
    if not candidatos:
        log("descubrimiento: ninguna ley federal nueva confirmada contra el DOF")
        return 0
    log(f"descubrimiento: {len(candidatos)} candidato(s) -- NO se escribieron:")
    for c in candidatos:
        log(f"    {c['abrev']}  {c['actualizado']}  (idOrdenamiento {c['idOrdenamiento']})")
        log(f"        {c['nombre']}")
    log(
        "  revisa cada uno; el 'abrev' sugerido se escribe una vez, a mano, en "
        "<outdir>/leyes/<slug>/estado.json (abrev/nombre) antes del primer rastreo, "
        "y no se vuelve a calcular"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
