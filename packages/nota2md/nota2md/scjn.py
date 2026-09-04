"""The `scjn-leyes` release's own readers, plus the two DOF-keyed lookups
that resolve a `codNota` to the snapshot it published
(`localiza_codNota`/`snapshot_de_codNota`) -- these stay here rather than in
the `scjn` package because the release readers need this package's own
on-disk cache (`nota2md.cache`) and a `codNota` is a DOF concept. The
`codNota` **linking** step itself -- matching a snapshot to the `codNota` in
the first place -- now lives in `nota2md.linking` (issue #208); this module
calls it for none of its own work. Catalogue slugs, crawl state and the
provenance header's reader live in `scjn.catalog`/`scjn.state`/`scjn.header`,
re-exported here for this phase only (issue #207); the crawl itself lives in
`scjn.api`, against the SCJN's JSON API (`/SCOW-API`, issue #172).

Until issue #179 this module also carried the crawler for the legacy
WebForms Buscador (`/Buscador/`): a search POST round-tripping
`__VIEWSTATE`/`__EVENTVALIDATION`, a detail page whose `q` token was scoped
to the session that requested it, a reform grid paged through
`__EVENTTARGET`, and one `.docx` download per row parsed by
`docx_a_markdown`. All of it is gone. What replaced it, and why:

- The old Buscador simply did not index everything. Searching it for the
  LEY Federal de Cine y el Audiovisual returned 0 candidates, twice, live
  (issue #124's "Mecanismo 2"); the JSON API answers with
  `idOrdenamiento` 188805 for the same name. That law is now in the corpus.
- An instrument is addressable by a stable `idOrdenamiento` instead of a
  session-scoped URL, so a crawl is resumable and auditable, and the whole
  reform table arrives in one request instead of 31 postbacks.
- The per-reform article text arrives already segmented, so the heuristic
  paragraph classifier that read the `.docx` is now only the formatter
  `scjn.api` applies to it (`scjn.api._formatea_parrafo`).

The API is still **not** an official contract — a Swagger page is not a
stability promise, the same posture `dofjson.dofweb` takes toward the DOF's
own website, and the rate limiting stays. And the SCJN is
still not an official source of legal text: dof.gob.mx/SIDOF remains that
(the SCJN's own site marks its editorial insertions as "N. DE E." — Nota de
Editor). Every Markdown file the crawl writes is therefore tagged with a
`fuente: scjn` header, whose meaning this migration does not change, so it
is never mistaken for text reconstructed from the DOF's own notes — see
nota2md.leyes.reconstruct_legal_provisions, the DOF-only equivalent this
crawl stands in for once matched by date to a codNota — which
`legal_provisions` now does by default, through this module's
`snapshot_de_codNota` (issue #117).
"""

import gzip
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path

import requests

from nota2md.cache import (
    SIN_CACHE_DIR,
    asset_en_cache,
    bytes_de_asset,
    resuelve_cache_dir,
)
from scjn.catalog import (  # noqa: F401  (re-exported for this phase only, issue #207)
    apply_actualizado,
    catalog_key,
    instrument_up_to_date,
    iso_date_from_note,
    merge_catalog_overrides,
    merge_catalog_with_previous,
    mint_abrev,
    search_name,
    slug_instrumento,
    slugify,
)
from scjn.header import (  # noqa: F401  (re-exported for this phase only, issue #207)
    VersionInstrumento,
    _fecha,
    lee_cabecera,
    versiones_de_directorio,
)
from scjn.state import (  # noqa: F401  (re-exported for this phase only, issue #207)
    ARCHIVO_ESTADO,
    PENDIENTE_CAMBIO,
    PENDIENTE_NUNCA_RASTREADO,
    PENDIENTE_SIN_ACTUALIZADO,
    escribe_estado,
    lee_estado,
    motivo_pendiente,
)
from scjn.text import (  # noqa: F401  (re-exported for this phase only, issue #207)
    UMBRAL_CONFIANZA_SIMILITUD,
    UMBRAL_MINIMO_SIMILITUD,
    _normaliza,
    es_acuerdo_interno,
    grupo_instrumento,
    quita_notas_editoriales,
    ratio_similitud,
)

