"""Agrega los títulos del archivo local de notas del DOF (notas-archivo/).

Recorre los JSON diarios descargados por scripts/descargar_notas.py y produce
archivos CSV pequeños en website/datos/, que son los que consumen las páginas
del sitio Quarto.  El archivo crudo (notas-archivo/) nunca se versiona; estos
agregados sí, de modo que el sitio se puede renderizar sin los datos crudos.

Uso:
    python website/scripts/agrega_titulos.py [--archivo notas-archivo] [--salida website/datos]
"""

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

EDICIONES = {
    "NotasMatutinas": "matutina",
    "NotasVespertinas": "vespertina",
    "NotasExtraordinarias": "extraordinaria",
}

# Stopwords mínimas del español, más términos funcionales frecuentes en los
# títulos del DOF que no aportan al análisis léxico ("ref" es el folio
# administrativo "REF:NNNNNN" de las convocatorias y avisos).
STOPWORDS = set(
    """a al algo ante antes como con contra cual cuales cuando de del desde
    donde dos durante e el ella ellas ellos en entre era es esa ese eso esta
    estas este esto estos fue ha han hasta la las le les lo los mas más me mi
    mediante muy no nos o os otra otras otro otros para pero por que qué se
    ser si sin sobre son su sus tras un una unas uno unos y ya ref""".split()
)

PALABRA_RE = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)


def sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def primera_palabra(titulo: str) -> str:
    m = PALABRA_RE.search(titulo)
    return sin_acentos(m.group(0).upper()) if m else ""


def tokens(titulo: str):
    for m in PALABRA_RE.finditer(titulo.lower()):
        palabra = m.group(0)
        if len(palabra) > 2 and palabra not in STOPWORDS:
            yield sin_acentos(palabra)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archivo", default="notas-archivo", type=Path)
    parser.add_argument("--salida", default="website/datos", type=Path)
    parser.add_argument("--desde", default=1970, type=int)
    args = parser.parse_args()
    args.salida.mkdir(parents=True, exist_ok=True)

    notas_anio_ed = Counter()  # (año, edición) -> notas
    dias_con_notas = Counter()  # año -> días con al menos una nota
    long_chars = defaultdict(list)  # año -> longitudes en caracteres
    long_palabras = defaultdict(list)  # año -> longitudes en palabras
    primeras = Counter()  # (año, primera palabra) -> notas
    organismos = Counter()  # (año, organismo) -> notas
    terminos = Counter()  # (década, término) -> ocurrencias

    anios = sorted(
        p for p in args.archivo.iterdir() if p.is_dir() and int(p.name) >= args.desde
    )
    for dir_anio in anios:
        anio = int(dir_anio.name)
        for archivo in sorted(dir_anio.glob("*-notas.json")):
            with open(archivo) as fh:
                dia = json.load(fh)
            hubo_notas = False
            for clave, edicion in EDICIONES.items():
                for nota in dia.get(clave) or []:
                    titulo = (nota.get("titulo") or "").strip()
                    if not titulo:
                        continue
                    hubo_notas = True
                    notas_anio_ed[anio, edicion] += 1
                    long_chars[anio].append(len(titulo))
                    palabras = list(tokens(titulo))
                    long_palabras[anio].append(len(PALABRA_RE.findall(titulo)))
                    primeras[anio, primera_palabra(titulo)] += 1
                    organismo = (nota.get("nombreCodOrgaUno") or "").strip().upper()
                    organismos[anio, organismo or "(SIN ORGANISMO)"] += 1
                    decada = anio - anio % 10
                    for palabra in palabras:
                        terminos[decada, palabra] += 1
            if hubo_notas:
                dias_con_notas[anio] += 1
        print(f"{anio}: {sum(v for (a, _), v in notas_anio_ed.items() if a == anio):,} notas")

    def escribe(nombre: str, encabezado, filas):
        ruta = args.salida / nombre
        with open(ruta, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(encabezado)
            w.writerows(filas)
        print(f"-> {ruta}")

    escribe(
        "notas_por_anio.csv",
        ["anio", "edicion", "notas"],
        [(a, e, n) for (a, e), n in sorted(notas_anio_ed.items())],
    )
    escribe(
        "dias_con_notas.csv",
        ["anio", "dias"],
        sorted(dias_con_notas.items()),
    )
    escribe(
        "longitud_titulos.csv",
        ["anio", "notas", "chars_media", "chars_mediana", "palabras_media", "palabras_mediana"],
        [
            (
                a,
                len(long_chars[a]),
                round(mean(long_chars[a]), 2),
                median(long_chars[a]),
                round(mean(long_palabras[a]), 2),
                median(long_palabras[a]),
            )
            for a in sorted(long_chars)
        ],
    )

    # Primeras palabras: se conservan por año las 30 más frecuentes en el
    # total; el resto se acumula como OTRAS para mantener el CSV pequeño.
    total_primeras = Counter()
    for (_, palabra), n in primeras.items():
        total_primeras[palabra] += n
    top_primeras = {p for p, _ in total_primeras.most_common(30)}
    filas_primeras = Counter()
    for (anio, palabra), n in primeras.items():
        filas_primeras[anio, palabra if palabra in top_primeras else "OTRAS"] += n
    escribe(
        "primeras_palabras.csv",
        ["anio", "palabra", "notas"],
        [(a, p, n) for (a, p), n in sorted(filas_primeras.items())],
    )

    escribe(
        "organismos.csv",
        ["anio", "organismo", "notas"],
        [(a, o, n) for (a, o), n in sorted(organismos.items())],
    )

    filas_terminos = []
    for decada in sorted({d for d, _ in terminos}):
        top = Counter({t: n for (d, t), n in terminos.items() if d == decada})
        filas_terminos.extend((decada, t, n) for t, n in top.most_common(60))
    escribe("terminos_por_decada.csv", ["decada", "termino", "ocurrencias"], filas_terminos)


if __name__ == "__main__":
    main()
