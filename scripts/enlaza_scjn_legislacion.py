#!/usr/bin/env python3
"""Match every SCJN snapshot `fetch_scjn_legislacion.py` already downloaded
to the `codNota` of the DOF note that published it — Fase 2 of the crawl plan
in issue #105.

Federal laws are the only collection left (issue #189): reglamentos, tratados
and Normas Oficiales Mexicanas went out of scope with the Cámara de Diputados
data they leaned on, so ``leyes`` is written here as a literal path segment
rather than parameterized. That is a decision, not a leftover — a second
collection would have to justify itself again before it got a flag back.

For each instrument this workspace already tracks (issue #210: one directory
per law under ``<outdir>/leyes/``, its own `estado.json`), only its `nombre`
is used to find, for every snapshot already sitting under
``<outdir>/leyes/<abrev-o-nombre>/`` (see
`scjn.header.versiones_de_directorio`), which same-day DOF note (the dofjson
titles stream, `dofjson.legal_provisions_titles`) explicitly names
the instrument in its own title (issue #126,
`nota2md.linking.title_candidates_por_fecha`) — or, when no same-day title names
it at all, whichever same-day note opens with "DECRETO"/"LEY"
(`nota2md.linking._title_opens_with_decreto_or_ley`), since a reform's own title
does not always spell out every law it amends. See
`nota2md.linking.enlaza_por_titulo` for how same-day ties and misses are
resolved. Every entry also gets a content-diff confirmation (issue #127,
`nota2md.linking.confirm_by_content_diff`): the candidate codNota (among that
date's `title_candidates`) whose own DOF text best accounts for what
actually changed between this snapshot and the previous one
(``content_diff_confirmed_codNota``, ``content_diff_score``) — only for
candidates with digital DOF text, fetched via `dofjson` and saved under
``<outdir>/leyes/<abrev-o-nombre>/notas`` — each instrument's own
subdirectory, alongside its `indice.json`, not a directory shared across
instruments. Those notes are kept, not scratch: issue #128 ships them inside
each instrument's own tarball, so the DOF text every link was decided against
travels with the snapshots it was compared to. This always runs — a codNota link is either
title-confirmed or it is not; there is no reason to ever settle for less
than the strongest signal available.

Writes one ``indice.json`` per instrument directory, listing each
snapshot's own file, `fecha_publicacion`, the `codNota` matched to it
(``null`` when no match was found), every same-day candidate that named the
instrument (`title_candidates`), how that pool was resolved
(`title_link_status` — "linked", "content_diff", "none", "claimed" or
"ambiguous", see `nota2md.linking.title_link_status` and `resolve_links`), and
its content-diff confirmation.

Since issue #187 the content-diff confirmation *is* the link when title
matching alone could not pick a winner, instead of only annotating a
`codNota: null`: the candidate still had to name the instrument in its own
title, still had to clear `UMBRAL_CONFIRMACION_DIFF`, and still cannot take
a codNota another snapshot already claimed — see `resolve_links` for why
that does not weaken issue #115's "an absent link is worth more than a wrong
one". A date left `ambiguous` after that — several same-day candidates and
no content confirmation among them — is printed as a warning, since it is
exactly the class of case issue #115 asked to be reviewable by hand.

Needs at least one law already crawled under ``<outdir>/leyes/``
(`fetch_scjn_legislacion.py`) and the notas-archivo cache populated (the DOF
titles are streamed from it):

    ./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion --instrumento lft
    nota2md download gazette-metadata
    ./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion

Only instrument directories `fetch_scjn_legislacion.py` already crawled are
touched; an instrumento with no directory yet is skipped
silently — nothing to match until that instrument has been crawled.

``--instrumento SLUG`` (repeatable, issue #148) narrows the run to the named
instruments, so a single law refreshed by ``fetch_scjn_legislacion.py
--instrumento`` can have its own `indice.json` and missing `notas/`
regenerated without re-walking the whole collection. Each instrument it
links records the date in its own `estado.json`.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "nota2md"))
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from dofjson.titulos import SIN_CACHE_DIR, legal_provisions_titles  # noqa: E402
from nota2md.builder import fetch_nota, get_document  # noqa: E402
from nota2md.linking import (  # noqa: E402
    ESTADO_ENLACE_CONTENT_DIFF,
    confirm_by_content_diff,
    enlaza_por_titulo,
    resolve_links,
    title_candidates_por_fecha,
)
from scjn.catalog import slug_instrumento  # noqa: E402
from scjn.header import lee_cabecera, versiones_de_directorio  # noqa: E402
from scjn.release import AssetNotCached, download_scjn_leyes_index  # noqa: E402
from scjn.state import escribe_estado, lee_estado  # noqa: E402

#: The one collection left (issue #189), a literal path segment.
COLECCION = "leyes"


def _resolver_cache_dir(valor: str | None):
    """--cache-dir's value, resolved as dofjson's own CLI resolves it: not
    given -> SIN_CACHE_DIR (dofjson.titulos.CACHE_DIR); 'none' -> None
    (into memory); anything else -> that path."""
    if valor is None:
        return SIN_CACHE_DIR
    if valor.lower() == "none":
        return None
    return Path(valor)


def _load_catalog(outdir: Path) -> list[dict]:
    """Every law this workspace already tracks -- one entry per subdirectory
    of ``<outdir>/leyes/``, in place of the retired `catalogo.json` (issue
    #210). See `fetch_scjn_legislacion._load_catalog`, whose docstring this
    mirrors: `abrev`/`nombre` come from each law's own `estado.json`, falling
    back to `indice-global.json.gz` for `nombre` when that predates issue
    #210."""
    base = outdir / COLECCION
    if not base.is_dir():
        raise SystemExit(
            f"{base} no existe -- corre primero "
            f"./scripts/fetch_scjn_legislacion.py --outdir {outdir} --instrumento <slug>"
        )
    try:
        instrumentos = download_scjn_leyes_index()["instrumentos"]
    except AssetNotCached:
        instrumentos = {}

    catalogo = []
    for directorio in sorted(p for p in base.iterdir() if p.is_dir()):
        slug = directorio.name
        estado = lee_estado(directorio)
        nombre = estado.get("nombre") or instrumentos.get(slug, {}).get("nombre")
        if nombre is None:
            raise SystemExit(f"{directorio}: sin 'nombre' (ni en su estado.json ni en el indice)")
        entrada = {"abrev": estado.get("abrev") or slug, "nombre": nombre}
        if estado.get("nombre_scjn"):
            entrada["nombre_scjn"] = estado["nombre_scjn"]
        catalogo.append(entrada)
    return catalogo


def carga_porf(titulos) -> dict:
    """Every dofjson title record in the `titulos` iterable (issue #166: the
    stream `dofjson.legal_provisions_titles` yields, no longer a file), grouped
    by `fecha` — `title_candidates_por_fecha`'s own `porf` argument."""
    porf: dict[str, list] = {}
    for nota in titulos:
        porf.setdefault(nota["fecha"], []).append(nota)
    return porf


def _confianza(archivo: Path) -> dict:
    """The `ratio_similitud`/`sospechoso` fields `scjn.api.cabecera`
    writes into a snapshot's own header (issue #115), read back into
    `indice.json` so a packaging step can quarantine `sospechoso` entries
    without re-reading every snapshot file itself. Both come back `None`
    for a snapshot a crawl wrote before issue #115 added them — an older
    corpus is not re-crawled just to backfill this; only `--reintenta`
    (`fetch_scjn_legislacion.py`) does, by re-downloading the instrument."""
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
    skipped here, never OCR'd just for one more confidence signal.

    `cache_dir` is the *current* instrument's own directory, and `cache` is
    shared across the whole run (in-memory only, keyed by `cod_nota`) — since
    one codNota can be shared by several laws' reforms, a later instrument
    reusing an already-fetched codNota still gets its own on-disk copy under
    its own `cache_dir` (so every note a content diff for that instrument
    actually reasoned about sits next to that instrument's own `indice.json`),
    even though the network fetch itself only ever happens once."""
    ruta = cache_dir / f"nota-{cod_nota}.md"
    if cod_nota in cache:
        texto = cache[cod_nota]
        if texto is not None and not ruta.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            ruta.write_text(texto, encoding="utf-8")
        return texto
    if ruta.exists():
        texto = ruta.read_text(encoding="utf-8")
        cache[cod_nota] = texto
        return texto
    # get_document() is the package's one note-to-Markdown step (issue #170):
    # the record comes back with cadenaContenido already converted, so this is
    # both the "has digital text?" check and the conversion itself, with no
    # second fetch and no html_to_markdown call spelled out here.
    documento = get_document(nota=fetch_nota(cod_nota))
    if not documento.get("cadenaContenido"):
        cache[cod_nota] = None
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    texto = documento["cadenaContenido"] + "\n"
    ruta.write_text(texto, encoding="utf-8")
    cache[cod_nota] = texto
    return texto


def _confirmaciones_por_contenido(
    versiones, candidatos_por_fecha: dict, directorio_notas: Path, cache_notas: dict
):
    """One `nota2md.linking.ContentDiffConfirmation` per `versiones` entry
    (issue #127), against `candidatos_por_fecha`
    (`nota2md.linking.title_candidates_por_fecha`'s own output — the same pool
    `enlaza_por_titulo` used), fetched lazily and cached via `_texto_html`."""
    todos_los_candidatos = sorted({c for cs in candidatos_por_fecha.values() for c in cs})
    markdown_por_codNota = {}
    for cod in todos_los_candidatos:
        texto = _texto_html(cod, directorio_notas, cache_notas)
        if texto is not None:
            markdown_por_codNota[cod] = texto

    return confirm_by_content_diff(versiones, candidatos_por_fecha, markdown_por_codNota)


def enlaza_coleccion(
    outdir: Path,
    porf: dict,
    cache_notas: dict,
    instrumento: set[str] | None = None,
) -> None:
    """Rebuild every instrument's `indice.json`, or — when `instrumento` names
    slugs (issue #148) — only those, so one refreshed law can be re-linked
    without walking the other ~314."""
    instrumentos = _load_catalog(outdir)
    if instrumento is not None:
        faltantes = instrumento - {slug_instrumento(e) for e in instrumentos}
        if faltantes:
            raise SystemExit(f"{sorted(faltantes)} no esta(n) en el catalogo de {COLECCION}")
        instrumentos = [e for e in instrumentos if slug_instrumento(e) in instrumento]
    print(f"{COLECCION}: {len(instrumentos)} instrumento(s)", file=sys.stderr)
    for i, entrada in enumerate(instrumentos, 1):
        destino = outdir / COLECCION / slug_instrumento(entrada)
        if not destino.is_dir():
            continue
        versiones = versiones_de_directorio(destino)
        if not versiones:
            continue
        candidatos_por_fecha = title_candidates_por_fecha(
            (v.fecha_publicacion for v in versiones), entrada["nombre"], porf
        )
        enlazadas = enlaza_por_titulo(versiones, candidatos_por_fecha)
        confirmaciones = _confirmaciones_por_contenido(
            versiones, candidatos_por_fecha, destino / "notas", cache_notas
        )

        # Issue #187: the link is the title match when there is one and the
        # content-diff confirmation otherwise, rather than the title match
        # alone with the confirmation left as an annotation nobody reads.
        resueltos = resolve_links(enlazadas, confirmaciones, candidatos_por_fecha)

        indice = []
        for idx, v in enumerate(enlazadas):
            candidatos_dia = candidatos_por_fecha.get(v.fecha_publicacion, [])
            cod, estado = resueltos[idx]
            indice.append(
                {
                    "archivo": v.archivo.name,
                    "fecha_publicacion": v.fecha_publicacion,
                    "codNota": cod,
                    **_confianza(v.archivo),
                    "title_candidates": candidatos_dia,
                    "title_link_status": estado,
                    "content_diff_confirmed_codNota": confirmaciones[idx].confirmed_codNota,
                    "content_diff_score": confirmaciones[idx].score,
                }
            )
        (destino / "indice.json").write_text(
            json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Issue #148: the `enlazado` half of this instrument's own
        # estado.json -- `fetch_scjn_legislacion.py` owns `actualizado` and
        # `rastreado`, and `escribe_estado` merges rather than overwrites so
        # neither erases the other's record.
        escribe_estado(destino, enlazado=date.today().isoformat())
        enlazados = sum(1 for cod, _ in resueltos if cod is not None)
        por_diff = sum(1 for _, estado in resueltos if estado == ESTADO_ENLACE_CONTENT_DIFF)
        print(
            f"[{COLECCION} {i}/{len(instrumentos)}] {entrada['nombre']}: "
            f"{enlazados}/{len(enlazadas)} enlazadas"
            + (f" ({por_diff} por diff de contenido)" if por_diff else ""),
            file=sys.stderr,
        )
        for v, (cod, _) in zip(enlazadas, resueltos):
            candidatos_dia = candidatos_por_fecha.get(v.fecha_publicacion, [])
            if cod is None and len(candidatos_dia) > 1:
                print(
                    f"  aviso: {entrada['nombre']!r} {v.fecha_publicacion}: "
                    f"varios codNota mencionan el nombre ese dia ({candidatos_dia}) — "
                    "ninguno se enlaza por titulo, y el diff de contenido tampoco "
                    "confirmo uno: revisar a mano (issue #115/#126/#127/#187)",
                    file=sys.stderr,
                )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="donde fetch_scjn_legislacion.py ya escribio la coleccion leyes",
    )
    p.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="directorio con los assets .tgz de notas-archivo de donde se leen "
             "los titulos del DOF (poblalo con `nota2md download gazette-metadata`); "
             "sin valor: dofjson.titulos.CACHE_DIR; 'none': a memoria",
    )
    p.add_argument(
        "--instrumento", action="append", metavar="SLUG",
        help=(
            "repetible; slug_instrumento a re-enlazar, dejando el resto de la coleccion "
            "intacto (issue #148)"
        ),
    )
    args = p.parse_args(argv)

    porf = carga_porf(
        legal_provisions_titles(_resolver_cache_dir(args.cache_dir), log=lambda *_: None)
    )
    cache_notas: dict = {}
    instrumento = set(args.instrumento) if args.instrumento else None
    enlaza_coleccion(args.outdir, porf, cache_notas, instrumento=instrumento)
    return 0


if __name__ == "__main__":
    sys.exit(main())
