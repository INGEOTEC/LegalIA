#!/usr/bin/env python3
"""Crawl the SCJN for every federal law already seeded under
``<outdir>/leyes/`` (issue #210: one directory per law, each with its own
`estado.json` -- there is no separate `catalogo.json` any more), and save
each one's reform-dated snapshots as Markdown under
``<outdir>/leyes/<abrev-o-nombre>/<fecha_publicacion>.md`` — Fase 1 of the
crawl plan in issue #105.

Federal laws are the only collection left (issue #189), so ``leyes`` is a
literal path segment here rather than something to pass on the command line.
That is a decision, not a leftover: the other collections went out of scope
with the Cámara de Diputados data they leaned on, and a second one would have
to justify itself again before it got a flag back.

The crawl goes through the SCJN's own SCOW JSON API
(`/SCOW-API`, `scjn.api`). Until issue #179 it went instead through
the legacy WebForms Buscador (`/Buscador/`), and the mechanisms below are
worded the way that path taught them. What replaced what, and why:

- **The whole crawl** (search -> reform table -> per-reform text) was a
  session-scoped walk of `/Buscador/`: a POST resubmitting `__VIEWSTATE`, a
  detail page whose `q` token only the requesting session could use, a
  reform grid of 10 rows a page walked by `__EVENTTARGET` postbacks, and one
  `.docx` per row. It is now three JSON calls (`BusquedaFrase`, `Reforma`,
  `Articulos`), and an instrumento is addressable by a stable
  `idOrdenamiento`.
- **Mecanismo 2's permanent retry** (below) existed because the old Buscador
  did not index the LEY Federal de Cine y el Audiovisual at all -- searching
  its exact title returned 0 candidates, twice, live (issue #124). The API
  answers `idOrdenamiento` 188805 for the same name, so that case is closed;
  the retry stays, since a brand-new law can still be indexed late.
- **`elige_candidato`'s guards** (issue #115) live on as
  `scjn.api.elige_ordenamiento`, thresholds and hard exclusions unchanged,
  plus signals the old results page never showed (the API's own
  `categoriaOrdenamiento`).

The API is not an official contract either -- a Swagger page is not a
stability promise -- so the rate limiting stays, and the SCJN remains a
non-official source of legal text: the `fuente: scjn` header these files
carry means exactly what it meant before the migration.

Resumable at two levels. Within one instrumento, a file already on disk is
left alone and its reform's download skipped (see
scjn.api.descarga_ordenamiento), so a later re-run picking up new
reforms only fetches what is missing. Across a whole collection, the slug
of the last instrumento fully attempted is checkpointed to
``<outdir>/leyes/.progreso.json`` and cleared once the collection
finishes; a run killed partway (crash, network drop, Ctrl-C) resumes right
after that slug instead of re-walking every already-done instrumento's
reform table from the top — pass ``--reiniciar`` to discard that checkpoint
and sweep the collection from the beginning again. Checkpointing by slug
rather than position (issue #210) is what lets this survive a law being
added to or removed from the corpus between runs, unlike the old
position-in-`catalogo.json` checkpoint it replaces.

A third, narrower case: issue #115's manual audit confirmed 5 instrumentos
(ccf, lisr, lsint, lfd, lopgjdf) where the SCJN search returned, and a past
crawl saved, the wrong document entirely — candidate selection gained guards
against this, but the wrong snapshots already on disk are still there,
untouched, since the per-file skip above has no notion of a snapshot being
*wrong*. ``--reintenta SLUG`` (repeatable, `scjn.catalog.slug_instrumento`)
re-bajas exactly the named instrumentos: their existing snapshots (and
stale `indice.json`) are deleted first so they are genuinely re-fetched
against the fixed candidate selection, while every instrumento not named is
skipped without touching the SCJN at all — a full collection is ~600
instrumentos, and there is no need to re-walk the other ~595 that were never
wrong just to verify a fix aimed at 5. Leaves the collection's own
``.progreso.json`` checkpoint untouched either way. Rate-limited:
`--espera` seconds between requests, since this is an unofficial site with
no stability contract (the same posture `dofjson.dofweb` takes toward the
DOF's own website).

A fourth case, issue #124's follow-up ("Dos casos disparadores"): an
instrumento the coverage sweep above never finds *anything* for at all,
either because the SCJN's own full-text search never matches the
catalogue's exact wording (`lisipl`), or because the SCJN has not indexed a brand-new
law yet (`lfca`). Two mechanisms close this, both driven entirely by fields
this script's own `refresca_catalogo()` writes into each law's own
`estado.json` (issue #210 folded `extract_scjn_titles.py`'s refresh into it)
— nothing to pass on this script's own command line:

- **Mecanismo 1** (manual override): when a catalogue entry carries a
  `nombre_scjn` field, it is searched instead of `nombre`
  (`scjn.catalog.search_name`) — `nombre` itself is still what gets printed
  and what `enlaza_scjn_legislacion.py` (issue #126) compares against DOF
  titles. Applied so far only to `lisipl`; `lfca`'s own gap is indexing
  lag, not a title mismatch, so no override was correct for it — and the
  API has since indexed the law, which is what actually closed the case.
- **Mecanismo 2** (incremental refresh): once a collection has been crawled
  start-to-finish at least once under this mechanism, that date is
  recorded to ``<outdir>/leyes/.rastreo_completo.json``. A later
  refresh run skips an instrumento without touching the SCJN at all
  (`scjn.catalog.instrument_up_to_date`) only when it already has a
  snapshot on disk *and* its own `actualizado` (its most recent reform's
  date, `refresca_catalogo()`) is no later than that checkpoint — an
  instrumento with no snapshot on disk yet is always retried regardless of
  `actualizado`, so a law like `lfca`, not yet indexed by the SCJN at all,
  keeps getting retried on every refresh automatically, with nothing to
  configure by hand once the SCJN eventually catches up. ``--reiniciar``
  (see below) bypasses this skip too, the same as it bypasses the
  ``.progreso.json`` checkpoint.

Issue #140 found two bugs live in the mechanisms above, both fixed here:
Mecanismo 2 needs `actualizado` on the catalogue to ever skip anything, and
that field is only ever written by `refresca_catalogo()` -- a corpus never
refreshed under it (or hand-seeded) leaves it inert with no visible sign
why, so this script now warns on stderr, once per collection, when it sees
a catalogue with zero `actualizado` entries.
And a large instrumento's own crawl -- confirmed live against the CPEUM,
301 reforms -- used to go completely silent for as long as that took,
indistinguishable from a hung process; the crawl's
`on_progreso` callback now narrates it, one line per reform.

Issue #177 added ``--api``; issue #178 re-crawled the whole `leyes`
collection through it, diffed the result against the corpus this repo had
already published, and made it the default; issue #179 removed the WebForms
path altogether, so ``--api`` is now a no-op accepted only so a command
written during the transition keeps running. What the diff showed, over 315 instrumentos:
270 identical in every respect, 0 that the new index cannot find and the
old one could, 53 whose only change is a title cleaned of the HTML
scraping's own spacing artifacts ("INTE RES PUBLICO", "LEY ,"), and two
files the old crawler had saved from an entirely different ordenamiento
(a Morelos state law under `lgeepa`, a Nuevo Leon one under `cpeum` --
issue #115 Hallazgo C, found again). Every mechanism described above keeps
its meaning under the API; two things are genuinely new:

- an instrumento's `estado.json` records the `id_ordenamiento` the crawl
  resolved, so a later run skips the search step entirely and reads exactly
  the document the previous one read. `lee_estado`/`motivo_pendiente`
  tolerate an `estado.json` written before this field existed, and
  ``--reintenta`` deliberately does *not* reuse it (re-downloading the same
  wrong document from a remembered id is exactly what issue #115 is about).
- a reform the SCJN itself cannot serve (`lfd` has ones answering HTTP 500
  on every attempt, issue #173) is reported and skipped, not treated as the
  instrumento failing.

Issue #148 turns the refresh above from all-or-nothing into law-by-law, with
two additions and no change to what a plain full sweep does:

- ``--plan`` answers, **without a single request to the SCJN**, which laws
  are pending: `scjn.state.motivo_pendiente` compares each catalogue
  entry's `actualizado` against the one that instrumento was actually
  crawled with, recorded in its own ``estado.json`` (see below), falling
  back to the collection-wide ``.rastreo_completo.json`` only when there is
  no per-law state yet. It prints the laws that changed, the ones never
  crawled, and — counted apart, never in the work list unless
  ``--incluye-sin-actualizado`` says so — the ones nothing dates at all. It
  also prints the date `refresca_catalogo()` last ran, since `actualizado`
  comes from it, so an empty list reads as "up to date as of *that* date",
  not "up to date with today's DOF". It refreshes first, since planning
  against a stale `actualizado` answers a stale question
  (``--sin-refrescar-catalogo`` opts out).
- ``--actualiza`` runs the whole chain for exactly the instrumentos that plan
  lists: crawl, link (`enlaza_scjn_legislacion.py`), and repackage their
  assets (`empaqueta_scjn_leyes.py`). It recomputes the plan itself, so
  there is nothing to copy between the two commands, and it exits with no
  effects at all when nothing is pending — the expected case most of the
  time. One law failing does not stop the rest: its `estado.json` is left
  untouched so the next plan lists it again, and the run ends with a summary
  and a non-zero exit status. The DOF titles the linking step needs are
  streamed from the notas-archivo cache (``nota2md download gazette-metadata``
  populates it; ``--cache-dir`` points at your own directory).
- ``--instrumento SLUG`` (repeatable) crawls only the named instrumentos and
  touches the SCJN for nothing else — the manual escape hatch for one
  specific law, underneath what ``--actualiza`` drives. Unlike
  ``--reintenta``, which exists for issue #115's "the SCJN returned the
  wrong document" case, this **deletes nothing**: the snapshots already on
  disk stay, and only the reforms that are new get downloaded.

Publishing is still not part of any of this (issue #115, Hallazgo C):
``--actualiza`` stops once the assets are written, and a human reads
``MANIFEST.md`` and runs ``gh release upload`` by hand.

Either way, an instrumento that finishes crawling writes its own
``<outdir>/leyes/<slug>/estado.json`` with the `actualizado` it was
crawled against and the date it was crawled, so the next ``--plan`` can tell
that one law apart from the rest of the collection. That file ships inside
the instrumento's own tarball (issue #128), so the release carries its own
freshness metadata.

    # que hay que actualizar (no toca la SCJN):
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --plan
    # actualizarlo (rastrea + enlaza + empaqueta, solo lo pendiente):
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --actualiza

Needs at least one law already seeded under ``<outdir>/leyes/`` -- a
directory named by its slug (`scjn.catalog.slug_instrumento`) holding an
`estado.json` with at least `abrev`/`nombre` -- since issue #210 retired the
single `catalogo.json` file that used to enumerate every known law up front.
`scripts/discover_federal_laws.py` is how a brand-new law gets that seed;
the corpus this repo already has is the common case, and needs nothing
extra to keep rastreando.

    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion \
        --reintenta ccf --reintenta lisr --reintenta lsint --reintenta lfd --reintenta lopgjdf
"""

