"""Incremental, resumable download of the DOF daily notes INDEX.

For every date in a range this does exactly what the command

    dofjson YYYY-MM-DD --endpoint notas

does: query ``api.get_notas(date, respaldo=respaldo)`` and save the result
as one JSON file per day (``<outdir>/YYYY/DDMMYYYY-notas.json``). It does
NOT download each note's content or scanned images: only the index.

Meant to be run as many times as needed: each run only downloads the missing
days. A registry of completed days is kept in ``<outdir>/.completados``, so
it resumes where it left off and retries the days that failed on network
errors. Days without an edition (holidays, weekends, old dates the service
does not have) are marked as completed. "Today" is never marked, since it can
still receive more notes later. The full range is ~40,000 days — a long
download, designed to be interrupted (Ctrl-C) and resumed at will.

Days SIDOF loses
----------------
SIDOF does not report a missing day as an error. It answers **200 OK with
every note list empty** — indistinguishable, on its face, from a Sunday. For a
handful of dates that answer is wrong: the gazette was published and SIDOF
simply does not have it (08-03-1999, whose edition carried a constitutional
reform, is one). Left alone, the archive would record those days as empty and
mark them done forever.

So an empty answer is not taken at face value. On a weekday — where a real
edition is expected and the confirmed losses all are — the day is put to the
DOF's own website, a separate system with a separate database, through
dofjson.api.get_notas() (see its own docstring for the ``respaldo`` policy
this module's ``respaldo`` parameter maps straight onto). If it has the day,
its index is stored instead; if it agrees the day is empty, that is now two
independent sources agreeing.

Every stored day carries a ``fuente`` key naming where it came from
(``"sidof"`` or ``"dof.gob.mx"``), and the registry records it alongside the
date, so the provenance of a downloaded archive can be audited afterwards.
"""

import datetime as dt
import json
import time
from pathlib import Path

import requests

from dofjson import api, dofweb, titulos

FECHA_INICIO_DEFAULT = dt.date(1917, 1, 2)


def iter_fechas(desde: dt.date, hasta: dt.date):
    dia = desde
    un_dia = dt.timedelta(days=1)
    while dia <= hasta:
        yield dia
        dia += un_dia


def _cargar_completados(path: Path) -> set:
    """Dates already done, from the registry.

    Entries are ``fecha`` or ``fecha<TAB>fuente``; registries written before
    provenance was recorded hold the bare date, so only the first field is
    read.
    """
    if not path.exists():
        return set()
    return {
        linea.split("\t")[0].strip()
        for linea in path.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    }


