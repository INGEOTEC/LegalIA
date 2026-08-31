"""Fase 0 of issue #172 (issue #173): probe the SCJN's new SCOW JSON API
against the corpus the WebForms crawler already wrote to disk.

Throwaway by design — it writes no production code path and imports from
`nota2md.scjn` only the helpers whose behaviour it wants to keep measuring
against (`ratio_similitud`, `lee_cabecera`). It answers, with numbers, the
six questions issue #173 lists; the findings themselves go as a comment on
issue #172, which is the deliverable.

Usage:

    python scripts/spike_scjn_api.py --corpus scripts/scjn/leyes \
        --out /tmp/spike.json [--sample ccf,lisr,...]
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "nota2md"))

from nota2md.leyes import normaliza_para_comparar  # noqa: E402
from nota2md.scjn import lee_cabecera, ratio_similitud  # noqa: E402

BASE = "https://legislacion.scjn.gob.mx/SCOW-API"
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

# The five instruments issue #115 audited by hand (the old search returned a
# different document), plus the volume case, the #124 override case and the
# trigger case; the rest of the sample is filled in from the corpus at random.
SAMPLE_CORE = [
    "ccf",
    "lisr",
    "lsint",
    "lfd",
    "lopgjdf",
    "cpeum",
    "lisipl",
    "lfca",
]

_EM = re.compile(r"</?em>")
_FECHA_ARCHIVO = re.compile(r"^(\d{2}-\d{2}-\d{4})")
_N_DE_E = re.compile(r"N\.?\s*DE\.?\s*\.?\s*E\.?\b", re.I)
_REFERENCIA_CONOCIDA = re.compile(
    r"^(ENCABEZADO|ART[IÍ]CULO\b|TRANSITORIO|T[IÍ]TULO\b|CAP[IÍ]TULO\b|SECCI[OÓ]N\b|LIBRO\b|"
    r"AP[EÉ]NDICE\b|ANEXO\b|PROEMIO|DENOMINACI[OÓ]N)",
    re.I,
)


def _similitud(a: str, b: str) -> float:
    """Word-multiset overlap on `normaliza_para_comparar`'d text — a real
    number over texts of thousands of articles, where difflib's ratio is
    quadratic and its quick_ratio only an upper bound."""
    ca, cb = Counter(normaliza_para_comparar(a).split()), Counter(normaliza_para_comparar(b).split())
    total = sum(ca.values()) + sum(cb.values())
    return 2 * sum((ca & cb).values()) / total if total else 1.0


def _get(path: str, params: dict, espera: float) -> dict:
    for intento in range(4):
        r = requests.get(f"{BASE}{path}", params=params, headers=HEADERS, timeout=90)
        if r.status_code == 200:
            time.sleep(espera)
            try:
                return r.json()
            except ValueError:
                raise RuntimeError(f"non-JSON answer (WAF challenge?) for {path}")
        time.sleep(espera * (2**intento))
    raise RuntimeError(f"{path} -> HTTP {r.status_code}")


def busqueda_frase(nombre: str, espera: float) -> dict:
    """The full ConsultaRequest body: the API answers HTTP 500, not a
    validation error, when the optional filter strings are simply left out."""
    body = {
        "q": nombre,
        "tipoBusqueda": 1,
        "tipoPublicacion": 1,
        "ambitoF": "",
        "categoriaF": "",
        "vigenciaF": "",
        "entidadFederativaF": "",
        "materiaF": "",
        "municipioF": "",
        "fechaPublicacionInicio": "",
        "fechaPublicacionFin": "",
        "numeroPagina": 1,
        "tamanioPagina": 25,
        "consultaArticulos": 0,
    }
    for intento in range(4):
        r = requests.post(
            f"{BASE}/api/SCOW/BusquedaFrase", json=body, headers=HEADERS, timeout=90
        )
        if r.status_code == 200:
            time.sleep(espera)
            return r.json()
        time.sleep(espera * (2**intento))
    raise RuntimeError(f"BusquedaFrase -> HTTP {r.status_code}")


def reformas(id_ordenamiento: str, espera: float, tamanio: int = 100) -> list[dict]:
    filas, pagina = [], 1
    while True:
        d = _get(
            "/api/SCOW/Reforma",
            {
                "idOrdenamiento": id_ordenamiento,
                "numeroPagina": pagina,
                "tamanioPagina": tamanio,
            },
            espera,
        )
        lote = d.get("reformas") or d.get("resultados") or []
        filas += lote
        total = d.get("tamanio") or 0
        if not lote or len(filas) >= total:
            return filas
        pagina += 1


def articulos(id_ordenamiento: str, id_reforma, espera: float, tamanio: int = 200) -> list[dict]:
    filas, pagina = [], 1
    while True:
        d = _get(
            "/api/SCOW/Articulos",
            {
                "idOrdenamiento": id_ordenamiento,
                "idReforma": id_reforma,
                "numeroPagina": pagina,
                "tamanioPagina": tamanio,
            },
            espera,
        )
        lote = d.get("articulos") or d.get("resultados") or []
        filas += lote
        total = d.get("tamanio") or 0
        if not lote or len(filas) >= total:
            return filas
        pagina += 1


def _fecha_api(cadena: str) -> str:
    """`22/05/2026 00:00:00` -> `22-05-2026`, the on-disk file name."""
    return (cadena or "").split(" ")[0].replace("/", "-")


def _cuerpo(archivo: Path) -> str:
    texto = archivo.read_text(encoding="utf-8")
    partes = texto.split("---", 2)
    return partes[2] if len(partes) == 3 else texto


def analiza(slug: str, entrada: dict, directorio: Path, espera: float) -> dict:
    nombre = entrada.get("nombre_scjn") or entrada["nombre"]
    out = {"slug": slug, "nombre_buscado": nombre}
    d = busqueda_frase(nombre, espera)
    out["codigo"] = d.get("codigo")
    candidatos = [
        {
            "idOrdenamiento": c.get("idOrdenamiento"),
            "ordenamiento": _EM.sub("", c.get("ordenamiento") or ""),
            "iweight": c.get("iweight"),
            "vigencia": c.get("vigencia"),
            "ambito": c.get("ambito"),
            "categoriaOrdenamiento": c.get("categoriaOrdenamiento"),
            "materia": c.get("materia"),
            "fechaPublicado": c.get("fechaPublicado"),
        }
        for c in (d.get("resultados") or [])
    ]
    for c in candidatos:
        c["ratio"] = round(ratio_similitud(c["ordenamiento"], nombre), 3)
    out["n_candidatos"] = len(candidatos)
    out["candidatos"] = candidatos[:8]
    if not candidatos:
        return out

    federales = [c for c in candidatos if c["ambito"] == "FEDERAL"] or candidatos
    elegido = max(federales, key=lambda c: c["ratio"])
    out["elegido"] = elegido
    # ¿coincide con el título que el crawler viejo guardó en disco?
    en_disco = sorted(p for p in directorio.glob("*.md"))
    if en_disco:
        out["ordenamiento_en_disco"] = lee_cabecera(en_disco[0]).get("ordenamiento")

    filas = reformas(elegido["idOrdenamiento"], espera)
    out["n_reformas_api"] = len(filas)
    fechas_api = [_fecha_api(f.get("fechaPublicacion")) for f in filas]
    out["fechas_api_unicas"] = len(set(fechas_api))
    # `<fecha>-2.md` is the 2nd row published the same day, not another date.
    base_disco = {m.group(1) for m in (_FECHA_ARCHIVO.match(p.stem) for p in en_disco) if m}
    out["n_archivos_disco"] = len(en_disco)
    out["faltantes_en_api"] = sorted(base_disco - set(fechas_api))
    out["sobrantes_en_api"] = sorted(set(fechas_api) - base_disco)

    # Q6: fechaExpedicion / seccionPublicacion contra lo que `_cabecera` escribe.
    coincide_exp = distintos_exp = 0
    for f in filas:
        fp = _fecha_api(f.get("fechaPublicacion"))
        archivo = directorio / f"{fp}.md"
        if not archivo.exists():
            continue
        cab = lee_cabecera(archivo)
        fe_api = _fecha_api(f.get("fechaExpedicion") or "")
        if cab.get("fecha_expedicion") == fe_api:
            coincide_exp += 1
        elif cab.get("fecha_expedicion"):
            distintos_exp += 1
    out["fecha_expedicion_coincide"] = coincide_exp
    out["fecha_expedicion_distinta"] = distintos_exp
    out["seccion_publicacion_ejemplo"] = next(
        (f.get("seccionPublicacion") for f in filas if f.get("seccionPublicacion")), None
    )

    # Q2/Q3/Q4: texto, notas editoriales y estructura, sobre hasta 3 reformas.
    muestras = []
    comparables = [f for f in filas if (directorio / f"{_fecha_api(f.get('fechaPublicacion'))}.md").exists()]
    for f in (comparables[:2] + comparables[-1:])[:3]:
        fp = _fecha_api(f.get("fechaPublicacion"))
        arts = articulos(elegido["idOrdenamiento"], f.get("reformaId"), espera)
        texto_api = "\n\n".join((a.get("contenido") or "") for a in arts)
        texto_disco = _cuerpo(directorio / f"{fp}.md")
        sim = _similitud(texto_disco, texto_api)
        refs = [(a.get("referencia") or "").strip() for a in arts]
        ordenes = [a.get("orden") for a in arts if a.get("orden") is not None]
        muestras.append(
            {
                "fecha": fp,
                "reformaId": f.get("reformaId"),
                "n_articulos": len(arts),
                "similitud": round(sim, 3),
                "n_de_e_api": len(_N_DE_E.findall(texto_api)),
                "n_de_e_disco": len(_N_DE_E.findall(texto_disco)),
                "referencia_vacia": sum(1 for r in refs if not r),
                "referencia_no_reconocida": sum(
                    1 for r in refs if r and not _REFERENCIA_CONOCIDA.match(r)
                ),
                "orden_contiguo": bool(ordenes)
                and sorted(ordenes) == list(range(min(ordenes), min(ordenes) + len(ordenes))),
                "referencias_muestra": refs[:6],
                "referencias_no_reconocidas": sorted(
                    {r for r in refs if r and not _REFERENCIA_CONOCIDA.match(r)}
                )[:8],
            }
        )
    out["muestras"] = muestras
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=Path("scripts/scjn/leyes"))
    ap.add_argument("--out", type=Path, default=Path("spike_scjn_api.json"))
    ap.add_argument("--sample", default="")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--espera", type=float, default=0.5)
    args = ap.parse_args()

    catalogo = {e["abrev"]: e for e in json.loads((args.corpus / "catalogo.json").read_text())}
    if args.sample:
        slugs = args.sample.split(",")
    else:
        resto = [s for s in sorted(catalogo) if s not in SAMPLE_CORE]
        paso = max(1, len(resto) // max(1, args.n - len(SAMPLE_CORE)))
        slugs = SAMPLE_CORE + resto[::paso][: args.n - len(SAMPLE_CORE)]

    resultados = []
    for slug in slugs:
        entrada = catalogo.get(slug)
        if entrada is None or not (args.corpus / slug).is_dir():
            print(f"[skip] {slug}", file=sys.stderr)
            continue
        print(f"[{slug}]", file=sys.stderr, flush=True)
        try:
            resultados.append(analiza(slug, entrada, args.corpus / slug, args.espera))
        except Exception as exc:  # el spike reporta el fallo, no se detiene
            print(f"[error] {slug}: {exc}", file=sys.stderr)
            resultados.append({"slug": slug, "error": str(exc)})
        args.out.write_text(json.dumps(resultados, ensure_ascii=False, indent=1))
    print(f"escrito {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