import argparse
import importlib.util
import json
import sys
import time
from datetime import date
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "nota2md"))
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from dofjson.titulos import SIN_CACHE_DIR, legal_provisions_titles  # noqa: E402
from nota2md.linking import newest_dof_publication_dates  # noqa: E402
from scjn import api as scjn_api  # noqa: E402
from scjn.api import elige_ordenamiento  # noqa: E402
from scjn.catalog import search_name, slug_instrumento  # noqa: E402
from scjn.release import AssetNotCached, download_scjn_leyes_index  # noqa: E402
from scjn.state import (  # noqa: E402
    ARCHIVO_ESTADO,
    PENDIENTE_CAMBIO,
    PENDIENTE_NUNCA_RASTREADO,
    PENDIENTE_SIN_ACTUALIZADO,
    escribe_estado,
    lee_estado,
    motivo_pendiente,
)

#: The one collection left (issue #189), a literal path segment. It is also
#: the one with a per-instrument release to repackage into
#: (`empaqueta_scjn_leyes.py`, issue #128).
COLECCION = "leyes"


def _load_catalog(outdir: Path) -> list[dict]:
    """Every law this workspace already tracks -- one entry per subdirectory
    of ``<outdir>/leyes/``, sorted by slug, in place of the retired
    `catalogo.json` (issue #210). `abrev`/`nombre_scjn`/`actualizado` come
    from the law's own `estado.json` when a crawl or the one-time backfill
    has already written them there; `abrev` is never re-derived (#186) --
    only ever taken verbatim from `estado.json`, falling back to the
    directory's own slug. `nombre` falls back to the `scjn-leyes` release's
    `indice-global.json.gz` for a directory whose `estado.json` predates
    issue #210 or has none at all yet -- the same tolerance
    `scjn.release.download_scjn_leyes_catalog` gives an old-format tarball.

    Every directory under ``<outdir>/leyes/`` is included, unconditionally:
    that *is* the new floor issue #210 asks for (an entry never silently
    drops out, because there is no second file to drop out of), stronger
    than `catalogo.json`'s old floor.

    Raises `SystemExit` for a directory whose `nombre` cannot be found
    anywhere -- a brand-new law seeded by hand ahead of its first crawl
    needs at least `abrev`/`nombre` written into its own `estado.json`
    first (`scripts/discover_federal_laws.py` prints both to copy in)."""
    base = outdir / COLECCION
    if not base.is_dir():
        raise SystemExit(
            f"{base} no existe -- crea al menos una ley con su propio estado.json "
            "(ver scripts/discover_federal_laws.py) antes de rastrear la coleccion"
        )
    try:
        instrumentos = download_scjn_leyes_index()["instrumentos"]
    except AssetNotCached:
        instrumentos = {}

    catalogo = []
    for directorio in sorted(p for p in base.iterdir() if p.is_dir()):
        slug = directorio.name
        estado = lee_estado(directorio)
        nombre = estado.get("nombre") or instrumentos.get(slug, {}).get("nombre")
        if nombre is None:
            raise SystemExit(
                f"{directorio}: sin 'nombre' (ni en su propio estado.json ni en el "
                f"indice del release) -- escribe uno a mano en {directorio / ARCHIVO_ESTADO} "
                "antes de rastrear esta ley"
            )
        entrada = {"abrev": estado.get("abrev") or slug, "nombre": nombre}
        if estado.get("nombre_scjn"):
            entrada["nombre_scjn"] = estado["nombre_scjn"]
        if estado.get("actualizado"):
            entrada["actualizado"] = estado["actualizado"]
        catalogo.append(entrada)
    return catalogo


