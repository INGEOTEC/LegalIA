#!/usr/bin/env python3
"""Match every SCJN snapshot `fetch_scjn_legislacion.py` already downloaded
to the `codNota` of the DOF note that published it — Fase 2 of the crawl plan
in issue #105.

For each instrument `download_legal_provisions_provenance_ids(coleccion)`
knows about, its own `historial` (the `codNota` of its reforms, oldest first)
is paired by publication date against the snapshots already sitting under
``<outdir>/<coleccion>/<abrev-o-nombre>/`` (see
`nota2md.scjn.versiones_de_directorio`), using a dofjson titles dataset
(`dofjson.download_legal_provisions_titles`) for the fecha of every candidate
codNota — see `nota2md.scjn.enlaza_historial` for how ties and misses are
resolved. Writes one ``indice.json`` per instrument directory, listing each
snapshot's own file, `fecha_publicacion`, and the `codNota` matched to it
(``null`` when no match was found).

Each entry also carries an independent cross-check of that link (issue #126,
`nota2md.scjn.cross_check_by_title_mention`): every same-day codNota whose
own title explicitly names the instrument (`title_candidates`), and how that
compares to the historial-based link (`title_check_status` — "confirmed",
"disagreement", "revealed", "ambiguous" or "none"). A "disagreement" — the
historial-based link and the sole title-mentioning candidate point to
different codNota — is also printed as a warning, since it is exactly the
class of case issue #115 asked to be reviewable by hand.

With ``--content-diff``, each entry also gets a content-diff confirmation
(issue #127, `nota2md.scjn.confirm_by_content_diff`): the candidate codNota
(among `codNota` and `title_candidates`) whose own DOF text best accounts
for what actually changed between this snapshot and the previous one
(``content_diff_confirmed_codNota``, ``content_diff_score``) — only for
candidates with digital DOF text; this fetches each candidate's own note
via `dofjson` (caching it under ``--cache-notas``, on top of the titles
dataset this script already needs), so it is opt-in rather than the
default, since it can add a lot of extra network calls to a full-catalogue
run.

Needs a titles dataset already built:

    python -c "from nota2md import download_legal_provisions_titles as d; d('titulos.jsonl.gz')"
    ./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion --titulos titulos.jsonl.gz
    ./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion --titulos titulos.jsonl.gz \
        --content-diff --cache-notas scjn-legislacion/_notas_cache

Only instrument directories `fetch_scjn_legislacion.py` already crawled are
touched; a coleccion+instrumento pair with no directory yet is skipped
silently — nothing to match until that instrument has been crawled.
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

from nota2md import download_legal_provisions_provenance_ids
from nota2md.builder import fetch_nota, legal_provisions
from nota2md.scjn import (
    agrupa_candidatos_por_fecha,
    confirm_by_content_diff,
    cross_check_by_title_mention,
    enlaza_historial,
    lee_cabecera,
    slug_instrumento,
    versiones_de_directorio,
)

COLECCIONES = ("leyes", "reglamentos", "tratados")


def carga_porf(titulos: Path) -> dict:
    """Every dofjson title record in `titulos` (a gzipped JSONL from
    `dofjson.download_legal_provisions_titles`), grouped by `fecha` — the
    same shape `leyesmx.dof.notas_por_fecha` builds, reused here as
    `enlaza_historial`'s own `porf` argument."""
    porf: dict[str, list] = {}
    with gzip.open(titulos, "rt", encoding="utf-8") as f:
        for linea in f:
            nota = json.loads(linea)
            porf.setdefault(nota["fecha"], []).append(nota)
    return porf


def _confianza(archivo: Path) -> dict:
    """The `ratio_similitud`/`sospechoso` fields `nota2md.scjn._cabecera`
    writes into a snapshot's own header (issue #115), read back into
    `indice.json` so a packaging step can quarantine `sospechoso` entries
    without re-reading every snapshot file itself. Both come back `None`
    for a snapshot a crawl wrote before issue #115 added them — an older
    corpus is not re-crawled just to backfill this; `audita_scjn_legislacion.py`
    recomputes the ratio offline for exactly that case."""
    campos = lee_cabecera(archivo)
    ratio = campos.get("ratio_similitud")
    return {
        "ratio_similitud": float(ratio) if ratio is not None else None,
        "sospechoso": (campos.get("sospechoso") == "true") if "sospechoso" in campos else None,
    }


def _texto_html(cod_nota: int, cache_dir: Path, cache: dict) -> str | None:
    """`cod_nota`'s own DOF Markdown via the HTML path, or None when it has
    no `cadenaContenido` at all — issue #127's content-diff confirmation is
    only possible for a codNota with digital text; one without it is
    skipped here, never OCR'd just for one more confidence signal. Cached
    both in `cache` (in-memory, for this run) and under `cache_dir` (on
    disk, across runs), since one codNota can be shared by several laws'
    reforms."""
    if cod_nota in cache:
        return cache[cod_nota]
    ruta = cache_dir / f"nota-{cod_nota}.md"
    if ruta.exists():
        texto = ruta.read_text(encoding="utf-8")
        cache[cod_nota] = texto
        return texto
    nota = fetch_nota(cod_nota)
    if not nota.get("cadenaContenido"):
        cache[cod_nota] = None
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    ruta = legal_provisions(cod_nota, cache_dir, source="html", nota=nota)
    texto = ruta.read_text(encoding="utf-8")
    cache[cod_nota] = texto
    return texto


