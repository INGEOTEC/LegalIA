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
"""

import datetime as dt
import json
import time
from pathlib import Path

import requests

from dofjson import client

FECHA_INICIO_DEFAULT = dt.date(1917, 1, 2)


def iter_fechas(desde: dt.date, hasta: dt.date):
    dia = desde
    un_dia = dt.timedelta(days=1)
    while dia <= hasta:
        yield dia
        dia += un_dia


def _cargar_completados(path: Path) -> set:
    if not path.exists():
        return set()
    return {
        linea.strip()
        for linea in path.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    }


def _marcar_completado(path: Path, fecha: dt.date, completados: set) -> None:
    completados.add(fecha.isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(fecha.isoformat() + "\n")


def procesar_dia(fecha: dt.date, root: Path, pausa: float, stats: dict) -> str:
    """Download and save one day's notes index.

    Returns "completado" (mark done, do not repeat) or "reintentar"
    (transient error; will be retried on a future run).
    """
    try:
        notas = client.get_notas(fecha)
    except requests.exceptions.HTTPError as exc:
        # 404 = the service has no such day (typical for very old dates):
        # marked as done so it is not retried forever.
        if exc.response is not None and exc.response.status_code == 404:
            stats["dias_sin_edicion"] += 1
            return "completado"
        stats["dias_error"] += 1
        return "reintentar"
    except requests.exceptions.RequestException:
        stats["dias_error"] += 1
        return "reintentar"
    time.sleep(pausa)

    notas = client.quita_notas_sin_titulo(notas)
    day_dir = root / f"{fecha:%Y}"
    day_dir.mkdir(parents=True, exist_ok=True)
    dest = day_dir / f"{fecha:%d%m%Y}-notas.json"
    dest.write_text(json.dumps(notas, ensure_ascii=False, indent=2), encoding="utf-8")
    stats["dias_con_indice"] += 1
    return "completado"


def download_archivo(
    desde: dt.date, hasta: dt.date, root: Path, pausa: float = 0.5, log=print
) -> dict:
    """Download the notes index for every day in [desde, hasta] into root.

    Resumable: skips the days registered in ``root/.completados``. Returns
    the run's statistics.
    """
    root.mkdir(parents=True, exist_ok=True)
    completados_path = root / ".completados"
    completados = _cargar_completados(completados_path)
    hoy = dt.date.today()

    stats = {
        "dias_procesados": 0, "dias_con_indice": 0,
        "dias_sin_edicion": 0, "dias_error": 0,
    }

    log(f"Descargando índices de notas del DOF: {desde} -> {hasta}  (destino: {root}/)")
    log(f"Días ya completados: {len(completados)}\n")

    try:
        for fecha in iter_fechas(desde, hasta):
            if fecha.isoformat() in completados:
                continue
            resultado = procesar_dia(fecha, root, pausa, stats)
            stats["dias_procesados"] += 1
            # Never mark "today" as completed: it can receive more notes later.
            if resultado == "completado" and fecha < hoy:
                _marcar_completado(completados_path, fecha, completados)
            if stats["dias_procesados"] % 100 == 0:
                log(
                    f"[{fecha}] procesados={stats['dias_procesados']} "
                    f"con-índice={stats['dias_con_indice']} "
                    f"sin-edición={stats['dias_sin_edicion']} errores={stats['dias_error']}"
                )
    except KeyboardInterrupt:
        log("\nInterrumpido. Vuelve a correr el comando para continuar.")

    log("\nResumen:")
    for k, v in stats.items():
        log(f"  {k}: {v}")
    return stats
