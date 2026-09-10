"""Shared discovery machinery for the SCJN's id-keyed collections
(`scjn-reglamentos`, `scjn-lineamientos`) -- issue #222's Fase 0, extracted
out of `scripts/discover_federal_reglamentos.py` (issue #220) so a third
collection's own discovery script is a thin argparse wrapper naming its
category and phrases, not another ~250-line copy of the same three passes.

The shared shape, for any id-keyed collection's own target category (e.g.
`"REGLAMENTO"`, `"LINEAMIENTOS"`):

1. **`discover_by_category`** -- page `categoriaF=<categoria>`+`ambitoF=FEDERAL`
   with a union of phrases (`BusquedaFrase` has no "list everything" mode).
2. **`candidates_outside_category`** -- every *other* federal hit of one
   rescue phrase (e.g. `"reglamento"`, `"lineamientos"`), federal, any
   category.
3. **`rescue_by_reform_category`** -- of those candidates, the ones whose own
   reform table has a row classified `<categoria>` somewhere in its history
   -- a title merely containing the phrase is never enough on its own.
4. **`coverage_audit`** -- the opt-in, one-time, exhaustive sweep of every
   *other* federal instrument's reform table (issue #220's decision 5,
   #222's decision 8), for an instrument whose title never mentions the
   category's own word at all. Expensive (~65-70 minutes at the crawler's
   default `--espera`), so it caches the reform tables it fetches under a
   caller-given `cache_dir` (gitignored scratch, e.g. `scripts/scjn/`, never
   a release asset) -- a later collection's own sweep replays the same
   tables offline against its own predicate instead of paying the cost
   again.

Every function takes an `api` object exposing only
`search_ordenamiento`/`reformas_of_ordenamiento` (an `scjn.api.ScjnApi`, or a
stub in a test) -- nothing here is network code itself, which is what makes
it unit-testable without the SCJN."""

import gzip
import json
from pathlib import Path

from scjn.api import Ordenamiento, ScjnApi

#: How often `coverage_audit` flushes its cache to disk mid-sweep -- an
#: interrupted ~70-minute sweep should not lose more than this many
#: already-fetched reform tables.
_CADA_CUANTOS_GUARDA_CACHE = 50


def pagina_categoria(
    api: ScjnApi, frase: str, categoria: str, *, ambito: str = "FEDERAL", tamanio_pagina: int = 100
):
    """Every hit of `frase` under `categoria`+`ambito`, paged to the end --
    the one pattern every pass below shares. `categoria=""` searches every
    category (used by `candidates_outside_category` and the coverage audit's
    own paging is done phrase-by-category instead, see below)."""
    pagina = 1
    while True:
        lote = api.search_ordenamiento(
            frase, tamanio_pagina=tamanio_pagina, categoria=categoria,
            ambito=ambito, pagina=pagina,
        )
        yield from lote
        if len(lote) < tamanio_pagina:
            return
        pagina += 1


def discover_by_category(
    api: ScjnApi, categoria: str, frases: tuple[str, ...], *, log=None
) -> dict[str, Ordenamiento]:
    """Every federal instrument the SCJN itself classifies `categoria`, keyed
    by `idOrdenamiento` -- the union of `frases` paged over
    `categoriaF=<categoria>`+`FEDERAL`."""
    hallados: dict[str, Ordenamiento] = {}
    for frase in frases:
        nuevos = 0
        for hit in pagina_categoria(api, frase, categoria):
            if hit.idOrdenamiento not in hallados:
                nuevos += 1
            hallados[hit.idOrdenamiento] = hit
        if log:
            log(f"  q={frase!r}: {nuevos} nuevo(s), union {len(hallados)}")
    return hallados


def candidates_outside_category(
    api: ScjnApi, frase: str, ya_incluidos: dict[str, Ordenamiento], *, log=None
) -> dict[str, Ordenamiento]:
    """Every other federal hit of `frase`, across every category, not
    already in `ya_incluidos` -- the candidates the reform-category rescue
    rule is applied to."""
    hallados: dict[str, Ordenamiento] = {}
    for hit in pagina_categoria(api, frase, ""):
        if hit.idOrdenamiento in ya_incluidos:
            continue
        hallados[hit.idOrdenamiento] = hit
    if log:
        log(f"  {len(hallados)} instrumento(s) federal(es) fuera de la categoria objetivo")
    return hallados