def _confirmaciones_por_contenido(
    versiones, enlazadas, verificaciones, cache_dir: Path, cache_notas: dict
):
    """One `nota2md.scjn.ContentDiffConfirmation` per `versiones` entry
    (issue #127) — the candidate codNota set per date is the caller's own
    union of #124/#126's signals (`nota2md.scjn.agrupa_candidatos_por_fecha`),
    fetched lazily and cached via `_texto_html`."""
    candidatos_por_fecha = agrupa_candidatos_por_fecha(enlazadas, verificaciones)

    todos_los_candidatos = sorted({c for cs in candidatos_por_fecha.values() for c in cs})
    markdown_por_codNota = {}
    for cod in todos_los_candidatos:
        texto = _texto_html(cod, cache_dir, cache_notas)
        if texto is not None:
            markdown_por_codNota[cod] = texto

    return confirm_by_content_diff(versiones, candidatos_por_fecha, markdown_por_codNota)


def enlaza_coleccion(
    coleccion: str,
    outdir: Path,
    porf: dict,
    *,
    cache_notas_dir: Path | None = None,
    cache_notas: dict | None = None,
) -> None:
    instrumentos = download_legal_provisions_provenance_ids(coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    for i, entrada in enumerate(instrumentos, 1):
        destino = outdir / coleccion / slug_instrumento(entrada)
        if not destino.is_dir():
            continue
        versiones = versiones_de_directorio(destino)
        if not versiones:
            continue
        enlazadas = enlaza_historial(versiones, entrada["historial"], porf)
        verificaciones = cross_check_by_title_mention(enlazadas, entrada["nombre"], porf)

        confirmaciones = None
        if cache_notas_dir is not None:
            confirmaciones = _confirmaciones_por_contenido(
                versiones, enlazadas, verificaciones, cache_notas_dir, cache_notas
            )

        indice = []
        for idx, (v, chk) in enumerate(zip(enlazadas, verificaciones)):
            entrada_indice = {
                "archivo": v.archivo.name,
                "fecha_publicacion": v.fecha_publicacion,
                "codNota": v.codNota,
                **_confianza(v.archivo),
                "title_candidates": chk.title_candidates,
                "title_check_status": chk.status,
            }
            if confirmaciones is not None:
                entrada_indice["content_diff_confirmed_codNota"] = (
                    confirmaciones[idx].confirmed_codNota
                )
                entrada_indice["content_diff_score"] = confirmaciones[idx].score
            indice.append(entrada_indice)
        (destino / "indice.json").write_text(
            json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        enlazados = sum(1 for v in enlazadas if v.codNota is not None)
        print(
            f"[{coleccion} {i}/{len(instrumentos)}] {entrada['nombre']}: "
            f"{enlazados}/{len(enlazadas)} enlazadas",
            file=sys.stderr,
        )
        for chk in verificaciones:
            if chk.status == "disagreement":
                print(
                    f"  aviso: {entrada['nombre']!r} {chk.fecha_publicacion}: "
                    f"enlaza_historial dice codNota={chk.historial_match}, pero el "
                    f"titulo de codNota={chk.title_candidates[0]} tambien menciona "
                    "el nombre explicitamente — revisar a mano (issue #115/#126)",
                    file=sys.stderr,
                )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="donde fetch_scjn_legislacion.py ya escribio cada coleccion",
    )
    p.add_argument(
        "--titulos", type=Path, required=True,
        help="dataset de dofjson.download_legal_provisions_titles (gzip JSONL)",
    )
    p.add_argument(
        "--coleccion", choices=COLECCIONES, action="append",
        help="repetible; por defecto las tres",
    )
    p.add_argument(
        "--content-diff", action="store_true",
        help=(
            "ademas del enlace por historial y por titulo, confirma por diff de "
            "contenido (issue #127) — descarga y cachea el texto DOF de cada "
            "candidato via dofjson, asi que agrega llamadas de red; requiere "
            "--cache-notas"
        ),
    )
    p.add_argument(
        "--cache-notas", type=Path,
        help="donde cachear el Markdown DOF ya descargado de cada codNota candidata "
             "(solo con --content-diff)",
    )
    args = p.parse_args(argv)

    if args.content_diff and not args.cache_notas:
        p.error("--content-diff requiere --cache-notas")

    porf = carga_porf(args.titulos)
    cache_notas: dict = {}
    for coleccion in args.coleccion or COLECCIONES:
        enlaza_coleccion(
            coleccion, args.outdir, porf,
            cache_notas_dir=args.cache_notas if args.content_diff else None,
            cache_notas=cache_notas,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
