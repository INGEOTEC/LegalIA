#!/usr/bin/env python3
"""Crawl the SCJN Buscador for every ley, reglamento and tratado that
`extract_scjn_titles.py`'s own catalogue already knows about, and save each
one's reform-dated snapshots as Markdown under
``<outdir>/<coleccion>/<abrev-o-nombre>/<fecha_publicacion>.md`` — Fase 1 of
the crawl plan in issue #105.

Only these three collections: issue #105's Fase 0 spike found that the SCJN
does not catalogue NOM technical standards as ordenamientos of their own at
all (8 real NOM codes, zero hits, even searching "Norma Oficial Mexicana"
generically), so "normas" has nothing here to crawl.

Resumable at two levels. Within one instrumento, a file already on disk is
left alone and its row's download skipped (see
nota2md.scjn.descarga_ordenamiento), so a later re-run picking up new
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
crawl saved, the wrong document entirely — `elige_candidato` gained guards
against this, but the wrong snapshots already on disk are still there,
untouched, since the per-file skip above has no notion of a snapshot being
*wrong*. ``--reintenta SLUG`` (repeatable, `nota2md.scjn.slug_instrumento`)
re-bajas exactly the named instrumentos: their existing snapshots (and
stale `indice.json`) are deleted first so they are genuinely re-fetched
against the fixed `elige_candidato`, while every instrumento not named is
skipped without touching the SCJN at all — a full collection is ~600
instrumentos, and there is no need to re-walk the other ~595 that were never
wrong just to verify a fix aimed at 5. Leaves the collection's own
``.progreso.json`` checkpoint untouched either way. Rate-limited:
`--espera` seconds between requests, since this is an unofficial site with
no public API (the same posture as leyesmx.diputados/dofjson.dofweb
elsewhere in this repo) — a single ley or reglamento search-then-detail-
then-per-row walk cannot be parallelized across instruments either, since
the SCJN scopes a detail page's own URL to the session that requested it.

Needs each requested collection's own ``catalogo.json`` already written by
``extract_scjn_titles.py`` under ``<outdir>/<coleccion>/`` (issue #123: this
never calls `download_legal_provisions_provenance_ids` itself, so Diputados'
`historial` never reaches it).

    ./scripts/extract_scjn_titles.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --coleccion tratados
    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --coleccion leyes \
        --reintenta ccf --reintenta lisr --reintenta lsint --reintenta lfd --reintenta lopgjdf

Needs the "scjn" extra installed (``pip install packages/nota2md[scjn]``) for
python-docx.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md.scjn import descarga_ordenamiento, nueva_sesion, slug_instrumento  # noqa: E402

COLECCIONES = ("leyes", "reglamentos", "tratados")


def _load_catalog(outdir: Path, coleccion: str) -> list[dict]:
    """The `nombre`(+`abrev`) catalogue `extract_scjn_titles.py` already
    wrote for `coleccion` -- Diputados' `historial` never reaches this
    script (issue #123)."""
    archivo = outdir / coleccion / "catalogo.json"
    if not archivo.is_file():
        raise SystemExit(
            f"{archivo} no existe -- corre primero "
            f"./scripts/extract_scjn_titles.py --outdir {outdir} --coleccion {coleccion}"
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
    `descarga_ordenamiento`'s own file-level skip already makes cheap."""
    try:
        return json.loads(_archivo_progreso(outdir, coleccion).read_text(encoding="utf-8"))["indice"]
    except (OSError, json.JSONDecodeError, KeyError):
        return 0


def _guarda_progreso(outdir: Path, coleccion: str, indice: int) -> None:
    archivo = _archivo_progreso(outdir, coleccion)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps({"indice": indice}), encoding="utf-8")


def rastrea_coleccion(
    coleccion: str,
    outdir: Path,
    espera: float,
    *,
    reiniciar: bool = False,
    reintenta: set[str] | None = None,
) -> None:
    instrumentos = _load_catalog(outdir, coleccion)
    print(f"{coleccion}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    if reintenta is not None:
        print(
            f"  --reintenta: solo se re-bajaran {sorted(reintenta)}; el resto se "
            "asume ya bajado y no se toca la SCJN",
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
    for i, entrada in enumerate(instrumentos, 1):
        if i <= inicio:
            continue
        slug = slug_instrumento(entrada)
        if reintenta is not None and slug not in reintenta:
            continue
        nombre = entrada["nombre"]
        destino = outdir / coleccion / slug
        if reintenta is not None:
            for archivo in destino.glob("*.md"):
                archivo.unlink()
            (destino / "indice.json").unlink(missing_ok=True)
        print(f"[{coleccion} {i}/{len(instrumentos)}] {nombre}", file=sys.stderr)
        sesion = nueva_sesion()
        try:
            escritos = descarga_ordenamiento(sesion, nombre, destino, espera=espera)
        except Exception as exc:
            print(f"  aviso: {nombre!r} fallo: {exc}", file=sys.stderr)
        else:
            if not escritos:
                print(f"  aviso: sin resultados en la SCJN para {nombre!r}", file=sys.stderr)
        if reintenta is None:
            _guarda_progreso(outdir, coleccion, i)
        time.sleep(espera)
    if reintenta is None:
        _archivo_progreso(outdir, coleccion).unlink(missing_ok=True)


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
        help="ignora el progreso guardado de una corrida anterior y rastrea la coleccion desde el principio",
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
    args = p.parse_args(argv)

    reintenta = set(args.reintenta) if args.reintenta else None
    for coleccion in args.coleccion or COLECCIONES:
        rastrea_coleccion(
            coleccion, args.outdir, args.espera, reiniciar=args.reiniciar, reintenta=reintenta
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
