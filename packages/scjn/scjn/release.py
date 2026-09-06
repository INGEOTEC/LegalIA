"""The `scjn-leyes` release's own readers, disk-first, plus the one downloader
that puts the release on disk in the first place (issue #209, Fase 3 of
#206/#207).

Two things happen together because they cannot be separated: these functions
moved here out of a downstream package's own module, and they stopped
downloading. Every reader below (`download_scjn_leyes_index`,
`download_scjn_leyes_corpus`, `markdown_de_snapshot`,
`download_scjn_leyes_catalog`, `iter_current_federal_laws`, `local_slugs`)
takes a `cache_dir` defaulting to `scjn.cache.CACHE_DIR` and only ever opens
files under it — none of them make an HTTP request, and a missing asset
raises `AssetNotCached` rather than attempting one. Only
`download_scjn_leyes_assets` (and, through it, the `scjn download` CLI) talks
to the network; a caller who wants the corpus on disk runs that first,
exactly once, and every reader afterwards is offline.

Resolving a `codNota` back to the snapshot it produced is **not** here: a
`codNota` is a DOF concept, so that pair of functions lives one layer up,
calling the readers below (issue #208/#209) -- this package never imports
that layer, keeping the dependency direction one-way (see `test_boundary.py`).
"""

import gzip
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path

import requests

from scjn import cache
from scjn.cache import _SCJN_LEYES_RELEASE
from scjn.header import _fecha
from scjn.state import ARCHIVO_ESTADO

#: User-Agent for the `scjn-leyes` release's own GitHub requests -- the only
#: network this module does (the SCJN crawl itself lives in `scjn.api`, with
#: its own headers).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-scjn/1.0)"}

_SCJN_LEYES_RELEASES_API = (
    f"https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/{_SCJN_LEYES_RELEASE}"
)

#: The reverse index published alongside the per-law tarballs: the union of
#: every `indice.json`, inverted by codNota and stripped of all text, so
#: resolving "which law does this codNota reform" costs a few hundred KB
#: instead of the ~380 MB the whole corpus weighs.
ASSET_INDICE_GLOBAL = "indice-global.json.gz"

#: The per-law metadata the SCJN's own search carries and this release
#: publishes (issue #215, off #203's items 1/2/5): its subject
#: classification, whether it is still in force, and the SCJN's own
#: one-paragraph abstract of it. One value per law, not per reform — the
#: `Reforma` rows carry none of the three — which is why they live in each
#: law's `estado.json` and in `instrumentos` here, and not in a snapshot's
#: provenance header: `vigencia` describes the law *today*, while a snapshot
#: describes it at one reform in the past.
CAMPOS_METADATOS = ("materia", "vigencia", "resumen")


class AssetNotCached(Exception):
    """A release asset a reader needs is not on disk yet.

    Raised instead of a network request by every reader in this module
    (issue #209): the disk-first contract means a caller is told which
    command populates the cache, not left waiting on an HTTP request a pure
    reader has no business making. Downloading is `download_scjn_leyes_assets`
    (or the `scjn download` CLI built on it) alone.
    """

    def __init__(self, nombre: str, cache_dir: Path):
        comando = (
            f"scjn download --slug {nombre.removesuffix('.tgz')}"
            if nombre.endswith(".tgz")
            else "scjn download"
        )
        super().__init__(f"'{nombre}' is not cached under {cache_dir} -- run `{comando}`")
        self.nombre = nombre
        self.cache_dir = cache_dir