def _archivo_progreso(outdir: Path) -> Path:
    return outdir / COLECCION / ".progreso.json"


def _lee_progreso(outdir: Path) -> str | None:
    """The slug (`scjn.catalog.slug_instrumento`, issue #210 -- a 1-based
    position in `catalogo.json`'s order until then) of the last instrumento a
    previous, interrupted run fully attempted -- None when there is no
    checkpoint (first run, or a collection that already finished and had its
    checkpoint cleared). A malformed/unreadable checkpoint is treated the
    same as none, rather than raising: worst case a finished instrumento gets
    re-attempted, which `scjn.api.descarga_ordenamiento`'s own file-level
    skip already makes cheap."""
    try:
        return json.loads(_archivo_progreso(outdir).read_text(encoding="utf-8"))["slug"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _guarda_progreso(outdir: Path, slug: str) -> None:
    archivo = _archivo_progreso(outdir)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"slug": slug}), encoding="utf-8")


def _archivo_rastreo_completo(outdir: Path) -> Path:
    return outdir / COLECCION / ".rastreo_completo.json"


def _lee_fecha_rastreo_completo(outdir: Path) -> str | None:
    """The ISO date the collection was last crawled start-to-finish under
    Mecanismo 2 (issue #124's follow-up), or None the first time this runs
    after the mechanism was added, or after a malformed/missing checkpoint
    -- treated the same as "no known previous full crawl": nothing gets
    skipped, exactly as if Mecanismo 2 did not exist yet."""
    try:
        campos = json.loads(_archivo_rastreo_completo(outdir).read_text(encoding="utf-8"))
        return campos["fecha"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _guarda_fecha_rastreo_completo(outdir: Path, fecha: str) -> None:
    archivo = _archivo_rastreo_completo(outdir)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"fecha": fecha}), encoding="utf-8")


def _imprime_avance(mensaje: str) -> None:
    """`scjn.api.descarga_ordenamiento`'s own `on_progreso` callback (issue #140,
    Causa 2): indented under its instrumento's own `[leyes i/N]` line so
    it reads as a sub-step, not another instrumento."""
    print(f"    {mensaje}", file=sys.stderr)