def _tiene_reforma_de_categoria(reformas, categoria: str) -> bool:
    """Whether any row of a reform table (`list[Reforma]` or the cached
    `list[dict]` shape `coverage_audit` stores) is classified `categoria` by
    the SCJN -- the rescue rule's own predicate, shared by
    `rescue_by_reform_category` and `coverage_audit`."""
    for r in reformas:
        valor = r.categoria if hasattr(r, "categoria") else r.get("categoria")
        if (valor or "").strip().upper() == categoria:
            return True
    return False


def rescue_by_reform_category(
    api: ScjnApi, candidatos: dict[str, Ordenamiento], categoria: str, *, log=None
) -> dict[str, Ordenamiento]:
    """Every one of `candidatos` (keyed by `idOrdenamiento`, none of them
    already classified `categoria`) whose own reform table has a `categoria`
    row somewhere in its history -- one request per candidate. A title
    merely containing the rescue phrase is never enough on its own; only the
    SCJN's own reform-table classification is."""
    rescatados: dict[str, Ordenamiento] = {}
    for i, (id_ordenamiento, hit) in enumerate(sorted(candidatos.items()), 1):
        if log:
            log(
                f"  [{i}/{len(candidatos)}] {hit.ordenamiento[:70]!r} "
                f"({hit.categoriaOrdenamiento})"
            )
        reformas = api.reformas_of_ordenamiento(id_ordenamiento)
        if _tiene_reforma_de_categoria(reformas, categoria):
            rescatados[id_ordenamiento] = hit
    return rescatados


def _archivo_cache_auditoria(cache_dir: Path) -> Path:
    return Path(cache_dir) / "auditoria_cobertura.json.gz"


def _lee_cache_auditoria(cache_dir) -> dict:
    """The coverage audit's own cache: reform tables already swept, keyed by
    `idOrdenamiento`, reusable across collections and reruns
    (issue #222's decision 8). `cache_dir=None` (e.g. a unit test with no
    scratch directory of its own) means "cache nothing" -- every sweep
    starts cold and nothing is written back."""
    if cache_dir is None:
        return {"instrumentos": {}}
    archivo = _archivo_cache_auditoria(cache_dir)
    if not archivo.exists():
        return {"instrumentos": {}}
    with gzip.open(archivo, "rt", encoding="utf-8") as f:
        datos = json.load(f)
    datos.setdefault("instrumentos", {})
    return datos