def construye_indice_global(instrumentos: list[dict], generado: str) -> tuple[dict, dict]:
    """The `indice-global.json.gz` payload for `instrumentos`, plus the counts
    the packaging manifest reports — see `ASSET_INDICE_GLOBAL`.

    Each entry of `instrumentos` is ``{"slug", "nombre", "asset", "indice"}``,
    where `indice` is that law's own `indice.json` (empty/absent for a law
    crawled but never linked). The result is::

        {"generado", "coleccion",
         "instrumentos": {slug: {"nombre", "asset", "snapshots",
                                 "materia"?, "vigencia"?, "resumen"?}},
         "codNota": {"4967917": [{"slug", "archivo", "title_link_status",
                                  "content_diff_confirmed_codNota",
                                  "content_diff_score"}]}}

    Two shapes here are deliberate, not incidental:

    * The `codNota` keys are **strings** — JSON has no integer keys. Readers
      (`download_scjn_leyes_index`) convert them back to `int` on load.
    * Each value is a **list**, not a single object: one decree routinely
      reforms several laws at once, and collapsing that into a dict would
      silently keep whichever law happened to be packaged last. Leaving it a
      list is what lets a caller resolving a `codNota` back to its snapshot
      raise on the ambiguity instead of guessing (issue #117, D4).

    `materia`/`vigencia`/`resumen` (`CAMPOS_METADATOS`, issue #215) are copied
    from the instrument entry when it carries them — the SCJN's own
    classification, in-force status and abstract, one value per law rather
    than per snapshot. A law with no value for one of them simply has no such
    key: absent, never `null`, so a reader can tell "we have not asked" from
    "the SCJN says nothing", and so the asset does not grow by 315 nulls
    while the fields are being backfilled.

    Only snapshots with a `codNota` actually linked make it in (D2): the index
    is the list of what we *know*, so an `ambiguous` or `unlinked` snapshot is
    counted in the returned tally and left out of the payload.

    `coleccion` is written as the literal `"leyes"`. It stopped being a
    parameter in issue #189, when `leyes` became the project's only
    collection, but stays in the payload: it is a field of a *published*
    asset that readers can already see, and dropping it would change the
    release format for no gain.
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
        for campo in CAMPOS_METADATOS:
            valor = instrumento.get(campo)
            if valor:
                entradas_instrumentos[slug][campo] = valor
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
    """Every asset of the `scjn-leyes` release, name -> download URL. Network
    -- used by the downloader only, never by a reader."""
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
    path, on purpose — see that script). Used by the downloader only."""
    urls = _assets_scjn_leyes(timeout)
    if nombre not in urls:
        raise KeyError(
            f"el release '{_SCJN_LEYES_RELEASE}' no publica el asset '{nombre}' "
            "todavia — ver issue #128: este corpus solo se publica a mano, tras "
            "revision humana"
        )
    return urls[nombre]


def _read_asset(nombre: str, cache_dir) -> bytes:
    """`nombre`'s bytes off the on-disk cache, with no network fallback --
    the basic disk-only building block every reader in this module uses
    (issue #209). Raises `AssetNotCached` when the file is not there."""
    directorio = cache.resuelve_cache_dir(cache_dir)
    ruta = directorio / _SCJN_LEYES_RELEASE / nombre
    if not ruta.exists():
        raise AssetNotCached(nombre, directorio)
    return ruta.read_bytes()


#: `download_scjn_leyes_index`'s in-process memo, keyed by which cache
#: directory it was read through. A batch of thousands of `legal_provisions`
#: calls must not re-read (let alone re-decompress) the same index once per
#: note.
_MEMO_INDICE_GLOBAL: dict[str, dict] = {}


def download_scjn_leyes_index(*, cache_dir=None) -> dict:
    """The `scjn-leyes` release's reverse index (`ASSET_INDICE_GLOBAL`), as the
    dict `construye_indice_global` wrote — except that its `codNota` keys come
    back as `int`, not the strings JSON forced them into.

    Memoized per cache directory for the life of the process, so resolving a
    whole batch of notes reads the file once. Reads only `cache_dir`
    (`scjn.cache.CACHE_DIR` when not given) — raises `AssetNotCached` while
    the index is not cached there yet; run `scjn download` first.
    """
    directorio = cache.resuelve_cache_dir(cache_dir)
    clave = str(directorio)
    if clave in _MEMO_INDICE_GLOBAL:
        return _MEMO_INDICE_GLOBAL[clave]

    contenido = _read_asset(ASSET_INDICE_GLOBAL, directorio)
    indice = json.loads(gzip.decompress(contenido).decode("utf-8"))
    indice["codNota"] = {int(cod): entradas for cod, entradas in indice["codNota"].items()}
    _MEMO_INDICE_GLOBAL[clave] = indice
    return indice


def markdown_de_snapshot(slug: str, archivo: str, *, cache_dir=None) -> str:
    """The text of one snapshot, read out of its law's already-cached
    `<slug>.tgz` asset. Raises `AssetNotCached` when that tarball is not on
    disk under `cache_dir` yet."""
    contenido = _read_asset(f"{slug}.tgz", cache_dir)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere -- see scripts/empaqueta_scjn_leyes.py.
        miembro = tar.extractfile(tar.getmember(f"{slug}/{archivo}"))
        return miembro.read().decode("utf-8")


