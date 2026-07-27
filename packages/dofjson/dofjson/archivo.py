"""Incremental, resumable download of the DOF daily notes INDEX.

For every date in a range this does exactly what the command

    dofjson YYYY-MM-DD --endpoint notas

does: query ``get_notas(date)``, drop title-less stub entries with
``quita_notas_sin_titulo``, and save the result as one JSON file per day
(``<outdir>/YYYY/DDMMYYYY-notas.json``). It does NOT download each note's
content or scanned images: only the index.

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
edition is expected and the confirmed losses all are — the day is put to
``dofweb``, the DOF's own website, which is a separate system with a separate
database. If it has the day, its index is stored instead; if it agrees the
day is empty, that is now two independent sources agreeing.

Every stored day carries a ``fuente`` key naming where it came from
(``"sidof"`` or ``"dof.gob.mx"``), and the registry records it alongside the
date, so the provenance of a downloaded archive can be audited afterwards.
"""

import datetime as dt
import json
import time
from pathlib import Path

import requests

from dofjson import client, dofweb

FECHA_INICIO_DEFAULT = dt.date(1917, 1, 2)

FUENTE_SIDOF = "sidof"

#: When to fall back to the DOF website after SIDOF reports an empty day.
#: ``habiles`` (the default) asks only on Mon-Fri, where an edition is
#: expected — that is a few hundred extra requests over the full range, and
#: covers every confirmed loss. ``todos`` also asks on weekends, which do very
#: occasionally carry an extraordinary edition, at the cost of roughly 10,000
#: more requests. ``nunca`` restores the SIDOF-only behaviour.
RESPALDO_OPCIONES = ("habiles", "todos", "nunca")


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


_LISTAS_NOTAS = ("NotasMatutinas", "NotasVespertinas", "NotasExtraordinarias")


def tiene_notas(notas: dict) -> bool:
    """Whether a get_notas() response carries any note at all."""
    return any(notas.get(clave) for clave in _LISTAS_NOTAS)


def consultar_respaldo(fecha: dt.date, respaldo: str) -> bool:
    """Whether an empty SIDOF answer for this date is worth double-checking."""
    if respaldo == "nunca":
        return False
    if respaldo == "todos":
        return True
    return fecha.weekday() < 5


def _guardar(notas: dict, fecha: dt.date, root: Path) -> Path:
    day_dir = root / f"{fecha:%Y}"
    day_dir.mkdir(parents=True, exist_ok=True)
    dest = day_dir / f"{fecha:%d%m%Y}-notas.json"
    dest.write_text(json.dumps(notas, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def procesar_dia(
    fecha: dt.date, root: Path, pausa: float, stats: dict, respaldo: str = "habiles"
) -> tuple[str, str]:
    """Download and save one day's notes index.

    Returns ``(resultado, fuente)``, where `resultado` is "completado" (mark
    done, do not repeat) or "reintentar" (transient error; retried on a future
    run), and `fuente` names where the day's data came from — for the registry.
    """
    try:
        notas = client.get_notas(fecha)
    except requests.exceptions.HTTPError as exc:
        # 404 = the service has no such day. Rare: SIDOF answers 200-with-no-
        # notes instead, which is handled below. Treated the same way.
        if exc.response is not None and exc.response.status_code == 404:
            notas = None
        else:
            stats["dias_error"] += 1
            return "reintentar", ""
    except requests.exceptions.RequestException:
        stats["dias_error"] += 1
        return "reintentar", ""
    time.sleep(pausa)

    if notas is not None:
        notas = client.quita_notas_sin_titulo(notas)
    if notas is not None and tiene_notas(notas):
        notas["fuente"] = FUENTE_SIDOF
        _guardar(notas, fecha, root)
        stats["dias_con_indice"] += 1
        return "completado", FUENTE_SIDOF

    # SIDOF has nothing for this day. That is the normal answer for a Sunday
    # and the wrong answer for a day it lost, and the two look identical from
    # here — so ask the other source before believing it.
    if not consultar_respaldo(fecha, respaldo):
        stats["dias_sin_edicion"] += 1
        return "completado", "sin-edicion"

    try:
        alterno = dofweb.get_notas(fecha)
    except requests.exceptions.RequestException:
        stats["dias_error"] += 1
        return "reintentar", ""
    time.sleep(pausa)

    if not dofweb.hay_publicacion(alterno):
        # Both sources agree the gazette did not come out.
        stats["dias_sin_edicion"] += 1
        return "completado", "sin-edicion"

    _guardar(alterno, fecha, root)
    if dofweb.cuenta_notas(alterno):
        stats["dias_recuperados"] += 1
    else:
        # An edition exists but only as scanned images, so there is no index
        # to recover — the file records that it was published all the same.
        stats["dias_solo_imagen"] += 1
    return "completado", dofweb.FUENTE


def download_archivo(
    desde: dt.date,
    hasta: dt.date,
    root: Path,
    pausa: float = 0.5,
    log=print,
    respaldo: str = "habiles",
) -> dict:
    """Download the notes index for every day in [desde, hasta] into root.

    Resumable: skips the days registered in ``root/.completados``. When SIDOF
    reports a day as empty, `respaldo` decides whether to check it against the
    DOF website first (see RESPALDO_OPCIONES). Returns the run's statistics.
    """
    if respaldo not in RESPALDO_OPCIONES:
        raise ValueError(
            f"respaldo debe ser uno de {RESPALDO_OPCIONES}, no {respaldo!r}"
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
    log(f"Respaldo desde {dofweb.BASE_URL} cuando SIDOF no trae notas: {respaldo}\n")

    try:
        for fecha in iter_fechas(desde, hasta):
            if fecha.isoformat() in completados:
                continue
            antes = stats["dias_recuperados"]
            resultado, fuente = procesar_dia(fecha, root, pausa, stats, respaldo)
            stats["dias_procesados"] += 1
            if stats["dias_recuperados"] > antes:
                log(f"[{fecha}] SIDOF no la tiene; recuperada de {dofweb.FUENTE}")
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
            f"da por vacíos sí se publicaron; se tomaron de {dofweb.FUENTE} "
            f'(marcados con "fuente": "{dofweb.FUENTE}").'
        )
    return stats