def _script_hermano(nombre: str):
    """Another `scripts/` script, imported by path so its own `main(argv)`
    can be called in-process — `scripts/` is not a package, and the chain of
    issue #148 is short enough that shelling out four subprocesses to run it
    would only add ways for it to break silently.

    Imported lazily, inside the functions that need it: `--plan` must stay
    cheap and offline, and `enlaza_scjn_legislacion` pulls in
    `nota2md.builder` (and its dependencies) just by being imported."""
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _resolver_cache_dir(valor: str | None):
    """--cache-dir's value, resolved the same way `enlaza_scjn_legislacion.py`
    resolves its own: not given -> SIN_CACHE_DIR (dofjson.titulos.CACHE_DIR);
    'none' -> None (into memory); anything else -> that path. Kept as a small
    local copy rather than reached for through `_script_hermano` so `--plan`
    and a plain crawl (neither of which need the DOF titles cache) do not pay
    for importing `enlaza_scjn_legislacion.py`, which pulls in
    `nota2md.builder`."""
    if valor is None:
        return SIN_CACHE_DIR
    if valor.lower() == "none":
        return None
    return Path(valor)


def _iso(fecha: str | None) -> str | None:
    """`DD-MM-YYYY` (the SCJN reform table's own shape) as ISO `YYYY-MM-DD`."""
    if not fecha or len(fecha) != 10:
        return None
    return f"{fecha[6:10]}-{fecha[3:5]}-{fecha[0:2]}"


def scjn_dates(
    catalogo: list[dict], outdir: Path, *, api: scjn_api.ScjnApi | None = None, log=None
) -> dict[str, str]:
    """slug -> the newest `fecha_publicacion` the SCJN's reform table reports,
    for every law it answers for.

    `id_ordenamiento` comes from the law's own `estado.json` under `outdir`
    when it has been crawled; otherwise the law is searched by name first,
    through exactly the candidate selection the crawl uses
    (`elige_ordenamiento`), so this can never date a law off the wrong
    document. A law the SCJN does not answer for at all is simply absent —
    that is the `lfca` case, and the DOF half covers it.

    A failure on one law never aborts the refresh over one law's metadata:
    it is warned about and skipped, the same posture the crawl itself takes.
    """
    api = api or scjn_api.ScjnApi()
    fechas: dict[str, str] = {}
    for entrada in catalogo:
        slug = slug_instrumento(entrada)
        id_ordenamiento = lee_estado(outdir / COLECCION / slug).get("id_ordenamiento")
        try:
            if id_ordenamiento is None:
                elegido = elige_ordenamiento(
                    api.search_ordenamiento(entrada.get("nombre_scjn") or entrada["nombre"]),
                    entrada["nombre"],
                )
                if elegido is None:
                    continue
                id_ordenamiento = elegido.idOrdenamiento
            reformas = api.reformas_of_ordenamiento(id_ordenamiento)
        except Exception as exc:
            if log:
                log(f"  warning: SCJN gave no reform table for {slug}: {exc}")
            continue
        publicadas = [_iso(r.fecha_publicacion) for r in reformas]
        publicadas = [f for f in publicadas if f]
        if publicadas:
            fechas[slug] = max(publicadas)
    return fechas


def dof_dates(catalogo: list[dict], *, cache_dir=None, log=None) -> dict[str, str]:
    """slug -> the newest DOF publication date whose title names the law and
    opens with DECRETO/LEY. One pass over the whole titles stream for the
    whole collection, not one lookup per law."""
    instrumentos = {slug_instrumento(e): e["nombre"] for e in catalogo}
    titulos = (
        legal_provisions_titles(log=(lambda *_a, **_k: None))
        if cache_dir is None
        else legal_provisions_titles(cache_dir, log=(lambda *_a, **_k: None))
    )
    if log:
        log(f"  leyendo titulos del DOF para {len(instrumentos)} ley(es)...")
    return newest_dof_publication_dates(instrumentos, titulos)


def _archivo_refresco(outdir: Path) -> Path:
    return outdir / COLECCION / ".actualizado_refrescado.json"


