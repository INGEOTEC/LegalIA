#!/usr/bin/env python3
"""Crawl the SCJN for every ley, reglamento and tratado that
`extract_scjn_titles.py`'s own catalogue already knows about, and save each
one's reform-dated snapshots as Markdown under
``<outdir>/<coleccion>/<abrev-o-nombre>/<fecha_publicacion>.md`` — Fase 1 of
the crawl plan in issue #105.

Only these three collections: issue #105's Fase 0 spike found that the SCJN
does not catalogue NOM technical standards as ordenamientos of their own at
all (8 real NOM codes, zero hits, even searching "Norma Oficial Mexicana"
generically), so "normas" has nothing here to crawl.

The crawl goes through the SCJN's own SCOW JSON API
(`/SCOW-API`, `nota2md.scjn_api`). Until issue #179 it went instead through
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
  `scjn_api.elige_ordenamiento`, thresholds and hard exclusions unchanged,
  plus signals the old results page never showed (the API's own
  `categoriaOrdenamiento`).

The API is not an official contract either -- a Swagger page is not a
stability promise -- so the rate limiting stays, and the SCJN remains a
non-official source of legal text: the `fuente: scjn` header these files
carry means exactly what it meant before the migration.

Resumable at two levels. Within one instrumento, a file already on disk is
left alone and its reform's download skipped (see
nota2md.scjn_api.descarga_ordenamiento), so a later re-run picking up new
reforms only fetches what is missing. Across a whole collection, the index
of the last instrumento fully attempted is checkpointed to
``<outdir>/<coleccion>/.progreso.json`` and cleared once the collection
finishes; a run killed partway (crash, network drop, Ctrl-C) resumes right
after that index instead of re-walking every already-done instrumento's
reform table from the top — pass ``--reiniciar`` to discard that checkpoint
and sweep the collection from the beginning again (e.g. after the upstream
catalogue itself changed).

A third, narrower case: issue #115's manual audit confirmed 5 instrumentos
(ccf, lisr, lsint, lfd, lopgjdf) where the SCJN search returned, and a past
crawl saved, the wrong document entirely — candidate selection gained guards
against this, but the wrong snapshots already on disk are still there,
untouched, since the per-file skip above has no notion of a snapshot being
*wrong*. ``--reintenta SLUG`` (repeatable, `nota2md.scjn.slug_instrumento`)
re-bajas exactly the named instrumentos: their existing snapshots (and
stale `indice.json`) are deleted first so they are genuinely re-fetched
against the fixed candidate selection, while every instrumento not named is
skipped without touching the SCJN at all — a full collection is ~600
instrumentos, and there is no need to re-walk the other ~595 that were never
wrong just to verify a fix aimed at 5. Leaves the collection's own
``.progreso.json`` checkpoint untouched either way. Rate-limited:
`--espera` seconds between requests, since this is an unofficial site with
no stability contract (the same posture as leyesmx.diputados/dofjson.dofweb
elsewhere in this repo).

A fourth case, issue #124's follow-up ("Dos casos disparadores"): an
instrumento the coverage sweep above never finds *anything* for at all,
either because the SCJN's own full-text search never matches Diputados'
exact wording (`lisipl`), or because the SCJN has not indexed a brand-new
law yet (`lfca`). Two mechanisms close this, both driven entirely by
`catalogo.json` fields `extract_scjn_titles.py` now writes — nothing to
pass on this script's own command line:

- **Mecanismo 1** (manual override): when a catalogue entry carries a
  `nombre_scjn` field, it is searched instead of `nombre`
  (`nota2md.scjn.search_name`) — `nombre` itself is still what gets printed
  and what `enlaza_scjn_legislacion.py` (issue #126) compares against DOF
  titles. Applied so far only to `lisipl`; `lfca`'s own gap is indexing
  lag, not a title mismatch, so no override was correct for it — and the
  API has since indexed the law, which is what actually closed the case.
- **Mecanismo 2** (incremental refresh): once a collection has been crawled
  start-to-finish at least once under this mechanism, that date is
  recorded to ``<outdir>/<coleccion>/.rastreo_completo.json``. A later
  refresh run skips an instrumento without touching the SCJN at all
  (`nota2md.scjn.instrument_up_to_date`) only when it already has a
  snapshot on disk *and* its own `actualizado` (its most recent reform's
  date, `extract_scjn_titles.py`) is no later than that checkpoint — an
  instrumento with no snapshot on disk yet is always retried regardless of
  `actualizado`, so a law like `lfca`, not yet indexed by the SCJN at all,
  keeps getting retried on every refresh automatically, with nothing to
  configure by hand once the SCJN eventually catches up. ``--reiniciar``
  (see below) bypasses this skip too, the same as it bypasses the
  ``.progreso.json`` checkpoint.

Issue #140 found two bugs live in the mechanisms above, both fixed here:
Mecanismo 2 needs `actualizado` on the catalogue to ever skip anything, and
that field is only ever written by `extract_scjn_titles.py` -- a
`catalogo.json` extracted before that field existed (or hand-edited) leaves
it inert with no visible sign why, so this script now warns on stderr, once
per collection, when it sees a catalogue with zero `actualizado` entries.
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
  are pending: `nota2md.scjn.motivo_pendiente` compares each catalogue
  entry's `actualizado` against the one that instrumento was actually
  crawled with, recorded in its own ``estado.json`` (see below), falling
  back to the collection-wide ``.rastreo_completo.json`` only when there is
  no per-law state yet. It prints the laws that changed, the ones never
  crawled, and — counted apart, never in the work list unless
  ``--incluye-sin-actualizado`` says so — the ones whose `actualizado`
  Diputados does not give at all. It also prints the publication date of the
  `historial-legislativo` release `actualizado` itself comes from, so an
  empty list reads as "up to date as of *that* date", not "up to date with
  today's DOF". It refreshes `catalogo.json` from Diputados first, since
  planning against a stale catalogue answers a stale question
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
``<outdir>/<coleccion>/<slug>/estado.json`` with the `actualizado` it was
crawled against and the date it was crawled, so the next ``--plan`` can tell
that one law apart from the rest of the collection. That file ships inside
the instrumento's own tarball (issue #128), so the release carries its own
freshness metadata.

    # que hay que actualizar (no toca la SCJN):
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes --plan
    # actualizarlo (rastrea + enlaza + empaqueta, solo lo pendiente):
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes --actualiza

Needs each requested collection's own ``catalogo.json`` already written by
``extract_scjn_titles.py`` under ``<outdir>/<coleccion>/`` — since issue #186
that catalogue is built from the SCJN and the DOF alone, so nothing in this
pipeline reads anything the Cámara de Diputados published.

    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --coleccion tratados
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --coleccion leyes \
        --reintenta ccf --reintenta lisr --reintenta lsint --reintenta lfd --reintenta lopgjdf
"""

