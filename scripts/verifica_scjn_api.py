#!/usr/bin/env python3
"""Reconcile a crawled SCJN collection against the reform count the SCJN
itself reports for each instrument — the check issue #178 added after `lfd`
turned out to hold 92 snapshots against the 98 reforms its own detail page
(``/consulta/detalle-buscador?...&idOrd=693``) lists.

For every instrument with an `estado.json` carrying an `id_ordenamiento`,
this asks `/api/SCOW/Reforma` for its reform table and checks that each row
either has a snapshot on disk or is marked `tieneArticulos=false` (the API
saying it holds no consolidated text for that row — mostly FE DE ERRATAS and
ACLARACION). Anything else is a shortfall, reported per instrument.

``--baja`` also fetches the missing snapshots, so a run that finds a
shortfall can close it in place instead of re-crawling the collection.

Usage:

    python scripts/verifica_scjn_api.py --outdir scripts/scjn/leyes-api2 [--baja]
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "nota2md"))

from nota2md.scjn_api import (  # noqa: E402
    ScjnApi,
    ScjnApiError,
    snapshot,
    Ordenamiento,
)

_FECHA = re.compile(r"^(\d{2}-\d{2}-\d{4})(?:-(\d+))?$")


def nombres_esperados(reformas) -> dict[str, object]:
    """The file name each reform row must have, in the SCJN's own row order —
    the same `-2`/`-3` numbering `descarga_ordenamiento` applies to rows
    sharing a `fecha_publicacion`."""
    repeticiones: dict[str, int] = {}
    esperados = {}
    for reforma in reformas:
        repeticiones[reforma.fecha_publicacion] = (
            repeticiones.get(reforma.fecha_publicacion, 0) + 1
        )
        orden = repeticiones[reforma.fecha_publicacion]
        sufijo = "" if orden == 1 else f"-{orden}"
        esperados[f"{reforma.fecha_publicacion}{sufijo}.md"] = reforma
    return esperados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", type=Path, required=True, help="el directorio de la coleccion")
    ap.add_argument("--espera", type=float, default=0.5)
    ap.add_argument("--baja", action="store_true", help="descarga los snapshots que falten")
    ap.add_argument("--instrumento", action="append", metavar="SLUG")
    args = ap.parse_args()

    api = ScjnApi(espera=args.espera)
    seleccion = set(args.instrumento) if args.instrumento else None

    total_reformas = total_snapshots = total_sin_texto = 0
    descuadres = []
    bajados = 0
    directorios = sorted(p for p in args.outdir.iterdir() if p.is_dir())
    for directorio in directorios:
        slug = directorio.name
        if seleccion is not None and slug not in seleccion:
            continue
        estado_archivo = directorio / "estado.json"
        if not estado_archivo.exists():
            continue
        id_ordenamiento = json.loads(estado_archivo.read_text()).get("id_ordenamiento")
        if not id_ordenamiento:
            print(f"[{slug}] sin id_ordenamiento en estado.json — se omite", file=sys.stderr)
            continue
        try:
            reformas = api.reformas_of_ordenamiento(id_ordenamiento)
        except ScjnApiError as exc:
            print(f"[{slug}] no se pudo leer su tabla de reformas: {exc}", file=sys.stderr)
            descuadres.append((slug, 0, 0, 0, [f"Reforma: {exc}"]))
            continue

        esperados = nombres_esperados(reformas)
        en_disco = {p.name for p in directorio.glob("*.md")}
        sin_texto = [n for n, r in esperados.items() if not r.tieneArticulos]
        faltan = [
            n for n, r in esperados.items() if r.tieneArticulos and n not in en_disco
        ]
        sobran = sorted(en_disco - set(esperados))

        total_reformas += len(reformas)
        total_snapshots += len(en_disco)
        total_sin_texto += len(sin_texto)

        if args.baja and faltan:
            nombre = json.loads(estado_archivo.read_text()).get("nombre_buscado") or slug
            elegido = Ordenamiento(
                idOrdenamiento=str(id_ordenamiento),
                ordenamiento=nombre,
                ratio=1.0,
                sospechoso=False,
            )
            todavia = []
            for archivo in faltan:
                reforma = esperados[archivo]
                try:
                    articulos = api.articulos_of_reforma(id_ordenamiento, reforma.reformaId)
                except ScjnApiError as exc:
                    todavia.append(f"{archivo} (reformaId {reforma.reformaId}): {exc}")
                    continue
                (directorio / archivo).write_text(
                    snapshot(elegido, reforma, articulos, nombre), encoding="utf-8"
                )
                bajados += 1
                print(f"[{slug}] bajado {archivo} ({len(articulos)} articulos)", file=sys.stderr)
            faltan = todavia

        if faltan or sobran:
            descuadres.append(
                (slug, len(reformas), len(en_disco), len(sin_texto), faltan + [f"+{s}" for s in sobran])
            )

    print(
        f"\n{len(directorios)} instrumento(s): {total_reformas} reforma(s) reportada(s) "
        f"por la SCJN, {total_snapshots} snapshot(s) en disco, "
        f"{total_sin_texto} reforma(s) sin texto consolidado (tieneArticulos=false)"
    )
    if bajados:
        print(f"{bajados} snapshot(s) bajado(s) en esta corrida")
    if not descuadres:
        print("TODO CUADRA: cada reforma tiene su snapshot o esta marcada sin texto consolidado")
        return 0
    print(f"\n{len(descuadres)} instrumento(s) NO cuadran:")
    for slug, reformas, disco, sin_texto, detalle in descuadres:
        print(f"  {slug}: reformas={reformas} snapshots={disco} sin_texto={sin_texto}")
        for d in detalle[:10]:
            print(f"     {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