def _fecha_refresco(outdir: Path) -> str | None:
    """The ISO date `refresca_catalogo` last ran on, or None the first time.

    Printed by `--plan` because the corpus can never be fresher than the
    refresh it is planned against: "0 pendientes" means "up to date as of
    this date", not "up to date with today's DOF". Replaces
    `catalogo.json`'s own mtime (issue #210): there is no single file to
    stat any more, since `actualizado_scjn`/`actualizado_dof`/`actualizado`
    are now written into each law's own `estado.json`."""
    try:
        return json.loads(_archivo_refresco(outdir).read_text(encoding="utf-8"))["fecha"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def refresca_catalogo(outdir: Path, *, cache_dir=SIN_CACHE_DIR, dof_only: bool = False) -> None:
    """Refresh every known law's own `actualizado_scjn`/`actualizado_dof`/
    `actualizado` before planning against them (issue #148, folded into
    issue #210's per-law `estado.json`). Without this the plan is computed
    against whatever `actualizado` values were written last time, which is
    exactly the question being asked — so it is the default, and
    `--sin-refrescar-catalogo` is for when the refresh just ran by hand and
    there is no reason to pay for it twice.

    Each source's own date is read as a floor, not overwritten blindly: a
    law this run's `scjn_dates`/`dof_dates` did not answer for (a transient
    SCJN failure, or `dof_only=True` skipping the SCJN half entirely) keeps
    whichever value its own `estado.json` already carried, since neither
    source's date can ever move backwards in practice — under-reporting
    `actualizado` by discarding a still-true previous answer would be worse
    than a run that changes nothing for that law. `dof_only=True` skips the
    SCJN reform table and makes the whole refresh offline, at the cost of
    the omnibus-decree laws only the SCJN half dates (see the module
    docstring) -- it is for a quick local refresh, not for a run whose plan
    anyone acts on."""
    log = lambda mensaje: print(mensaje, file=sys.stderr)  # noqa: E731
    log(f"{COLECCION}: refrescando 'actualizado' desde la SCJN y el DOF...")
    catalogo = _load_catalog(outdir)
    fechas_scjn = {} if dof_only else scjn_dates(catalogo, outdir, log=log)
    fechas_dof = dof_dates(catalogo, cache_dir=cache_dir, log=log)

    sin_fecha = 0
    for entrada in catalogo:
        slug = slug_instrumento(entrada)
        destino = outdir / COLECCION / slug
        existente = lee_estado(destino)
        actualizado_scjn = fechas_scjn.get(slug, existente.get("actualizado_scjn"))
        actualizado_dof = fechas_dof.get(slug, existente.get("actualizado_dof"))
        campos = {}
        if actualizado_scjn:
            campos["actualizado_scjn"] = actualizado_scjn
        if actualizado_dof:
            campos["actualizado_dof"] = actualizado_dof
        candidatos = [f for f in (actualizado_scjn, actualizado_dof) if f]
        if candidatos:
            campos["actualizado"] = max(candidatos)
        else:
            sin_fecha += 1
        if campos:
            escribe_estado(destino, **campos)

    _archivo_refresco(outdir).parent.mkdir(parents=True, exist_ok=True)
    _archivo_refresco(outdir).write_text(
        json.dumps({"fecha": date.today().isoformat()}), encoding="utf-8"
    )
    log(f"{COLECCION}: {len(catalogo)} instrumento(s), {sin_fecha} sin 'actualizado'")


def actualiza_coleccion(
    outdir: Path,
    espera: float,
    *,
    destino: Path,
    cache_dir=SIN_CACHE_DIR,
    refresca: bool = True,
    dof_only: bool = False,
    incluye_sin_actualizado: bool = False,
    empaqueta: bool = True,
) -> int:
    """The whole issue #148 chain in one call: refresh the catalogue, work
    out what is pending, and for each pending instrumento crawl it, link it
    and — once, at the end — repackage the assets that changed. Returns how
    many instrumentos failed, so a caller can make it the process's own exit
    status.

    One law failing does not stop the others: a crawl that raised or found
    nothing leaves that instrumento's own `estado.json` untouched, so the
    next `--plan` lists it again, and the run moves on to the next law. The
    summary at the end says which ones need another try.

    The DOF titles are streamed once (issue #166) and the grouping reused for
    every instrumento — `enlaza_coleccion` takes it as an argument precisely
    so a chain like this does not walk the whole notas-archivo cache once per
    law.

    Packaging is `empaqueta_scjn_leyes.py` (issue #128), the per-instrument
    release of this one collection."""
    if refresca:
        refresca_catalogo(outdir, cache_dir=cache_dir, dof_only=dof_only)
    pendientes = planea_coleccion(
        outdir, incluye_sin_actualizado=incluye_sin_actualizado
    )
    if not pendientes:
        print(f"{COLECCION}: nada que actualizar; no se toca la SCJN", file=sys.stderr)
        return 0

    enlaza = _script_hermano("enlaza_scjn_legislacion")
    porf = enlaza.carga_porf(
        legal_provisions_titles(cache_dir, log=lambda *_: None)
    )
    cache_notas: dict = {}

    listos, fallidos = [], []
    for n, slug in enumerate(pendientes, 1):
        print(f"\n== [{n}/{len(pendientes)}] {slug} ==", file=sys.stderr)
        try:
            if rastrea_coleccion(outdir, espera, instrumento={slug}):
                raise RuntimeError("la SCJN no devolvio nada para este instrumento")
            enlaza.enlaza_coleccion(outdir, porf, cache_notas, instrumento={slug})
        except Exception as exc:
            print(f"  aviso: {slug} no se pudo actualizar: {exc}", file=sys.stderr)
            fallidos.append(slug)
        else:
            listos.append(slug)

    if listos and empaqueta:
        print(f"\n== empaquetando {len(listos)} instrumento(s) ==", file=sys.stderr)
        _script_hermano("empaqueta_scjn_leyes").main(
            ["--outdir", str(outdir), "--destino", str(destino)]
            + [arg for slug in listos for arg in ("--instrumento", slug)]
        )

    print(f"\n{COLECCION}: {len(listos)} actualizado(s), {len(fallidos)} fallido(s)", file=sys.stderr)
    if fallidos:
        print(
            f"  vuelve a correr --actualiza para reintentar {sorted(fallidos)}: no se les "
            "escribio estado.json, asi que el proximo plan los sigue listando",
            file=sys.stderr,
        )
    return len(fallidos)


def planea_coleccion(
    outdir: Path, *, incluye_sin_actualizado: bool = False
) -> list[str]:
    """Print which instrumentos need a refresh and return
    their slugs, sorted — issue #148's planner. Makes no request to the SCJN
    whatsoever: the whole decision comes from every law's own `estado.json`
    (`scjn.state.motivo_pendiente`).

    Instrumentos with no `actualizado` in the catalogue are always printed,
    counted apart, and only included in the returned work list when
    `incluye_sin_actualizado` — nothing can tell whether they changed, so
    re-searching them is a human's call, not a default (issue #148, punto 5)."""
    instrumentos = _load_catalog(outdir)
    fecha_corpus = _lee_fecha_rastreo_completo(outdir)
    por_motivo: dict[str, list[tuple[str, str]]] = {}
    for entrada in instrumentos:
        slug = slug_instrumento(entrada)
        motivo = motivo_pendiente(entrada, outdir / COLECCION / slug, fecha_corpus)
        if motivo is not None:
            por_motivo.setdefault(motivo, []).append((slug, entrada["nombre"]))

    cambiados = por_motivo.get(PENDIENTE_CAMBIO, [])
    nunca = por_motivo.get(PENDIENTE_NUNCA_RASTREADO, [])
    sin_fecha = por_motivo.get(PENDIENTE_SIN_ACTUALIZADO, [])

    print(f"{COLECCION}: {len(instrumentos)} instrumento(s) en el corpus", file=sys.stderr)
    for titulo, entradas in (
        (f"cambiaron desde su ultimo rastreo ({len(cambiados)})", cambiados),
        (f"nunca rastreados ({len(nunca)})", nunca),
    ):
        print(f"  {titulo}:", file=sys.stderr)
        for slug, nombre in entradas:
            print(f"    {slug}  {nombre}", file=sys.stderr)
        if not entradas:
            print("    (ninguno)", file=sys.stderr)
    print(
        f"  sin 'actualizado' en el catalogo ({len(sin_fecha)}) -- no se puede saber si "
        "cambiaron; se rastrean solo con --incluye-sin-actualizado:",
        file=sys.stderr,
    )
    for slug, nombre in sin_fecha:
        print(f"    {slug}  {nombre}", file=sys.stderr)

    fecha_refresco = _fecha_refresco(outdir)
    if fecha_refresco:
        print(
            f"  'actualizado' se refresco el {fecha_refresco} (SCJN + DOF): sin "
            "pendientes significa al dia hasta esa fecha, no hasta el DOF de hoy",
            file=sys.stderr,
        )
    else:
        print(
            "  aviso: no se pudo leer la fecha del ultimo refresco (de donde sale "
            "'actualizado'); el plan de arriba sigue siendo valido, pero no se sabe "
            "hasta que fecha esta al dia",
            file=sys.stderr,
        )

    pendientes = [slug for slug, _ in cambiados + nunca]
    if incluye_sin_actualizado:
        pendientes += [slug for slug, _ in sin_fecha]
    print(f"  {len(pendientes)} instrumento(s) por actualizar", file=sys.stderr)
    if pendientes:
        # The whole chain, not the four steps: --actualiza recomputes this
        # same plan, so there is nothing to copy across from here by hand.
        print(
            "  python scripts/fetch_scjn_legislacion.py --outdir "
            f"{outdir} --actualiza",
            file=sys.stderr,
        )
    return pendientes


def rastrea_coleccion(
    outdir: Path,
    espera: float,
    *,
    reiniciar: bool = False,
    reintenta: set[str] | None = None,
    instrumento: set[str] | None = None,
) -> list[str]:
    """Crawl the collection and return the slugs whose crawl did not succeed —
    the SCJN raised, or returned nothing at all for them. Failures were
    already printed and skipped over rather than aborting the run; returning
    them as well is what lets `actualiza_coleccion` (issue #148) tell which
    laws are actually ready for the next step of the chain."""
    instrumentos = _load_catalog(outdir)
    print(f"{COLECCION}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    # Mecanismo 2 (issue #124's follow-up): the date this collection was last
    # crawled start-to-finish, so an up-to-date instrumento can be skipped
    # below without touching the SCJN at all -- see instrument_up_to_date().
    # --reiniciar bypasses this the same way it bypasses .progreso.json.
    fecha_corpus = None if reiniciar else _lee_fecha_rastreo_completo(outdir)
    # Issue #140, Causa 1: `actualizado` is only ever written by
    # refresca_catalogo(), never by the crawl below -- a corpus where no law's
    # own estado.json carries it (never refreshed, or hand-seeded) means
    # Mecanismo 2 has nothing to compare against and will never skip
    # anything, silently, run after run. Said explicitly instead of just
    # doing nothing: the fix is one command, not a rewrite.
    if not reiniciar and instrumentos and not any(e.get("actualizado") for e in instrumentos):
        print(
            f"  aviso: ningun instrumento de {COLECCION} trae 'actualizado' -- "
            "el Mecanismo 2 (refresh incremental, issue #124) no puede saltar nada hasta correr "
            f"./scripts/fetch_scjn_legislacion.py --outdir {outdir} --plan",
            file=sys.stderr,
        )
    # --instrumento and --reintenta both narrow the sweep to named slugs and
    # both leave the collection's own .progreso.json alone (a partial sweep
    # is not a sweep); what separates them is that --reintenta deletes the
    # instrumento's snapshots first (issue #115's wrong-document case) and
    # --instrumento never deletes anything (issue #148's incremental case).
    seleccion = reintenta if reintenta is not None else instrumento
    if seleccion is not None:
        faltantes = seleccion - {slug_instrumento(e) for e in instrumentos}
        if faltantes:
            raise SystemExit(
                f"{sorted(faltantes)} no esta(n) en {outdir / COLECCION} -- revisa el slug "
                "(scjn.catalog.slug_instrumento) o seedealo primero con su propio estado.json "
                "(ver scripts/discover_federal_laws.py)"
            )
    if seleccion is not None:
        bandera = "--reintenta" if reintenta is not None else "--instrumento"
        print(
            f"  {bandera}: solo se {'re-bajaran' if reintenta is not None else 'rastrearan'} "
            f"{sorted(seleccion)}; el resto se asume ya bajado y no se toca la SCJN",
            file=sys.stderr,
        )
        inicio_slug = None
    else:
        inicio_slug = None if reiniciar else _lee_progreso(outdir)
        if inicio_slug is not None:
            print(
                f"  reanudando despues del instrumento {inicio_slug!r} "
                "(progreso guardado de una corrida anterior)",
                file=sys.stderr,
            )
    # One client for the whole collection: it holds the connection pool and
    # the rate limit, and nothing in it is scoped to an instrument (unlike
    # the WebForms session it replaced, which had to be new for each one --
    # its detail-page `q` token was scoped to the session that got it).
    cliente_api = scjn_api.ScjnApi(espera=espera)
    saltados = 0
    fallidos = []
    descuadres: list[tuple] = []
    # Issue #210: the checkpoint is a slug, not a position, so it is immune
    # to the catalogue's own order changing between runs (there is no longer
    # a single file whose order even could change) -- resuming just skips
    # everything up to and including the slug last fully attempted.
    saltar_hasta_slug = inicio_slug is not None
    for i, entrada in enumerate(instrumentos, 1):
        slug = slug_instrumento(entrada)
        if saltar_hasta_slug:
            if slug == inicio_slug:
                saltar_hasta_slug = False
            continue
        if seleccion is not None and slug not in seleccion:
            continue
        nombre = entrada["nombre"]
        destino = outdir / COLECCION / slug
        # Issue #148: the skip is now decided per instrumento against its own
        # estado.json, and only falls back to the collection-wide checkpoint
        # when it has none -- so a law refreshed on its own counts as up to
        # date without waiting for a full sweep to finish. Naming it
        # explicitly (--instrumento/--reintenta) always crawls it.
        if seleccion is None and motivo_pendiente(entrada, destino, fecha_corpus) is None:
            saltados += 1
        else:
            buscado = search_name(entrada)
            if reintenta is not None:
                for archivo in destino.glob("*.md"):
                    archivo.unlink()
                (destino / "indice.json").unlink(missing_ok=True)
                # Its estado.json goes with them: it recorded that the
                # snapshots just deleted were up to date, which stops being
                # true the moment they are gone (issue #148).
                (destino / ARCHIVO_ESTADO).unlink(missing_ok=True)
            etiqueta = nombre if buscado == nombre else f"{nombre} (buscado como {buscado!r})"
            print(f"[{COLECCION} {i}/{len(instrumentos)}] {etiqueta}", file=sys.stderr)
            id_ordenamiento = None
            try:
                # Issue #140, Causa 2: a large instrumento (the CPEUM's 301
                # reforms is the confirmed case) otherwise goes
                # silent for as long as its own crawl takes --
                # indistinguishable from a hung process.
                # Issue #177: an `id_ordenamiento` a previous run already
                # resolved skips the search step entirely -- the whole
                # point of an instrument being addressable by a stable id
                # instead of a session URL. --reintenta deliberately does
                # not reuse it: re-downloading a wrong document from the
                # same id would defeat the purpose (issue #115).
                if reintenta is None:
                    id_previo = lee_estado(destino).get("id_ordenamiento")
                else:
                    id_previo = None
                resultado = scjn_api.descarga_ordenamiento(
                    cliente_api,
                    buscado,
                    destino,
                    on_progreso=_imprime_avance,
                    id_ordenamiento=id_previo,
                )
                escritos = resultado.escritos
                if resultado.ordenamiento is not None:
                    id_ordenamiento = resultado.ordenamiento.idOrdenamiento
                for sin in resultado.reformas_sin_articulos:
                    print(
                        f"  sin texto consolidado (tieneArticulos=false): {sin}",
                        file=sys.stderr,
                    )
                for fallida in resultado.reformas_fallidas:
                    print(f"  aviso: reforma no servida por la SCJN: {fallida}", file=sys.stderr)
                # Issue #178: check the crawl against the SCJN's own reform
                # count for this instrumento -- its detail page shows that
                # number, and a silent shortfall is exactly how a paging
                # bug hid ~106 missing snapshots in the first full crawl.
                cubiertas = len(escritos) + len(resultado.reformas_sin_articulos)
                if resultado.total_reformas and cubiertas != resultado.total_reformas:
                    print(
                        f"  DESCUADRE: la SCJN reporta {resultado.total_reformas} reforma(s) "
                        f"y quedaron {len(escritos)} snapshot(s) + "
                        f"{len(resultado.reformas_sin_articulos)} sin texto consolidado "
                        f"= {cubiertas}",
                        file=sys.stderr,
                    )
                    descuadres.append(
                        (slug, resultado.total_reformas, len(escritos),
                         len(resultado.reformas_sin_articulos))
                    )
            except Exception as exc:
                print(f"  aviso: {buscado!r} fallo: {exc}", file=sys.stderr)
                fallidos.append(slug)
            else:
                if not escritos:
                    print(f"  aviso: sin resultados en la SCJN para {buscado!r}", file=sys.stderr)
                    fallidos.append(slug)
                # Issue #148: record the `actualizado` this instrumento was
                # actually crawled against -- the catalogue's value now, not
                # today's date, so a later refresh compares like with like.
                # Only after a crawl that did not raise: a failed one leaves
                # the previous state alone rather than claiming freshness it
                # does not have. An instrumento the SCJN has nothing for
                # still gets no `estado.json` directory of snapshots, so
                # `motivo_pendiente` keeps returning `nunca_rastreado` for it.
                if destino.is_dir():
                    campos = dict(
                        actualizado=entrada.get("actualizado"),
                        rastreado=date.today().isoformat(),
                    )
                    # Issue #177: only ever added, never required --
                    # `lee_estado`/`motivo_pendiente` read an estado.json
                    # written before this field existed exactly as they did.
                    if id_ordenamiento is not None:
                        campos["id_ordenamiento"] = id_ordenamiento
                    escribe_estado(destino, **campos)
            time.sleep(espera)
        if seleccion is None:
            _guarda_progreso(outdir, slug)
    if saltados:
        print(
            f"  {COLECCION}: {saltados} instrumento(s) skipped -- already up to date, "
            "SCJN not touched (Mecanismo 2, issue #124)",
            file=sys.stderr,
        )
    if descuadres:
        print(
            f"\n  {COLECCION}: {len(descuadres)} instrumento(s) NO cuadran con el "
            "numero de reformas que reporta la SCJN:",
            file=sys.stderr,
        )
        for slug_d, total_d, escritos_d, sin_d in descuadres:
            print(
                f"    {slug_d}: reformas={total_d} snapshots={escritos_d} "
                f"sin_texto={sin_d} faltan={total_d - escritos_d - sin_d}",
                file=sys.stderr,
            )
    # Only a sweep of the whole collection may claim it was crawled
    # start-to-finish: --instrumento/--reintenta deliberately skipped most of
    # it, so neither the checkpoint nor the full-crawl date apply to them.
    if seleccion is None:
        _archivo_progreso(outdir).unlink(missing_ok=True)
        _guarda_fecha_rastreo_completo(outdir, date.today().isoformat())
    return fallidos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--outdir", type=Path, required=True)
    p.add_argument(
        "--espera",
        type=float,
        default=1.0,
        help="segundos de espera entre solicitudes a la SCJN (default: 1.0)",
    )
    p.add_argument(
        "--reiniciar",
        action="store_true",
        help=(
            "ignora el progreso guardado de una corrida anterior y el checkpoint de "
            "'ya actualizado' (Mecanismo 2, issue #124); rastrea la coleccion completa "
            "desde el principio"
        ),
    )
    p.add_argument(
        "--reintenta",
        action="append",
        metavar="SLUG",
        help=(
            "repetible; slug_instrumento (scjn.catalog.slug_instrumento, p.ej. ccf, "
            "lisr) a re-descargar desde cero: se borran sus snapshots existentes y su "
            "indice.json y se re-bajan; todo instrumento cuyo slug no este en la lista "
            "se salta sin tocar la SCJN. No mezclar con --reiniciar."
        ),
    )
    p.add_argument(
        "--instrumento",
        action="append",
        metavar="SLUG",
        help=(
            "repetible; slug_instrumento a rastrear, sin tocar la SCJN para ningun otro "
            "(issue #148). A diferencia de --reintenta, no borra nada: los snapshots ya "
            "en disco se conservan y solo se bajan las reformas nuevas."
        ),
    )
    p.add_argument(
        "--api",
        action="store_true",
        help=(
            "sin efecto: la API es el unico camino desde el issue #179. Se acepta para "
            "que un comando escrito durante la transicion (issue #177) siga corriendo"
        ),
    )
    p.add_argument(
        "--plan",
        action="store_true",
        help=(
            "no rastrea nada: imprime que instrumentos cambiaron desde su ultimo "
            "rastreo (issue #148) y sale. No hace ninguna peticion a la SCJN"
        ),
    )
    p.add_argument(
        "--actualiza",
        action="store_true",
        help=(
            "la cadena completa de issue #148 en un solo comando: refresca el catalogo, "
            "calcula el plan, y para cada instrumento pendiente rastrea, enlaza y "
            "reempaqueta su asset. Sale sin efectos cuando no hay pendientes"
        ),
    )
    p.add_argument(
        "--sin-refrescar-catalogo",
        action="store_true",
        help=(
            "no vuelve a refrescar 'actualizado' desde la SCJN y el DOF antes de "
            "planear (--plan/--actualiza); usalo solo si ese refresco acaba de correr"
        ),
    )
    p.add_argument(
        "--dof-only",
        action="store_true",
        help=(
            "el refresco de 'actualizado' (--plan/--actualiza) se salta la tabla de "
            "reformas de la SCJN; offline, pero sub-fecha las leyes reformadas solo "
            "por decretos omnibus (ver el docstring del modulo)"
        ),
    )
    p.add_argument(
        "--cache-dir",
        default=None,
        metavar="DIR",
        help=(
            "directorio con los assets .tgz de notas-archivo de donde el paso de "
            "enlace de --actualiza lee los titulos del DOF (poblalo con "
            "`nota2md download gazette-metadata`); sin valor: "
            "dofjson.titulos.CACHE_DIR; 'none': a memoria"
        ),
    )
    p.add_argument(
        "--destino",
        type=Path,
        default=Path("scripts/scjn/leyes-release"),
        help="donde --actualiza reescribe los assets del release (default: %(default)s)",
    )
    p.add_argument(
        "--sin-empaquetar",
        action="store_true",
        help="con --actualiza, se detiene despues de enlazar y no reescribe ningun asset",
    )
    p.add_argument(
        "--incluye-sin-actualizado",
        action="store_true",
        help=(
            "con --plan, incluye en la lista de pendientes los instrumentos sin "
            "'actualizado' en el catalogo (por default solo se cuentan aparte)"
        ),
    )
    args = p.parse_args(argv)

    if args.reintenta and args.instrumento:
        raise SystemExit(
            "--reintenta y --instrumento no se mezclan: el primero borra los snapshots "
            "del instrumento antes de rebajarlos (issue #115), el segundo no borra nada "
            "(issue #148). Elige cual de los dos casos es el tuyo."
        )
    if args.plan and args.actualiza:
        raise SystemExit("--plan solo dice que hay que hacer; --actualiza lo hace. Elige uno.")
    reintenta = set(args.reintenta) if args.reintenta else None
    instrumento = set(args.instrumento) if args.instrumento else None
    fallidos = 0
    if args.plan:
        if not args.sin_refrescar_catalogo:
            refresca_catalogo(
                args.outdir, cache_dir=_resolver_cache_dir(args.cache_dir), dof_only=args.dof_only
            )
        planea_coleccion(
            args.outdir, incluye_sin_actualizado=args.incluye_sin_actualizado
        )
    elif args.actualiza:
        fallidos = actualiza_coleccion(
            args.outdir,
            args.espera,
            destino=args.destino,
            cache_dir=_resolver_cache_dir(args.cache_dir),
            refresca=not args.sin_refrescar_catalogo,
            dof_only=args.dof_only,
            incluye_sin_actualizado=args.incluye_sin_actualizado,
            empaqueta=not args.sin_empaquetar,
        )
    else:
        rastrea_coleccion(
            args.outdir,
            args.espera,
            reiniciar=args.reiniciar,
            reintenta=reintenta,
            instrumento=instrumento,
        )
    # Non-zero when --actualiza left work behind, so a caller (or a human
    # reading `echo $?`) does not mistake a partial run for a clean one.
    return 1 if fallidos else 0


if __name__ == "__main__":
    sys.exit(main())