import argparse
import importlib.util
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from dofjson.titulos import SIN_CACHE_DIR, legal_provisions_titles  # noqa: E402
from nota2md.scjn import (  # noqa: E402
    ARCHIVO_ESTADO,
    PENDIENTE_CAMBIO,
    PENDIENTE_NUNCA_RASTREADO,
    PENDIENTE_SIN_ACTUALIZADO,
    escribe_estado,
    lee_estado,
    motivo_pendiente,
    search_name,
    slug_instrumento,
)
from nota2md import scjn_api  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "tratados")
#: The only collection with a per-instrument release to repackage into
#: (`empaqueta_scjn_leyes.py`, issue #128).
COLECCION_EMPAQUETABLE = "leyes"


def _load_catalog(outdir: Path, coleccion: str) -> list[dict]:
    """The `nombre`(+`abrev`) catalogue `extract_scjn_titles.py` already
    wrote for `coleccion` -- Diputados' `historial` never reaches this
    script (issue #123)."""
    archivo = outdir / coleccion / "catalogo.json"
    if not archivo.is_file():
        raise SystemExit(
            f"{archivo} no existe -- corre primero "
            f"./scripts/extract_scjn_titles.py --outdir {outdir}"
        )
    return json.loads(archivo.read_text(encoding="utf-8"))


def _archivo_progreso(outdir: Path, coleccion: str) -> Path:
    return outdir / coleccion / ".progreso.json"


