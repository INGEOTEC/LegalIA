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
  lag, not a title mismatch, so no override is correct for it (see
  `nota2md.scjn`'s own "issue #124 (follow-up)" section).
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
And a large instrumento's own crawl -- confirmed live against the CPEUM, 301
rows across 31 grid pages -- used to go completely silent for as long as
that took, indistinguishable from a hung process; `descarga_ordenamiento`'s
`on_progreso` callback now narrates it, one line per grid page and per row.

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
from datetime import date
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md.scjn import (  # noqa: E402
    descarga_ordenamiento,
    instrument_up_to_date,
    nueva_sesion,
    search_name,
    slug_instrumento,
)

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
    """`descarga_ordenamiento`'s own `on_progreso` callback (issue #140,
    Causa 2): indented under its instrumento's own `[coleccion i/N]` line so
    it reads as a sub-step, not another instrumento."""
    print(f"    {mensaje}", file=sys.stderr)


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
            f"./scripts/extract_scjn_titles.py --outdir {outdir} --coleccion {coleccion}",
            file=sys.stderr,
        )
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
    saltados = 0
    for i, entrada in enumerate(instrumentos, 1):
        if i <= inicio:
            continue
        slug = slug_instrumento(entrada)
        if reintenta is not None and slug not in reintenta:
            continue
        nombre = entrada["nombre"]
        destino = outdir / coleccion / slug
        if reintenta is None and instrument_up_to_date(destino, entrada.get("actualizado"), fecha_corpus):
            saltados += 1
        else:
            buscado = search_name(entrada)
            if reintenta is not None:
                for archivo in destino.glob("*.md"):
                    archivo.unlink()
                (destino / "indice.json").unlink(missing_ok=True)
            etiqueta = nombre if buscado == nombre else f"{nombre} (buscado como {buscado!r})"
            print(f"[{coleccion} {i}/{len(instrumentos)}] {etiqueta}", file=sys.stderr)
            sesion = nueva_sesion()
            try:
                # Issue #140, Causa 2: a large instrumento (the CPEUM's 301
                # rows across 31 grid pages is the confirmed case) otherwise
                # goes silent for as long as its own crawl takes --
                # indistinguishable from a hung process.
                escritos = descarga_ordenamiento(
                    sesion, buscado, destino, espera=espera, on_progreso=_imprime_avance
                )
            except Exception as exc:
                print(f"  aviso: {buscado!r} fallo: {exc}", file=sys.stderr)
            else:
                if not escritos:
                    print(f"  aviso: sin resultados en la SCJN para {buscado!r}", file=sys.stderr)
            time.sleep(espera)
        if reintenta is None:
            _guarda_progreso(outdir, coleccion, i)
    if saltados:
        print(
            f"  {coleccion}: {saltados} instrumento(s) skipped -- already up to date, "
            "SCJN not touched (Mecanismo 2, issue #124)",
            file=sys.stderr,
        )
    if reintenta is None:
        _archivo_progreso(outdir, coleccion).unlink(missing_ok=True)
        _guarda_fecha_rastreo_completo(outdir, coleccion, date.today().isoformat())


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
    args = p.parse_args(argv)

    reintenta = set(args.reintenta) if args.reintenta else None
    for coleccion in args.coleccion or COLECCIONES:
        rastrea_coleccion(
            coleccion, args.outdir, args.espera, reiniciar=args.reiniciar, reintenta=reintenta
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