def download_scjn_leyes_corpus(slug: str, *, cache_dir=None) -> dict:
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
    has not run for it yet) is still packaged with its raw snapshots; each of
    those comes back with only `archivo`/`codNota=None`/`markdown`/`notas={}`
    set, no confidence fields, rather than being dropped.

    Reads only that law's own already-cached `<slug>.tgz` asset. Raises
    `AssetNotCached` while it is not on disk under `cache_dir` yet — run
    `scjn download --slug <slug>` first.
    """
    contenido = _read_asset(f"{slug}.tgz", cache_dir)

    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        miembros = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}

    indice = None
    cuerpos: dict[str, str] = {}
    notas: dict[int, str] = {}
    for nombre, contenido_miembro in miembros.items():
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere; the prefix carries no information the caller needs.
        _, _, relativo = nombre.partition("/")
        if relativo == "indice.json":
            indice = json.loads(contenido_miembro)
        elif relativo.startswith("notas/"):
            cod = relativo[len("notas/nota-"):].removesuffix(".md")
            notas[int(cod)] = contenido_miembro.decode("utf-8")
        else:
            cuerpos[relativo] = contenido_miembro.decode("utf-8")

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


def local_slugs(cache_dir=None) -> list[str]:
    """Every law with a `<slug>.tgz` already on disk under `cache_dir` -- the
    disk-first answer to "which laws does this machine have", with no HTTP
    request at all (issue #205, made public here since every reader in this
    module needs it, not just `iter_current_federal_laws`).

    `Path.glob("*.tgz")` matches a whole file name, so it already excludes an
    interrupted download (`cache.SUFIJO_PARCIAL`'s `<slug>.tgz.parcial`,
    which does not end in `.tgz`) as well as `ASSET_INDICE_GLOBAL` and
    `SHA256SUMS.txt`, neither of which is a `.tgz` either -- nothing here
    needs to filter those out by hand.

    Returns an empty list for a cache directory that does not exist yet.
    """
    directorio = cache.resuelve_cache_dir(cache_dir)
    carpeta_release = directorio / _SCJN_LEYES_RELEASE
    if not carpeta_release.is_dir():
        return []
    return sorted(ruta.name.removesuffix(".tgz") for ruta in carpeta_release.glob("*.tgz"))


def iter_current_federal_laws(
    slugs: list[str] | None = None, *, cache_dir=None
) -> Iterator[dict]:
    """The current text of every federal law the `scjn-leyes` release
    publishes, one ``{"slug", "nombre", "materia", "vigencia", "resumen",
    "fecha_publicacion", "codNota", "archivo", "markdown"}`` dict per law --
    "current" meaning the snapshot with the newest `fecha_publicacion` in
    that law's own `indice.json`.

    A true generator: one `<slug>.tgz` asset is opened, its winning snapshot
    read, and the tarball's bytes dropped before the next `slug` is reached,
    so iterating the whole corpus never holds more than one law in memory at
    a time. `download_scjn_leyes_corpus` would not do here -- it decodes
    every snapshot and every `notas/` entry of a law just to keep the one
    that turns out to be the newest.

    `slugs=None` (the default) means "every law this machine has": `cache_dir`
    (`scjn.cache.CACHE_DIR` when not given) is asked for its own `<slug>.tgz`
    file names via `local_slugs`, with no HTTP request at all (issue #205).
    This is a disk-only reader (issue #209): a cold or absent cache does
    **not** fall back to asking the release what it currently publishes (that
    is `scjn_leyes_slugs`'s job, and the downloader's) — it simply yields
    nothing. Run `download_scjn_leyes_assets()` (or the `scjn download` CLI)
    first to populate the cache.

    `materia`/`vigencia`/`resumen` (issue #215) come from the same
    `instrumentos` entry `nombre` does, and degrade to None the same way when
    the index is not cached. They are what makes this iterator usable as a
    *classified* corpus — stratify by `materia`, keep only `VIGENTE` — without
    a second pass over the release or a request to the SCJN. `vigencia` is the
    law's status as of the last metadata run (`clasificado` in its own
    `estado.json`), not as of the snapshot being yielded: a 1970 snapshot of a
    law abrogated in 2014 comes back with `vigencia` `ABROGADO (A)`, which is
    a statement about the law, not about that text.

    `nombre` still comes from `indice-global.json.gz`'s `instrumentos`, which
    `construye_indice_global` populates for every crawled law regardless of
    whether it has an `indice.json` -- but that asset can be cached
    separately from a law's own tarball. When `cache_dir` holds tarballs but
    not the index, `nombre` degrades to `None` rather than raising
    `AssetNotCached` for a field nobody asked to read explicitly; call
    `download_scjn_leyes_assets()` (or `scjn download`) to populate the index
    too and get real names back.

    A law never linked (`enlaza_scjn_legislacion.py` has not run for it yet)
    has no `indice.json` at all: the winner is then the raw snapshot whose
    file name -- `DD-MM-YYYY.md`, or `DD-MM-YYYY-N.md` for a same-day repeat
    (`scjn.api.descarga_ordenamiento`'s `-N` suffix) -- carries the newest
    date, and `codNota` comes back `None`.

    `fecha_publicacion`, both in `indice.json` and in a raw snapshot's own
    file name, is `DD-MM-YYYY` (`scjn.header._fecha`'s format), not ISO --
    comparing it lexicographically would rank `"05-01-1999"` ahead of
    `"22-05-1998"`. The winner is chosen by parsing the date with `_fecha`;
    `archivo` breaks a tie between two snapshots published the same day, only
    to make the pick deterministic, not because either ordering is more
    correct.

    Raises `AssetNotCached` for an explicitly named `slug` whose tarball is
    not cached, exactly as `download_scjn_leyes_corpus` does.
    """
    directorio = cache.resuelve_cache_dir(cache_dir)
    if slugs is None:
        slugs = local_slugs(directorio)

    indice_en_disco = (directorio / _SCJN_LEYES_RELEASE / ASSET_INDICE_GLOBAL).exists()
    instrumentos = (
        download_scjn_leyes_index(cache_dir=directorio)["instrumentos"]
        if indice_en_disco
        else {}
    )

    for slug in slugs:
        contenido = _read_asset(f"{slug}.tgz", directorio)
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

        entrada_indice = instrumentos.get(slug, {})
        yield {
            "slug": slug,
            "nombre": entrada_indice.get("nombre"),
            **{campo: entrada_indice.get(campo) for campo in CAMPOS_METADATOS},
            "fecha_publicacion": fecha_publicacion,
            "codNota": cod_nota,
            "archivo": archivo,
            "markdown": markdown,
        }


def _estado_de_asset(slug: str, cache_dir) -> dict:
    """One law's own `estado.json` as the release ships it inside `<slug>.tgz`,
    or `{}` when that tarball carries none (a law packaged before issue #148
    added the file). Raises `AssetNotCached` when the tarball itself is not
    cached — `download_scjn_leyes_catalog` decides what to do with that."""
    contenido = _read_asset(f"{slug}.tgz", cache_dir)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for miembro in tar:
            # Every member is prefixed with `<slug>/` so the tarball unpacks
            # anywhere — the same convention `download_scjn_leyes_corpus` strips.
            if miembro.isfile() and miembro.name.partition("/")[2] == ARCHIVO_ESTADO:
                return json.loads(tar.extractfile(miembro).read())
    return {}


def download_scjn_leyes_catalog(*, freshness: bool = True, cache_dir=None, log=None) -> list[dict]:
    """The federal-law catalogue as the `scjn-leyes` release already publishes
    it: one ``{"abrev", "nombre", "actualizado", "materia", "vigencia",
    "resumen"}`` dict per law, sorted by `abrev`.

    This is the seed the Cámara de Diputados used to be scraped for — which
    laws exist, their name, their abbreviation — read back out of the release
    rather than rebuilt (issue #184). `nombre` comes from
    `indice-global.json.gz`'s `instrumentos`, keyed by slug
    (`scjn.catalog.slug_instrumento`); `abrev` and `actualizado` come from each
    law's own `estado.json` when `freshness=True` -- `abrev` verbatim (issue
    #210: `estado.json` is now the one place that value lives, once the
    one-time backfill has run), `actualizado` the date its last reform
    carried when it was crawled (issue #148).

    `materia`/`vigencia`/`resumen` (issue #215) are the SCJN's own subject
    classification, in-force status and one-paragraph abstract of the law —
    one value per law, not per reform. They are read off the index, so they
    come back in `freshness=False` mode too, with no tarball opened at all;
    when a tarball *is* read, that law's own `estado.json` wins over the
    index for them, since it is the record the next repack will publish. Each
    is **absent** rather than None for a law the SCJN has no value for, or
    that `scripts/fetch_federal_law_metadata.py` has not resolved yet.

    `actualizado` is **absent** — not None, not a placeholder — for a law whose
    `estado.json` has none, or whose tarball is not cached at all under
    `freshness=True` (issue #209: this reader never downloads a tarball just
    to answer four fields, so a law missing from the cache is reported this
    way rather than raising `AssetNotCached`). Absent means "freshness
    unknown, always review", which is what `motivo_pendiente` already does
    with a catalogue entry that has no `actualizado`. Pass `log` (e.g.
    `print`) to be told which laws were skipped for this reason.

    A tarball packaged before issue #210 added `abrev` to `estado.json` -- or
    `freshness=False`, which skips tarballs entirely -- falls back to the
    slug itself, which is `slug_instrumento`'s *normalized* form: the 14 laws
    whose historical `abrev` contains an underscore (`lif_2026`, `pef_2026`,
    `ligie_2022`, the `lrart*`/`lrf*` reglamentarias, `reg_diputados`,
    `reg_senado`) come back hyphenated until their tarball is backfilled.

    `freshness=False` skips the tarballs entirely and returns `abrev`/`nombre`
    only, off the index alone — a few hundred KB, and the only mode that
    never raises `AssetNotCached`. Raises `AssetNotCached` while the index
    itself is not cached — run `scjn download` first.
    """
    directorio = cache.resuelve_cache_dir(cache_dir)
    indice = download_scjn_leyes_index(cache_dir=directorio)
    catalogo = []
    for slug in sorted(indice["instrumentos"]):
        entrada_indice = indice["instrumentos"][slug]
        entrada = {"abrev": slug, "nombre": entrada_indice["nombre"]}
        entrada.update(
            {c: entrada_indice[c] for c in CAMPOS_METADATOS if entrada_indice.get(c)}
        )
        if freshness:
            try:
                estado = _estado_de_asset(slug, directorio)
            except AssetNotCached:
                if log is not None:
                    log(f"{slug}: tarball not cached, skipping freshness check")
                catalogo.append(entrada)
                continue
            if estado.get("abrev"):
                entrada["abrev"] = estado["abrev"]
            if estado.get("actualizado"):
                entrada["actualizado"] = estado["actualizado"]
            entrada.update(
                {campo: estado[campo] for campo in CAMPOS_METADATOS if estado.get(campo)}
            )
        catalogo.append(entrada)
    return catalogo


def scjn_leyes_slugs(timeout: int = 30) -> list[str]:
    """Every law the `scjn-leyes` release publishes a tarball for, by slug --
    the *downloader's* question ("what does the release publish"), not a
    reader's: it always makes an HTTP request, unlike `local_slugs`.

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
    cache_dir=None,
    refrescar: bool = False,
    timeout: int = 60,
    log=None,
) -> list[tuple[Path, bool]]:
    """Put the `scjn-leyes` release's assets on disk: the reverse index plus
    one tarball per law, into ``<cache_dir>/scjn-leyes/``.

    The only function in this module that talks to the network (besides
    `scjn_leyes_slugs`) — every reader above assumes this (or the `scjn
    download` CLI built on it) already ran.

    `slugs` picks which laws to fetch; None (the default) means every law the
    release publishes. The index is always included — it is what every
    reader resolves a codNota or a law's freshness through, and it costs a
    few hundred KB against the corpus' ~380 MB.

    Returns one ``(path, downloaded)`` pair per asset, in the order they were
    fetched, with `downloaded` False for an asset that was already cached —
    matched by name and never revalidated. `refrescar=True` re-downloads
    regardless.
    """
    directorio = cache.resuelve_cache_dir(cache_dir)

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
            ruta = cache.asset_en_cache(
                _SCJN_LEYES_RELEASE, nombre, urls[nombre],
                cache_dir=directorio, refrescar=refrescar, timeout=timeout,
            )
        if log is not None:
            estado = "already cached" if ya_estaba else "downloaded"
            log(f"[{i}/{len(nombres)}] {nombre}: {estado}")
        resultados.append((ruta, not ya_estaba))
    return resultados