def _lee_progreso(outdir: Path, coleccion: str) -> int:
    """The 1-based index (in `catalogo.json`'s own order, see `_load_catalog`)
    of the last instrumento a previous, interrupted run of `coleccion` fully
    attempted — 0 when there is no checkpoint (first run, or a collection
    that already finished and had its checkpoint cleared).
    A malformed/unreadable checkpoint is treated the same as none, rather
    than raising: worst case a finished instrumento gets re-attempted, which
    `scjn_api.descarga_ordenamiento`'s own file-level skip already makes cheap."""
    try:
        return json.loads(_archivo_progreso(outdir, coleccion).read_text(encoding="utf-8"))["indice"]
    except (OSError, json.JSONDecodeError, KeyError):
        return 0


def _guarda_progreso(outdir: Path, coleccion: str, indice: int) -> None:
    archivo = _archivo_progreso(outdir, coleccion)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"indice": indice}), encoding="utf-8")


def _archivo_rastreo_completo(outdir: Path, coleccion: str) -> Path:
    return outdir / coleccion / ".rastreo_completo.json"


def _lee_fecha_rastreo_completo(outdir: Path, coleccion: str) -> str | None:
    """The ISO date `coleccion` was last crawled start-to-finish under
    Mecanismo 2 (issue #124's follow-up), or None the first time this runs
    after the mechanism was added, or after a malformed/missing checkpoint
    -- treated the same as "no known previous full crawl": nothing gets
    skipped, exactly as if Mecanismo 2 did not exist yet."""
    try:
        campos = json.loads(_archivo_rastreo_completo(outdir, coleccion).read_text(encoding="utf-8"))
        return campos["fecha"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _guarda_fecha_rastreo_completo(outdir: Path, coleccion: str, fecha: str) -> None:
    archivo = _archivo_rastreo_completo(outdir, coleccion)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"fecha": fecha}), encoding="utf-8")