def _guarda_cache_auditoria(cache_dir, datos: dict) -> None:
    if cache_dir is None:
        return
    archivo = _archivo_cache_auditoria(cache_dir)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    parcial = archivo.with_suffix(archivo.suffix + ".parcial")
    with gzip.open(parcial, "wt", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    parcial.replace(archivo)


def coverage_audit(
    api: ScjnApi,
    ya_incluidos: set,
    categoria: str,
    frases_auditoria: tuple[tuple[str, str], ...],
    *,
    cache_dir=None,
    log=None,
) -> dict[str, Ordenamiento]:
    """The one-time exhaustive sweep (issue #220's decision 5, #222's
    decision 8): every *other* federal instrument reached by paging
    `frases_auditoria` (a tuple of `(frase, categoria_de_pagina)` pairs, the
    same "the category's own word is the phrase" trick
    `discover_federal_laws.py` uses), checked for a `categoria` reform row.

    Expensive (~65-70 minutes at the default `--espera`) and meant to run
    once, not on a schedule -- finding nothing new is itself the audit's
    result, not evidence it did not run. Every reform table it fetches is
    cached under `cache_dir` (gitignored scratch, e.g. `scripts/scjn/`,
    never a release asset), keyed by `idOrdenamiento`: a *later* collection's
    own audit reuses an already-swept instrument's table straight from the
    cache and re-applies its own `categoria` predicate offline, rather than
    re-fetching a table this sweep already paid for. Flushed to disk every
    `_CADA_CUANTOS_GUARDA_CACHE` newly-fetched instruments, so an
    interrupted ~70-minute sweep loses at most that many."""
    cache = _lee_cache_auditoria(cache_dir)
    instrumentos_cache = cache["instrumentos"]

    candidatos: dict[str, Ordenamiento] = {}
    for frase, categoria_pagina in frases_auditoria:
        for hit in pagina_categoria(api, frase, categoria_pagina):
            if hit.idOrdenamiento not in ya_incluidos:
                candidatos[hit.idOrdenamiento] = hit
    if log:
        log(
            f"  {len(candidatos)} instrumento(s) fuera de la categoria objetivo a "
            "revisar por fila de reforma"
        )

    rescatados: dict[str, Ordenamiento] = {}
    nuevos_desde_ultimo_guardado = 0
    for i, (id_ordenamiento, hit) in enumerate(sorted(candidatos.items()), 1):
        if log:
            log(
                f"  [{i}/{len(candidatos)}] {hit.ordenamiento[:70]!r} "
                f"({hit.categoriaOrdenamiento})"
            )
        entrada = instrumentos_cache.get(id_ordenamiento)
        if entrada is None:
            reformas = [
                {"categoria": r.categoria, "tieneArticulos": r.tieneArticulos}
                for r in api.reformas_of_ordenamiento(id_ordenamiento)
            ]
            instrumentos_cache[id_ordenamiento] = {
                "hit": {
                    "idOrdenamiento": hit.idOrdenamiento,
                    "ordenamiento": hit.ordenamiento,
                    "categoriaOrdenamiento": hit.categoriaOrdenamiento,
                    "vigencia": hit.vigencia,
                    "materia": hit.materia,
                    "resumen": hit.resumen,
                },
                "reformas": reformas,
            }
            nuevos_desde_ultimo_guardado += 1
            if nuevos_desde_ultimo_guardado >= _CADA_CUANTOS_GUARDA_CACHE:
                _guarda_cache_auditoria(cache_dir, cache)
                nuevos_desde_ultimo_guardado = 0
        else:
            reformas = entrada["reformas"]
        if _tiene_reforma_de_categoria(reformas, categoria):
            rescatados[id_ordenamiento] = hit
    _guarda_cache_auditoria(cache_dir, cache)
    return rescatados


def discover(
    api: ScjnApi,
    *,
    categoria: str,
    frases_union: tuple[str, ...],
    frase_rescate: str,
    auditoria_cobertura: bool = False,
    frases_auditoria: tuple[tuple[str, str], ...] | None = None,
    cache_dir=None,
    log=print,
) -> list[dict]:
    """The whole discovery pipeline (issue #220's Fase 1, generalized in
    #222's Fase 0): every federal instrument of one id-keyed collection,
    each carrying its own `id_ordenamiento`, `nombre`, `categoria_ordenamiento`,
    `vigencia`, `materia`, `resumen`, reform count and reform-with-text
    count -- sorted by `nombre`, ready for a human to review before a
    seeding script turns it into `estado.json` files.

    `categoria` is the collection's own SCJN classification (`"REGLAMENTO"`,
    `"LINEAMIENTOS"`); `frases_union` the phrases `discover_by_category`
    pages that category with; `frase_rescate` the one phrase
    `candidates_outside_category` searches across every other category.
    `auditoria_cobertura=True` also runs the opt-in coverage audit (needs
    `frases_auditoria`); `cache_dir` is where it caches reform tables
    (see `coverage_audit`).

    Never writes corpus state -- a discovery script wrapping this reports
    the result and stops; turning it into `estado.json` files is a
    collection's own seeding script."""
    log(f"descubriendo por categoria {categoria}...")
    por_categoria = discover_by_category(api, categoria, frases_union, log=log)
    log(f"{len(por_categoria)} instrumento(s) clasificados {categoria} por la SCJN")

    log(f"buscando candidatos fuera de la categoria {categoria}...")
    otros = candidates_outside_category(api, frase_rescate, por_categoria, log=log)
    log(f"aplicando la regla de rescate a {len(otros)} candidato(s)...")
    rescatados = rescue_by_reform_category(api, otros, categoria, log=log)
    log(f"{len(rescatados)} rescatado(s) por categoria de reforma")

    incluidos = {**por_categoria, **rescatados}

    if auditoria_cobertura:
        if not frases_auditoria:
            raise ValueError("auditoria_cobertura=True necesita frases_auditoria")
        log(
            "auditoria de cobertura (opt-in, issue #220 decision 5 / #222 decision 8): "
            "barriendo el resto del universo federal..."
        )
        extra = coverage_audit(
            api, set(incluidos), categoria, frases_auditoria, cache_dir=cache_dir, log=log
        )
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
            "reformas_con_texto": sum(1 for r in reformas if r.tieneArticulos),
        })
    return sorted(candidatos, key=lambda c: c["nombre"])