def _marcar_completado(path: Path, fecha: dt.date, completados: set, fuente: str) -> None:
    """Register a finished day together with where its data came from."""
    completados.add(fecha.isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{fecha.isoformat()}\t{fuente}\n")


def _guardar(notas: dict, fecha: dt.date, root: Path) -> Path:
    day_dir = root / f"{fecha:%Y}"
    day_dir.mkdir(parents=True, exist_ok=True)
    dest = day_dir / f"{fecha:%d%m%Y}-notas.json"
    dest.write_text(json.dumps(notas, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def procesar_dia(
    fecha: dt.date, root: Path, pausa: float, stats: dict, respaldo: str = "habiles",
    cache_dir: Path | None = titulos.SIN_CACHE_DIR,
) -> tuple[str, str]:
    """Download and save one day's notes index.

    Returns ``(resultado, fuente)``, where `resultado` is "completado" (mark
    done, do not repeat) or "reintentar" (transient error; retried on a future
    run), and `fuente` names where the day's data came from — for the registry.

    The actual SIDOF-then-dofweb decision is entirely api.get_notas()'s own
    (see its docstring) — this function is left with only what is specific to
    a long batch run: rate limiting (`pausa`) and turning a network failure
    into "reintentar" instead of raising. One pausa() covers the whole call,
    even on the rare day it makes two requests (SIDOF then dofweb) instead of
    one — the handful of such days a full archive run ever hits are not worth
    a second parameter just to pace them individually.

    `cache_dir`, passed straight to api.get_notas(), lets a day already
    published in the notas-archivo release be read off disk instead of hitting
    SIDOF/dofweb again — handy for rebuilding a local archive (e.g. on a fresh
    clone) without re-downloading history the release already has. Left
    unset, it defaults to `dofjson.titulos.CACHE_DIR` just like api.get_notas()
    does; pass `cache_dir=None` to force every day to be fetched live. A cache
    hit skips `pausa` too: it made no request, so there is nothing to be kind
    to the server about, and pacing a purely local read would only slow a
    from-cache rebuild down for no reason.
    """
    cache_dir_efectivo = titulos.CACHE_DIR if cache_dir is titulos.SIN_CACHE_DIR else cache_dir
    cache_hit = (
        cache_dir_efectivo is not None
        and titulos.nota_del_dia_en_cache(fecha, cache_dir_efectivo) is not None
    )
    try:
        notas = api.get_notas(fecha, respaldo=respaldo, cache_dir=cache_dir)
    except (requests.exceptions.RequestException, dofweb.PaginaDeOtroDia):
        # A network error, or a page served for the wrong date, is left to
        # retry instead of trusted or written off as empty: believing a
        # wrong-date page would file real notes under this day, and calling
        # a network hiccup "no edition" would bury the day for good.
        stats["dias_error"] += 1
        return "reintentar", ""
    if not cache_hit:
        time.sleep(pausa)

    if notas.get("fuente") == api.FUENTE_WEB:
        _guardar(notas, fecha, root)
        if api.cuenta_notas(notas):
            stats["dias_recuperados"] += 1
        else:
            # An edition exists but only as scanned images, so there is no
            # index to recover — the file records that it was published all
            # the same.
            stats["dias_solo_imagen"] += 1
        return "completado", api.FUENTE_WEB

    if api.tiene_notas(notas):
        _guardar(notas, fecha, root)
        stats["dias_con_indice"] += 1
        return "completado", api.FUENTE_SIDOF

    # SIDOF has nothing for this day, and either `respaldo` said not to
    # bother checking dofweb, or it did and dofweb agreed — a day with no
    # edition either way.
    stats["dias_sin_edicion"] += 1
    return "completado", "sin-edicion"


def download_archivo(
    desde: dt.date,
    hasta: dt.date,
    root: Path,
    pausa: float = 0.5,
    log=print,
    respaldo: str = "habiles",
    cache_dir: Path | None = titulos.SIN_CACHE_DIR,
) -> dict:
    """Download the notes index for every day in [desde, hasta] into root.

    Resumable: skips the days registered in ``root/.completados``. When SIDOF
    reports a day as empty, `respaldo` decides whether to check it against the
    DOF website first (see api.RESPALDO_OPCIONES). Returns the run's statistics.

    `cache_dir` names a directory already holding notas-archivo `.tgz` assets
    (see dofjson.titulos.download_dof_assets()): any day in range already
    published there is read straight off disk instead of hit
    against SIDOF/dofweb — useful for rebuilding this same archive elsewhere
    (a fresh clone, a new machine) without repeating the ~40,000 requests the
    release itself already paid for. Left unset, it defaults to
    `dofjson.titulos.CACHE_DIR`; pass `cache_dir=None` to always fetch live.
    """
    if respaldo not in api.RESPALDO_OPCIONES:
        raise ValueError(
            f"respaldo debe ser uno de {api.RESPALDO_OPCIONES}, no {respaldo!r}"
        )
    root.mkdir(parents=True, exist_ok=True)
    completados_path = root / ".completados"
    completados = _cargar_completados(completados_path)
    hoy = dt.date.today()

    stats = {
        "dias_procesados": 0, "dias_con_indice": 0,
        "dias_recuperados": 0, "dias_solo_imagen": 0,
        "dias_sin_edicion": 0, "dias_error": 0,
    }

    log(f"Descargando índices de notas del DOF: {desde} -> {hasta}  (destino: {root}/)")
    log(f"Días ya completados: {len(completados)}")
    log(f"Respaldo desde {api.FUENTE_WEB} cuando SIDOF no trae notas: {respaldo}\n")

    try:
        for fecha in iter_fechas(desde, hasta):
            if fecha.isoformat() in completados:
                continue
            antes = stats["dias_recuperados"]
            resultado, fuente = procesar_dia(fecha, root, pausa, stats, respaldo, cache_dir)
            stats["dias_procesados"] += 1
            if stats["dias_recuperados"] > antes:
                log(f"[{fecha}] SIDOF no la tiene; recuperada de {api.FUENTE_WEB}")
            # Never mark "today" as completed: it can receive more notes later.
            if resultado == "completado" and fecha < hoy:
                _marcar_completado(completados_path, fecha, completados, fuente)
            if stats["dias_procesados"] % 100 == 0:
                log(
                    f"[{fecha}] procesados={stats['dias_procesados']} "
                    f"con-índice={stats['dias_con_indice']} "
                    f"recuperados={stats['dias_recuperados']} "
                    f"sin-edición={stats['dias_sin_edicion']} errores={stats['dias_error']}"
                )
    except KeyboardInterrupt:
        log("\nInterrumpido. Vuelve a correr el comando para continuar.")

    log("\nResumen:")
    for k, v in stats.items():
        log(f"  {k}: {v}")
    if stats["dias_recuperados"] or stats["dias_solo_imagen"]:
        log(
            f"\n{stats['dias_recuperados'] + stats['dias_solo_imagen']} día(s) que SIDOF "
            f"da por vacíos sí se publicaron; se tomaron de {api.FUENTE_WEB} "
            f'(marcados con "fuente": "{api.FUENTE_WEB}").'
        )
    return stats