def _imprime_avance(mensaje: str) -> None:
    """`scjn_api.descarga_ordenamiento`'s own `on_progreso` callback (issue #140,
    Causa 2): indented under its instrumento's own `[coleccion i/N]` line so
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


def refresca_catalogo(outdir: Path, coleccion: str) -> None:
    """Re-extract `coleccion`'s own `catalogo.json` before planning against
    it (issue #148). Without this the plan is computed against whatever
    `actualizado` values were extracted last time, which is exactly the
    question being asked — so it is the default, and
    `--sin-refrescar-catalogo` is for when the catalogue was just refreshed
    by hand and there is no reason to pay for it twice.

    Since issue #186 that refresh reads the SCJN's own reform table and the
    DOF titles cache, not Diputados, so a `--plan` run is no longer
    SCJN-free: it costs one reform-table request per law. Refreshing the
    catalogue from the DOF alone (`extract_scjn_titles.py --dof-only`) and
    then planning with `--sin-refrescar-catalogo` is the offline route, at
    the cost that page's own docstring records."""
    print(f"{coleccion}: refrescando catalogo.json desde la SCJN y el DOF...", file=sys.stderr)
    _script_hermano("extract_scjn_titles").main(["--outdir", str(outdir)])


def actualiza_coleccion(
    coleccion: str,
    outdir: Path,
    espera: float,
    *,
    destino: Path,
    cache_dir=SIN_CACHE_DIR,
    refresca: bool = True,
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

    Packaging only exists for `leyes` (`empaqueta_scjn_leyes.py`, issue
    #128); the other collections stop after linking, which is said out loud
    rather than silently skipped."""
    if refresca:
        refresca_catalogo(outdir, coleccion)
    pendientes = planea_coleccion(
        coleccion, outdir, incluye_sin_actualizado=incluye_sin_actualizado
    )
    if not pendientes:
        print(f"{coleccion}: nada que actualizar; no se toca la SCJN", file=sys.stderr)
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
            if rastrea_coleccion(coleccion, outdir, espera, instrumento={slug}):
                raise RuntimeError("la SCJN no devolvio nada para este instrumento")
            enlaza.enlaza_coleccion(coleccion, outdir, porf, cache_notas, instrumento={slug})
        except Exception as exc:
            print(f"  aviso: {slug} no se pudo actualizar: {exc}", file=sys.stderr)
            fallidos.append(slug)
        else:
            listos.append(slug)

    if listos and empaqueta and coleccion == COLECCION_EMPAQUETABLE:
        print(f"\n== empaquetando {len(listos)} instrumento(s) ==", file=sys.stderr)
        _script_hermano("empaqueta_scjn_leyes").main(
            ["--outdir", str(outdir), "--destino", str(destino)]
            + [arg for slug in listos for arg in ("--instrumento", slug)]
        )
    elif listos and empaqueta:
        print(
            f"\n{coleccion} no se empaqueta: el release por instrumento (issue #128) "
            f"existe solo para '{COLECCION_EMPAQUETABLE}'",
            file=sys.stderr,
        )

    print(f"\n{coleccion}: {len(listos)} actualizado(s), {len(fallidos)} fallido(s)", file=sys.stderr)
    if fallidos:
        print(
            f"  vuelve a correr --actualiza para reintentar {sorted(fallidos)}: no se les "
            "escribio estado.json, asi que el proximo plan los sigue listando",
            file=sys.stderr,
        )
    return len(fallidos)


def _fecha_catalogo(outdir: Path, coleccion: str) -> str | None:
    """The date `catalogo.json` was last written, or None when there is no
    file to ask.

    Printed by `--plan` because the corpus can never be fresher than the
    catalogue it is planned against: "0 pendientes" means "up to date as of
    this date", not "up to date with today's DOF". Until issue #186 this
    read the `historial-legislativo` release's publication date over the
    network, because that release was where every `actualizado` came from
    and a monthly workflow republished it. Both facts are gone —
    `actualizado` now comes from the SCJN's reform table and the DOF titles
    at the moment `extract_scjn_titles.py` runs — so the bound is simply
    when that ran, which is a local `stat` and cannot fail on a network."""
    archivo = outdir / coleccion / "catalogo.json"
    try:
        return datetime.fromtimestamp(archivo.stat().st_mtime).date().isoformat()
    except OSError:
        return None


def planea_coleccion(
    coleccion: str, outdir: Path, *, incluye_sin_actualizado: bool = False
) -> list[str]:
    """Print which instrumentos of `coleccion` need a refresh and return
    their slugs, in `catalogo.json`'s own order — issue #148's planner.
    Makes no request to the SCJN whatsoever: the whole decision comes from
    `catalogo.json` plus each instrumento's own `estado.json`
    (`nota2md.scjn.motivo_pendiente`).

    Instrumentos with no `actualizado` in the catalogue are always printed,
    counted apart, and only included in the returned work list when
    `incluye_sin_actualizado` — nothing can tell whether they changed, so
    re-searching them is a human's call, not a default (issue #148, punto 5)."""
    instrumentos = _load_catalog(outdir, coleccion)
    fecha_corpus = _lee_fecha_rastreo_completo(outdir, coleccion)
    por_motivo: dict[str, list[tuple[str, str]]] = {}
    for entrada in instrumentos:
        slug = slug_instrumento(entrada)
        motivo = motivo_pendiente(entrada, outdir / coleccion / slug, fecha_corpus)
        if motivo is not None:
            por_motivo.setdefault(motivo, []).append((slug, entrada["nombre"]))

    cambiados = por_motivo.get(PENDIENTE_CAMBIO, [])
    nunca = por_motivo.get(PENDIENTE_NUNCA_RASTREADO, [])
    sin_fecha = por_motivo.get(PENDIENTE_SIN_ACTUALIZADO, [])

    print(f"{coleccion}: {len(instrumentos)} instrumento(s) en el catalogo", file=sys.stderr)
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

    fecha_catalogo = _fecha_catalogo(outdir, coleccion)
    if fecha_catalogo:
        print(
            f"  'actualizado' se extrajo el {fecha_catalogo} (SCJN + DOF): sin "
            "pendientes significa al dia hasta esa fecha, no hasta el DOF de hoy",
            file=sys.stderr,
        )
    else:
        print(
            "  aviso: no se pudo leer la fecha de catalogo.json (de donde sale "
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
            f"{outdir} --coleccion {coleccion} --actualiza",
            file=sys.stderr,
        )
    return pendientes


def rastrea_coleccion(
    coleccion: str,
    outdir: Path,
    espera: float,
    *,
    reiniciar: bool = False,
    reintenta: set[str] | None = None,
    instrumento: set[str] | None = None,
) -> list[str]:
    """Crawl `coleccion` and return the slugs whose crawl did not succeed —
    the SCJN raised, or returned nothing at all for them. Failures were
    already printed and skipped over rather than aborting the run; returning
    them as well is what lets `actualiza_coleccion` (issue #148) tell which
    laws are actually ready for the next step of the chain."""
    instrumentos = _load_catalog(outdir, coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    # Mecanismo 2 (issue #124's follow-up): the date this collection was last
    # crawled start-to-finish, so an up-to-date instrumento can be skipped
    # below without touching the SCJN at all -- see instrument_up_to_date().
    # --reiniciar bypasses this the same way it bypasses .progreso.json.
    fecha_corpus = None if reiniciar else _lee_fecha_rastreo_completo(outdir, coleccion)
    # Issue #140, Causa 1: `actualizado` is only ever written by
    # extract_scjn_titles.py, never by this script -- a catalogo.json where
    # no entry carries it (a catalogue extracted before that field existed,
    # or a hand-edited one) means Mecanismo 2 has nothing to compare against
    # and will never skip anything, silently, run after run. Said explicitly
    # instead of just doing nothing: the fix is one command, not a rewrite.
    if not reiniciar and instrumentos and not any(e.get("actualizado") for e in instrumentos):
        print(
            f"  aviso: ningun instrumento de {coleccion} trae 'actualizado' en catalogo.json -- "
            "el Mecanismo 2 (refresh incremental, issue #124) no puede saltar nada hasta correr "
            f"./scripts/extract_scjn_titles.py --outdir {outdir}",
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
                f"{sorted(faltantes)} no esta(n) en el catalogo de {coleccion} -- "
                "revisa el slug (nota2md.scjn.slug_instrumento) o refresca catalogo.json "
                "con ./scripts/extract_scjn_titles.py"
            )
    if seleccion is not None:
        bandera = "--reintenta" if reintenta is not None else "--instrumento"
        print(
            f"  {bandera}: solo se {'re-bajaran' if reintenta is not None else 'rastrearan'} "
            f"{sorted(seleccion)}; el resto se asume ya bajado y no se toca la SCJN",
            file=sys.stderr,
        )
        inicio = 0
    else:
        inicio = 0 if reiniciar else _lee_progreso(outdir, coleccion)
        if inicio:
            print(
                f"  reanudando en el instrumento {inicio + 1}/{len(instrumentos)} "
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
    for i, entrada in enumerate(instrumentos, 1):
        if i <= inicio:
            continue
        slug = slug_instrumento(entrada)
        if seleccion is not None and slug not in seleccion:
            continue
        nombre = entrada["nombre"]
        destino = outdir / coleccion / slug
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
            print(f"[{coleccion} {i}/{len(instrumentos)}] {etiqueta}", file=sys.stderr)
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
            _guarda_progreso(outdir, coleccion, i)
    if saltados:
        print(
            f"  {coleccion}: {saltados} instrumento(s) skipped -- already up to date, "
            "SCJN not touched (Mecanismo 2, issue #124)",
            file=sys.stderr,
        )
    if descuadres:
        print(
            f"\n  {coleccion}: {len(descuadres)} instrumento(s) NO cuadran con el "
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
        _archivo_progreso(outdir, coleccion).unlink(missing_ok=True)
        _guarda_fecha_rastreo_completo(outdir, coleccion, date.today().isoformat())
    return fallidos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--outdir", type=Path, required=True)
    p.add_argument(
        "--coleccion",
        choices=COLECCIONES,
        action="append",
        help="repetible; por defecto rastrea las tres",
    )
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
            "repetible; slug_instrumento (nota2md.scjn.slug_instrumento, p.ej. ccf, "
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
            "no vuelve a extraer catalogo.json de Diputados antes de planear "
            "(--plan/--actualiza); usalo solo si acabas de correr extract_scjn_titles.py"
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
    for coleccion in args.coleccion or COLECCIONES:
        if args.plan:
            if not args.sin_refrescar_catalogo:
                refresca_catalogo(coleccion=coleccion, outdir=args.outdir)
            planea_coleccion(
                coleccion, args.outdir, incluye_sin_actualizado=args.incluye_sin_actualizado
            )
            continue
        if args.actualiza:
            fallidos += actualiza_coleccion(
                coleccion,
                args.outdir,
                args.espera,
                destino=args.destino,
                cache_dir=_script_hermano(
                    "enlaza_scjn_legislacion"
                )._resolver_cache_dir(args.cache_dir),
                refresca=not args.sin_refrescar_catalogo,
                incluye_sin_actualizado=args.incluye_sin_actualizado,
                empaqueta=not args.sin_empaquetar,
            )
            continue
        rastrea_coleccion(
            coleccion,
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