#: User-Agent for the `scjn-leyes` release's own GitHub requests -- the only
#: network this module still does on its own (the SCJN crawl lives in
#: `scjn.api`, which carries its own headers).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-nota2md/1.0)"}


# --- issues #128/#117: read the packaged corpus (release loaders) --------
#
# `scripts/empaqueta_scjn_leyes.py` packages every already-crawled+linked
# `leyes` instrument (snapshots plus `indice.json`, carrying #115/#126/#127's
# confidence signals, plus the DOF notes each link was decided against) into
# one `<slug>.tgz` asset per law of the `scjn-leyes` release — see
# that script for why publishing it is, and stays, a deliberate manual step,
# never automated. These are that release's own readers, same shape as
# the reader of the retired `historial-legislativo` release, but deliberately
# never shared code with it: the two releases had different tags, different
# asset layouts and no caller in common. That reader is gone (#187); these
# are what a law's reform history is read through now.
#
# Three of them, in the order a caller reaches for them:
# `download_scjn_leyes_index` (the reverse index, a few hundred KB),
# `snapshot_de_codNota` (one reform's consolidated law text, what
# `legal_provisions` dispatches to) and `download_scjn_leyes_corpus` (a whole
# law, snapshots and links, for auditing the corpus itself). All three read
# through `nota2md.cache` -- on-disk by default, straight into memory with
# `cache_dir=None`.

_SCJN_LEYES_RELEASE = "scjn-leyes"
_SCJN_LEYES_RELEASES_API = (
    f"https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/{_SCJN_LEYES_RELEASE}"
)

#: The reverse index published alongside the per-law tarballs: the union of
#: every `indice.json`, inverted by codNota and stripped of all text, so
#: resolving "which law does this codNota reform" costs a few hundred KB
#: instead of the 380 MB the whole corpus weighs.
ASSET_INDICE_GLOBAL = "indice-global.json.gz"


def construye_indice_global(instrumentos: list[dict], generado: str) -> tuple[dict, dict]:
    """The `indice-global.json.gz` payload for `instrumentos`, plus the counts
    the packaging manifest reports — see `ASSET_INDICE_GLOBAL`.

    Each entry of `instrumentos` is ``{"slug", "nombre", "asset", "indice"}``,
    where `indice` is that law's own `indice.json` (empty/absent for a law
    crawled but never linked). The result is::

        {"generado", "coleccion",
         "instrumentos": {slug: {"nombre", "asset", "snapshots"}},
         "codNota": {"4967917": [{"slug", "archivo", "title_link_status",
                                  "content_diff_confirmed_codNota",
                                  "content_diff_score"}]}}

    Two shapes here are deliberate, not incidental:

    * The `codNota` keys are **strings** — JSON has no integer keys. Readers
      (`download_scjn_leyes_index`) convert them back to `int` on load.
    * Each value is a **list**, not a single object: one decree routinely
      reforms several laws at once, and collapsing that into a dict would
      silently keep whichever law happened to be packaged last. Leaving it a
      list is what lets `snapshot_de_codNota` raise on the ambiguity instead
      of guessing (issue #117, D4).

    Only snapshots with a `codNota` actually linked make it in (D2): the index
    is the list of what we *know*, so an `ambiguous` or `unlinked` snapshot is
    counted in the returned tally and left out of the payload.

    `coleccion` is written as the literal `"leyes"`. It stopped being a
    parameter in issue #189, when `leyes` became the project's only
    collection, but stays in the payload: it is a field of a *published*
    asset that readers can already see, and dropping it would change the
    release format for no gain (`tests/test_scjn_release_red.py` asserts the
    published index still has it).
    """
    entradas_instrumentos: dict[str, dict] = {}
    por_cod_nota: dict[str, list[dict]] = {}
    conteos = {"linked": 0, "ambiguous": 0, "unlinked": 0, "sin_indice": 0}

    for instrumento in sorted(instrumentos, key=lambda i: i["slug"]):
        slug = instrumento["slug"]
        indice = instrumento.get("indice") or []
        entradas_instrumentos[slug] = {
            "nombre": instrumento["nombre"],
            "asset": instrumento.get("asset") or f"{slug}.tgz",
            "snapshots": len(indice) or instrumento.get("snapshots", 0),
        }
        if not indice:
            conteos["sin_indice"] += 1
            continue

        for entrada in indice:
            cod = entrada.get("codNota")
            if cod is None:
                # `title_link_status` says *why* it is not linked when
                # enlaza_scjn_legislacion.py got that far; a snapshot from
                # before that field existed is simply unlinked.
                estado = entrada.get("title_link_status", "unlinked")
                conteos[estado] = conteos.get(estado, 0) + 1
                continue
            conteos["linked"] += 1
            por_cod_nota.setdefault(str(cod), []).append({
                "slug": slug,
                "archivo": entrada["archivo"],
                "title_link_status": entrada.get("title_link_status"),
                "content_diff_confirmed_codNota": entrada.get(
                    "content_diff_confirmed_codNota"
                ),
                "content_diff_score": entrada.get("content_diff_score"),
            })

    indice_global = {
        "generado": generado,
        "coleccion": "leyes",
        "instrumentos": entradas_instrumentos,
        "codNota": {cod: por_cod_nota[cod] for cod in sorted(por_cod_nota, key=int)},
    }
    return indice_global, conteos


