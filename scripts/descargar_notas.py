#!/usr/bin/env python3
"""Descarga incremental del ÍNDICE de notas del DOF, día por día, desde una
fecha inicial (por defecto, el 2 de enero de 1917) hasta hoy.

Para cada fecha hace exactamente lo que el comando

    dofjson AAAA-MM-DD --endpoint notas

es decir: consulta `get_notas(fecha)`, descarta las notas sin título con
`quita_notas_sin_titulo`, y guarda el resultado como un JSON por día. No baja
el contenido de cada nota ni las imágenes escaneadas: sólo el índice.

Pensado para correrse cuantas veces se quiera: en cada corrida sólo baja los
días que faltan. Lleva un registro de los días ya completados en
`<outdir>/.completados`, así que reanuda donde se quedó y reintenta los días
que fallaron por red. Los días sin edición (feriados, fines de semana, fechas
antiguas sin datos en el servicio) se marcan como completados.

Salida (un archivo por día, mismo nombre que produce el comando `dofjson`):

    <outdir>/AAAA/DDMMAAAA-notas.json

Uso:
    python scripts/descargar_notas.py                 # 1917-01-02 -> hoy
    python scripts/descargar_notas.py --desde 1980-01-01 --hasta 1980-12-31
    python scripts/descargar_notas.py --pausa 1.0     # más lento/amable

Aviso: el rango completo son ~40 000 días; es una descarga larga. Sirve
justamente por ser reanudable — puedes interrumpir (Ctrl-C) y seguir luego.
"""
import argparse
import datetime as dt
import json
import sys
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


def cargar_completados(path: Path) -> set:
    if not path.exists():
        return set()
    return {
        linea.strip()
        for linea in path.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    }


def marcar_completado(path: Path, fecha: dt.date, completados: set) -> None:
    completados.add(fecha.isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(fecha.isoformat() + "\n")


def procesar_dia(fecha: dt.date, root: Path, pausa: float, stats: dict) -> str:
    """Baja y guarda el índice de notas de un día, igual que
    `dofjson AAAA-MM-DD --endpoint notas`. Devuelve 'completado' (marcar y no
    repetir) o 'reintentar' (error transitorio; se reintenta luego)."""
    try:
        notas = client.get_notas(fecha)
    except requests.exceptions.HTTPError as exc:
        # 404 = el servicio no tiene ese día (típico en fechas muy antiguas):
        # se da por completado para no reintentarlo por siempre.
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--desde", default=FECHA_INICIO_DEFAULT.isoformat(),
        help=f"Fecha inicial AAAA-MM-DD (por defecto {FECHA_INICIO_DEFAULT.isoformat()})",
    )
    parser.add_argument(
        "--hasta", default=None,
        help="Fecha final AAAA-MM-DD (por defecto: hoy)",
    )
    parser.add_argument(
        "--outdir", default="notas-archivo",
        help="Directorio local donde guardar los índices (por defecto: notas-archivo/)",
    )
    parser.add_argument(
        "--pausa", type=float, default=0.5,
        help="Segundos de espera entre peticiones al servidor (por defecto: 0.5)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        desde = dt.date.fromisoformat(args.desde)
        hasta = dt.date.fromisoformat(args.hasta) if args.hasta else dt.date.today()
    except ValueError as exc:
        sys.exit(f"Fecha inválida: {exc}. Usa el formato AAAA-MM-DD.")
    if desde > hasta:
        sys.exit(f"--desde ({desde}) no puede ser posterior a --hasta ({hasta}).")

    root = Path(args.outdir)
    root.mkdir(parents=True, exist_ok=True)
    completados_path = root / ".completados"
    completados = cargar_completados(completados_path)
    hoy = dt.date.today()

    stats = {
        "dias_procesados": 0, "dias_con_indice": 0,
        "dias_sin_edicion": 0, "dias_error": 0,
    }

    print(f"Descargando índices de notas del DOF: {desde} -> {hasta}  (destino: {root}/)")
    print(f"Días ya completados: {len(completados)}\n")

    try:
        for fecha in iter_fechas(desde, hasta):
            if fecha.isoformat() in completados:
                continue
            resultado = procesar_dia(fecha, root, args.pausa, stats)
            stats["dias_procesados"] += 1
            # No marcar "hoy" como completado: puede recibir más notas más tarde.
            if resultado == "completado" and fecha < hoy:
                marcar_completado(completados_path, fecha, completados)
            if stats["dias_procesados"] % 100 == 0:
                print(
                    f"[{fecha}] procesados={stats['dias_procesados']} "
                    f"con-índice={stats['dias_con_indice']} "
                    f"sin-edición={stats['dias_sin_edicion']} errores={stats['dias_error']}"
                )
    except KeyboardInterrupt:
        print("\nInterrumpido. Vuelve a correr el script para continuar.")

    print("\nResumen:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