def _assets_scjn_leyes(timeout: int = 30) -> dict[str, str]:
    """Every asset of the `scjn-leyes` release, name -> download URL."""
    response = requests.get(_SCJN_LEYES_RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return {
        asset["name"]: asset["browser_download_url"] for asset in response.json()["assets"]
    }


def _url_de_asset(nombre: str, timeout: int) -> str:
    """The download URL of `nombre` in the `scjn-leyes` release.

    Raises `KeyError` while the release does not publish that asset yet —
    expected before a human has read `scripts/empaqueta_scjn_leyes.py`'s own
    manifest and published it by hand (this corpus has no automated publish
    path, on purpose — see that script)."""
    urls = _assets_scjn_leyes(timeout)
    if nombre not in urls:
        raise KeyError(
            f"el release '{_SCJN_LEYES_RELEASE}' no publica el asset '{nombre}' "
            "todavia — ver issue #128: este corpus solo se publica a mano, tras "
            "revision humana"
        )
    return urls[nombre]


def _bytes_de_asset(nombre: str, cache_dir, refrescar: bool, timeout: int) -> bytes:
    """`nombre`'s bytes, off the on-disk cache when there is one (see
    `nota2md.cache`) and straight into memory when there is not.

    The release index is only consulted when the asset is not already
    cached: a cache hit costs no HTTP request at all, which is the whole
    point of caching a corpus that is only ever republished by hand."""
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is not None:
        ruta = directorio / _SCJN_LEYES_RELEASE / nombre
        if ruta.exists() and not refrescar:
            return ruta.read_bytes()
    return bytes_de_asset(
        _SCJN_LEYES_RELEASE, nombre, _url_de_asset(nombre, timeout),
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout,
    )


#: `download_scjn_leyes_index`'s in-process memo, keyed by which cache
#: directory it was read through ("" for no cache). A batch of thousands of
#: `legal_provisions` calls must not re-read (let alone re-download and
#: re-decompress) the same index once per note.
_MEMO_INDICE_GLOBAL: dict[str, dict] = {}


def download_scjn_leyes_index(
    *, cache_dir=SIN_CACHE_DIR, refrescar: bool = False, timeout: int = 60
) -> dict:
    """The `scjn-leyes` release's reverse index (`ASSET_INDICE_GLOBAL`), as the
    dict `construye_indice_global` wrote — except that its `codNota` keys come
    back as `int`, not the strings JSON forced them into.

    Memoized per cache directory for the life of the process, so resolving a
    whole batch of notes reads the file once. `refrescar=True` bypasses both
    the memo and the on-disk cache and re-downloads.

    Raises `KeyError` while the asset is not published yet (see
    `_url_de_asset`); `legal_provisions` treats that as "no coverage" rather
    than letting it propagate.
    """
    directorio = resuelve_cache_dir(cache_dir)
    clave = str(directorio) if directorio is not None else ""
    if not refrescar and clave in _MEMO_INDICE_GLOBAL:
        return _MEMO_INDICE_GLOBAL[clave]

    contenido = _bytes_de_asset(ASSET_INDICE_GLOBAL, cache_dir, refrescar, timeout)
    indice = json.loads(gzip.decompress(contenido).decode("utf-8"))
    indice["codNota"] = {int(cod): entradas for cod, entradas in indice["codNota"].items()}
    _MEMO_INDICE_GLOBAL[clave] = indice
    return indice


def localiza_codNota(
    cod_nota: int,
    *,
    instrumento: str | None = None,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> tuple[str, str] | None:
    """Which snapshot of which law the reform `cod_nota` enacted produced, as
    ``(slug, archivo)`` — or None when the release's reverse index has no
    entry for it.

    The reverse-index half of `snapshot_de_codNota`, split out because it
    answers *where* the text is without reading the law's tarball at all: a
    caller that already has that snapshot materialized (see
    `legal_provisions` with no `outdir`) needs the name and nothing else.

    Raises `ValueError` for an ambiguous `cod_nota` exactly as
    `snapshot_de_codNota` does — see its docstring.
    """
    indice = download_scjn_leyes_index(
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    candidatos = indice["codNota"].get(int(cod_nota), [])

    if instrumento is not None:
        candidatos = [c for c in candidatos if c["slug"] == instrumento]
        if not candidatos:
            raise ValueError(
                f"el codNota {cod_nota} no reforma el instrumento {instrumento!r} "
                "segun el indice del release 'scjn-leyes'"
            )
    if not candidatos:
        return None
    if len(candidatos) > 1:
        nombres = ", ".join(
            f"{c['slug']} ({indice['instrumentos'].get(c['slug'], {}).get('nombre', '?')})"
            for c in candidatos
        )
        raise ValueError(
            f"el codNota {cod_nota} reforma mas de un instrumento: {nombres}. "
            "Pasa instrumento=<slug> para elegir uno"
        )

    candidato = candidatos[0]
    return candidato["slug"], candidato["archivo"]


def markdown_de_snapshot(
    slug: str,
    archivo: str,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> str:
    """The text of one snapshot, read out of its law's `<slug>.tgz` asset —
    the tarball half of `snapshot_de_codNota`, for a caller that already
    resolved `(slug, archivo)` with `localiza_codNota`."""
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere -- see scripts/empaqueta_scjn_leyes.py.
        miembro = tar.extractfile(tar.getmember(f"{slug}/{archivo}"))
        return miembro.read().decode("utf-8")


def snapshot_de_codNota(
    cod_nota: int,
    *,
    instrumento: str | None = None,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> tuple[str, str, str] | None:
    """The consolidated law text the SCJN holds for the reform `cod_nota`
    enacted, as ``(slug, archivo, markdown)`` — or None when the release's
    reverse index has no entry for it.

    `archivo` is the snapshot's own file name inside the tarball
    (``DD-MM-YYYY.md``, with issue #113's `-N` suffix when a law was reformed
    more than once on the same date); `markdown` is that file's text, its
    `fuente: scjn` provenance header included.

    None here is not an error, just "not covered": only snapshots with a
    `codNota` we are actually certain of are in the index at all (issue #117,
    D2), and the caller is expected to fall back to the DOF — which is what
    `legal_provisions` does.

    A `cod_nota` whose decree reformed several laws at once has several
    entries. Pass `instrumento` (a slug) to say which one is wanted;
    without it, that raises `ValueError` listing the candidates rather than
    silently returning one of them (D4).
    """
    ubicacion = localiza_codNota(
        cod_nota, instrumento=instrumento, cache_dir=cache_dir,
        refrescar=refrescar, timeout=timeout,
    )
    if ubicacion is None:
        return None
    slug, archivo = ubicacion
    markdown = markdown_de_snapshot(
        slug, archivo, cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    return slug, archivo, markdown


def download_scjn_leyes_corpus(
    slug: str, timeout: int = 60, *, cache_dir=SIN_CACHE_DIR, refrescar: bool = False
) -> dict:
    """One `leyes` instrument of the SCJN-based corpus (issue #128), by its
    own `slug`, as ``{"slug": ..., "snapshots": [...]}`` — one entry per
    snapshot, each carrying its own `indice.json` fields (`fecha_publicacion`,
    `codNota`, `ratio_similitud`, `sospechoso`, `title_candidates`,
    `title_link_status`, `content_diff_confirmed_codNota`,
    `content_diff_score`) plus its own Markdown body as `markdown` and, as
    `notas`, the DOF text of every candidate that was considered for it
    (``{codNota: markdown}``) — so the link can be audited without going
    back to the network.

    An instrument crawled but never linked (`scripts/enlaza_scjn_legislacion.py`
    has not run for it yet — Fase 2 pendiente) is still packaged with its raw
    snapshots; each of those comes back with only `archivo`/`codNota=None`/
    `markdown`/`notas={}` set, no confidence fields, rather than being dropped.

    Reads only that law's own `<slug>.tgz` asset (the release has one per
    law, not one for the whole collection — bringing down 380 MB to read a
    single law would be absurd), off the on-disk cache when there is one and
    straight into memory when `cache_dir=None` says there is not — see
    `nota2md.cache` for how `cache_dir`/`refrescar` resolve. Raises
    `KeyError` while the `scjn-leyes` release does not publish that asset
    yet — expected before a human has read
    `scripts/empaqueta_scjn_leyes.py`'s own manifest and published it by
    hand (this corpus has no automated publish path, on purpose — see that
    script).
    """
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)

    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        miembros = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}

    indice = None
    cuerpos: dict[str, str] = {}
    notas: dict[int, str] = {}
    for nombre, contenido in miembros.items():
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere; the prefix carries no information the caller needs.
        _, _, relativo = nombre.partition("/")
        if relativo == "indice.json":
            indice = json.loads(contenido)
        elif relativo.startswith("notas/"):
            cod = relativo[len("notas/nota-"):].removesuffix(".md")
            notas[int(cod)] = contenido.decode("utf-8")
        else:
            cuerpos[relativo] = contenido.decode("utf-8")

    if indice is not None:
        snapshots = [
            {
                **entrada,
                "markdown": cuerpos.get(entrada["archivo"]),
                "notas": {
                    cod: notas[cod]
                    for cod in entrada.get("title_candidates", [])
                    if cod in notas
                },
            }
            for entrada in indice
        ]
    else:
        snapshots = [
            {"archivo": nombre, "codNota": None, "markdown": texto, "notas": {}}
            for nombre, texto in sorted(cuerpos.items())
        ]
    return {"slug": slug, "snapshots": snapshots}


def _slugs_in_cache(cache_dir) -> list[str]:
    """Every law with a `<slug>.tgz` already on disk under `cache_dir`, by
    slug -- the cache-first answer to "which laws does this machine have",
    with no HTTP request at all (issue #205).

    `Path.glob("*.tgz")` matches a whole file name, so it already excludes an
    interrupted download (`cache.SUFIJO_PARCIAL`'s `<slug>.tgz.parcial`,
    which does not end in `.tgz`) as well as `ASSET_INDICE_GLOBAL` and
    `SHA256SUMS.txt`, neither of which is a `.tgz` either -- nothing here
    needs to filter those out by hand.

    Returns an empty list for `cache_dir=None` ("no cache") or a cache
    directory that does not exist yet.
    """
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is None:
        return []
    carpeta_release = directorio / _SCJN_LEYES_RELEASE
    if not carpeta_release.is_dir():
        return []
    return sorted(ruta.name.removesuffix(".tgz") for ruta in carpeta_release.glob("*.tgz"))


def iter_current_federal_laws(
    slugs: list[str] | None = None,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> Iterator[dict]:
    """The current text of every federal law the `scjn-leyes` release
    publishes, one ``{"slug", "nombre", "fecha_publicacion", "codNota",
    "archivo", "markdown"}`` dict per law -- "current" meaning the snapshot
    with the newest `fecha_publicacion` in that law's own `indice.json`.

    A true generator: one `<slug>.tgz` asset is opened, its winning snapshot
    read, and the tarball's bytes dropped before the next `slug` is reached,
    so iterating the whole corpus (currently ~315 laws, 380 MB uncompressed)
    never holds more than one law in memory at a time.
    `download_scjn_leyes_corpus` would not do here -- it decodes every
    snapshot and every `notas/` entry of a law just to keep the one that
    turns out to be the newest.

    `slugs=None` (the default) prefers the cache: when the resolved
    `cache_dir` already holds at least one `<slug>.tgz` and `refrescar` is
    False, those file names *are* "every law this machine has" -- no HTTP
    request at all, which is the whole fix for issue #205. Only a cold or
    absent cache, or `refrescar=True`, falls back to asking the release what
    it currently publishes a tarball for (`scjn_leyes_slugs`), rather than
    the keys of `indice-global.json.gz`'s `instrumentos`: a law that has been
    crawled but not linked yet has a `.tgz` and no entry there (same
    reasoning as `scjn_leyes_slugs` itself).

    This is a real behaviour change for a partially populated cache:
    `slugs=None` now means "every law *this machine has*", not "every law
    the release publishes", so it can silently yield fewer laws than before.
    Call `download_scjn_leyes_assets()` (or pass `refrescar=True`) to
    reconcile the cache with the release and get the rest.

    `nombre` still comes from `instrumentos`, which `construye_indice_global`
    populates for every crawled law regardless of whether it has an
    `indice.json` -- but reading it costs the very HTTP request the
    cache-first path above exists to avoid, when the cache holds tarballs but
    not `ASSET_INDICE_GLOBAL` itself. Rather than fetch it anyway or raise,
    that case degrades to `nombre=None`; the same
    `download_scjn_leyes_assets()`/`refrescar=True` call that backfills
    missing slugs also populates the index and gets real names back.

    A law never linked (`enlaza_scjn_legislacion.py` has not run for it yet)
    has no `indice.json` at all: the winner is then the raw snapshot whose
    file name -- `DD-MM-YYYY.md`, or `DD-MM-YYYY-N.md` for a same-day repeat
    (`scjn.api.descarga_ordenamiento`'s `-N` suffix) -- carries the newest
    date, and `codNota` comes back `None`.

    `fecha_publicacion`, both in `indice.json` and in a raw snapshot's own
    file name, is `DD-MM-YYYY` (`scjn.api._fecha`'s format), not ISO --
    comparing it lexicographically would rank `"05-01-1999"` ahead of
    `"22-05-1998"`. The winner is chosen by parsing the date with `_fecha`;
    `archivo` breaks a tie between two snapshots published the same day, only
    to make the pick deterministic, not because either ordering is more
    correct.

    Raises `KeyError` for a `slug` the release does not publish a tarball
    for, exactly as `download_scjn_leyes_corpus` does (`_bytes_de_asset`).
    """
    if slugs is None:
        slugs = [] if refrescar else _slugs_in_cache(cache_dir)
        if not slugs:
            slugs = scjn_leyes_slugs(timeout)

    directorio = resuelve_cache_dir(cache_dir)
    indice_en_disco = (
        directorio is not None
        and (directorio / _SCJN_LEYES_RELEASE / ASSET_INDICE_GLOBAL).exists()
    )
    if refrescar or directorio is None or indice_en_disco:
        instrumentos = download_scjn_leyes_index(
            cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
        )["instrumentos"]
    else:
        instrumentos = {}

    for slug in slugs:
        contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
        with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
            try:
                miembro_indice = tar.getmember(f"{slug}/indice.json")
            except KeyError:
                miembro_indice = None

            if miembro_indice is not None:
                indice = json.loads(tar.extractfile(miembro_indice).read())
                ganador = max(
                    indice, key=lambda e: (_fecha(e["fecha_publicacion"]), e["archivo"])
                )
                cod_nota = ganador.get("codNota")
                fecha_publicacion = ganador["fecha_publicacion"]
                archivo = ganador["archivo"]
            else:
                # Never linked: no indice.json, so nothing but the raw
                # snapshots' own file names says which is newest. estado.json
                # and notas/ are shipped alongside them but are not snapshots.
                candidatos = [
                    relativo
                    for m in tar.getmembers()
                    if m.isfile()
                    for relativo in (m.name.partition("/")[2],)
                    if relativo not in ("indice.json", ARCHIVO_ESTADO)
                    and not relativo.startswith("notas/")
                ]
                archivo = max(candidatos, key=lambda nombre: (_fecha(nombre[:10]), nombre))
                cod_nota = None
                fecha_publicacion = archivo[:10]

            miembro_md = tar.getmember(f"{slug}/{archivo}")
            markdown = tar.extractfile(miembro_md).read().decode("utf-8")

        yield {
            "slug": slug,
            "nombre": instrumentos.get(slug, {}).get("nombre"),
            "fecha_publicacion": fecha_publicacion,
            "codNota": cod_nota,
            "archivo": archivo,
            "markdown": markdown,
        }


def _estado_de_asset(slug: str, cache_dir, refrescar: bool, timeout: int) -> dict:
    """One law's own `estado.json` as the release ships it inside `<slug>.tgz`,
    or `{}` when that tarball carries none (a law packaged before issue #148
    added the file).

    Only that one member is read out of the tarball; the snapshots and the
    `notas/` are never decoded. A whole law's Markdown weighs orders of
    magnitude more than the four fields wanted here, and the catalogue reader
    does this once per law."""
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for miembro in tar:
            # Every member is prefixed with `<slug>/` so the tarball unpacks
            # anywhere — the same convention `download_scjn_leyes_corpus` strips.
            if miembro.isfile() and miembro.name.partition("/")[2] == ARCHIVO_ESTADO:
                return json.loads(tar.extractfile(miembro).read())
    return {}


def download_scjn_leyes_catalog(
    *,
    freshness: bool = True,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> list[dict]:
    """The federal-law catalogue as the `scjn-leyes` release already publishes
    it: one ``{"abrev", "nombre", "actualizado"}`` dict per law, sorted by
    `abrev`.

    This is the seed the Cámara de Diputados used to be scraped for — which
    laws exist, their name, their abbreviation — read back out of the release
    rather than rebuilt (issue #184). `nombre` and `abrev` come from
    `indice-global.json.gz`'s `instrumentos`, whose slug *is* the `abrev`
    (`slug_instrumento`); `actualizado` comes from each law's own
    `estado.json`, which records the date its last reform carried when it was
    crawled (issue #148).

    `actualizado` is **absent** — not None, not a placeholder — for a law whose
    `estado.json` has none (3 laws today: `lcmopfih`, `lfcpq`, `lisipl`).
    Absent means "freshness unknown, always review", which is what
    `motivo_pendiente` already does with a catalogue entry that has no
    `actualizado`.

    One caveat this reader cannot paper over, and which matters to whoever
    rebuilds `catalogo.json`: the slug is `slug_instrumento`'s *normalized*
    `abrev`, so the 14 laws whose historical `abrev` contains an underscore
    (`lif_2026`, `pef_2026`, `ligie_2022`, the `lrart*`/`lrf*` reglamentarias,
    `reg_diputados`, `reg_senado`) come back hyphenated. Existing `abrev`
    values are preserved verbatim, so a caller holding a previous catalogue
    must match on `slug_instrumento` and keep its own `abrev`.

    `freshness=False` skips the tarballs entirely and returns `abrev`/`nombre`
    only, off the index alone — a few hundred KB. The default reads one
    tarball per law, which is the whole 380 MB corpus the first time; with a
    `cache_dir` already populated (`download_scjn_leyes_assets`, or `nota2md
    download federal-laws`) it costs no request at all.

    Raises `KeyError` while the release does not publish an asset it needs —
    see `_url_de_asset`.
    """
    indice = download_scjn_leyes_index(
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    catalogo = []
    for slug in sorted(indice["instrumentos"]):
        entrada = {"abrev": slug, "nombre": indice["instrumentos"][slug]["nombre"]}
        if freshness:
            actualizado = _estado_de_asset(slug, cache_dir, refrescar, timeout).get(
                "actualizado"
            )
            if actualizado:
                entrada["actualizado"] = actualizado
        catalogo.append(entrada)
    return catalogo


def scjn_leyes_slugs(timeout: int = 30) -> list[str]:
    """Every law the `scjn-leyes` release publishes a tarball for, by slug.

    Read off the release's own asset listing rather than off
    `indice-global.json.gz`: a law crawled but not linked yet has a `.tgz`
    and no index entry, and "download the corpus" means the tarballs, not
    the ones the linking phase already got to.
    """
    return sorted(
        nombre.removesuffix(".tgz")
        for nombre in _assets_scjn_leyes(timeout)
        if nombre.endswith(".tgz")
    )


def download_scjn_leyes_assets(
    slugs: list[str] | None = None,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
    log=None,
) -> list[tuple[Path, bool]]:
    """Put the `scjn-leyes` release's assets on disk: the reverse index plus
    one tarball per law, into ``<cache_dir>/scjn-leyes/``.

    `slugs` picks which laws to fetch; None (the default) means every law the
    release publishes. The index is always included — it is what
    `legal_provisions` resolves a codNota through, and it costs a few hundred
    KB against the corpus' 380 MB.

    Returns one ``(path, downloaded)`` pair per asset, in the order they were
    fetched, with `downloaded` False for an asset that was already cached —
    matched by name and never revalidated, like every other read of this
    release (see `nota2md.cache`). `refrescar=True` re-downloads regardless.

    Unlike the release *readers*, this is the "materialize it on disk" verb,
    so ``cache_dir=None`` is meaningless here and raises: downloading into
    memory and discarding it is not something a caller can want.
    """
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is None:
        raise ValueError(
            "download_scjn_leyes_assets writes the release to disk; "
            "cache_dir=None ('no cache') has nothing to write to"
        )

    # Named slugs give the asset names outright, so a fully cached re-run can
    # skip the release listing too and cost no HTTP request at all; without
    # them the listing *is* how "every law" is known, so it is unavoidable.
    urls = None
    if slugs is None:
        urls = _assets_scjn_leyes(timeout)
        nombres = [ASSET_INDICE_GLOBAL] + [
            f"{slug}.tgz"
            for slug in sorted(n.removesuffix(".tgz") for n in urls if n.endswith(".tgz"))
        ]
    else:
        nombres = [ASSET_INDICE_GLOBAL] + [f"{slug}.tgz" for slug in slugs]

    resultados = []
    for i, nombre in enumerate(nombres, 1):
        destino = directorio / _SCJN_LEYES_RELEASE / nombre
        ya_estaba = destino.exists() and not refrescar
        if ya_estaba:
            ruta = destino
        else:
            if urls is None:
                urls = _assets_scjn_leyes(timeout)
            if nombre not in urls:
                raise KeyError(
                    f"el release '{_SCJN_LEYES_RELEASE}' no publica el asset '{nombre}'"
                )
            ruta = asset_en_cache(
                _SCJN_LEYES_RELEASE, nombre, urls[nombre],
                cache_dir=directorio, refrescar=refrescar, timeout=timeout,
            )
        if log is not None:
            estado = "already cached" if ya_estaba else "downloaded"
            log(f"[{i}/{len(nombres)}] {nombre}: {estado}")
        resultados.append((ruta, not ya_estaba))
    return resultados
