#!/usr/bin/env python3
"""Package an id-keyed SCJN corpus (`reglamentos`, issue #220; `lineamientos`,
issue #222) into one byte-reproducible tarball *per instrument with text*,
plus a human-readable manifest and a checksum file. Shared by both
collections (issue #222's Fase 0, generalized out of the `reglamentos`-only
`empaqueta_scjn_reglamentos.py`), without `indice.json`/`notas/` or a
`codNota` section: neither collection has DOF linking at all (issue #220's
own Scope, unchanged by #222), so every ``<id_ordenamiento>.tgz`` ships only
``<id_ordenamiento>/<fecha>.md`` and ``<id_ordenamiento>/estado.json``.

## Manual publish only — never automated

Same rule as `scjn-leyes` (issue #115, Hallazgo C): nothing this script
produces is ever published automatically, now or in the future. The
packaging step (this script) and the publish step (a person running `gh`
by hand) are deliberately kept apart.

`scjn-reglamentos` outgrew a single GitHub release on 2026-09-10 (issue
#223): a release holds at most 1000 assets, and a release body at most
125 000 characters. So this script does not print a fixed publish recipe --
it *plans* one, against the partition it actually wrote
(``<destino>/partes.json``, issue #223's decision 4: an asset already
published never moves to a different part, since a split across parts is
arbitrary and re-deriving it would disagree with reality for already
published assets):

    # Defaults: --outdir scripts/scjn, --destino scripts/scjn/<coleccion>-release
    ./scripts/empaqueta_scjn_coleccion.py --coleccion reglamentos
    less scripts/scjn/reglamentos-release/MANIFEST.md   # read it. all of it.
    less scripts/scjn/reglamentos-release/PUBLICAR.md   # then this, verbatim.

`PUBLICAR.md` is generated fresh every run, from `partes.json` and the
manifest -- the one place the exact `gh release create`/`upload`/`edit`
sequence lives.

## What goes in each tarball

    <id_ordenamiento>/<fecha>.md      the snapshots, with their provenance header
    <id_ordenamiento>/estado.json     when it was crawled, and its own
                                       categoria_ordenamiento/vigencia/materia/resumen

No `abrev` (neither collection has one, issue #220's own decision 3: the
SCJN reissues an instrument as a brand-new `idOrdenamiento` rather than as a
reform of the previous one, so a title-derived key would collide), no
`indice.json`, no `notas/` -- nothing here depends on a DOF link that does
not exist for either collection yet.

## An instrument with no consolidated text ships no tarball (decision 6)

Every row of some instruments' reform table has `tieneArticulos=False`: the
SCJN classifies and crawls them, but serves no text at all (5 of
`reglamentos`' 1087, 37 of `lineamientos`' 163). Their own `estado.json`
still carries `"rastreado"` -- the crawl ran, it is not pending -- so this
script tells that apart from "never crawled" (seeded but not yet swept by
`fetch_scjn_legislacion.py`) purely from `rastreado`, never from the absence
of a `.md` file alone. A text-less-but-crawled instrument is listed in
`indice-global.json.gz` with `snapshots: 0` and no `asset` key (nothing to
download), and in `MANIFEST.md` under its own heading, separate from "Nunca
rastreados" -- issue #222's decision 6, applied to both id-keyed
collections identically, since a per-collection difference here is exactly
the kind of behaviour flag the shared path must not grow.

## Updating one instrument

``--instrumento ID`` (repeatable, by `id_ordenamiento`) rewrites only the
named instruments' tarballs and leaves every other `.tgz` already in
`--destino` exactly as it is -- the same incremental contract
`empaqueta_scjn_leyes.py --instrumento` has.

Needs at least one instrument already seeded under ``<outdir>/<coleccion>/``
(`discover_federal_reglamentos.py`/`discover_federal_lineamientos.py` +
`seed_federal_reglamentos.py --coleccion <coleccion>`).
"""

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.cache import COLECCIONES_POR_ID  # noqa: E402
from scjn.catalog import instrumento_key  # noqa: E402
from scjn.header import versiones_de_directorio  # noqa: E402
from scjn.release import (  # noqa: E402
    ASSET_INDICE_GLOBAL,
    CAMPO_CATEGORIA_ORDENAMIENTO,
    CAMPOS_METADATOS,
    LIMITE_ASSETS_POR_RELEASE,
    _tag_de_parte,
    construye_indice_global_lineamientos,
    construye_indice_global_reglamentos,
)
from scjn.state import lee_estado  # noqa: E402

#: Each id-keyed collection's own index builder -- keyed by
#: `Coleccion.nombre`, same shape as `scjn.cli`'s own `_DESCARGAS_POR_ID`. A
#: fourth id-keyed collection adds one entry here, never a new branch.
_INDICES_POR_ID = {
    "reglamentos": construye_indice_global_reglamentos,
    "lineamientos": construye_indice_global_lineamientos,
}

#: GitHub's release-body cap (issue #223) -- `MANIFEST.md` grows with the
#: corpus and blew past it at 1082 instruments (179 749 bytes), so the
#: generated `RELEASE_NOTES*.md` files are asserted under this instead.
LIMITE_CUERPO_NOTAS = 125_000

#: Part 1 alone also carries `MANIFEST.md`, `SHA256SUMS.txt` and
#: `ASSET_INDICE_GLOBAL` (decision 5), so its own tarball budget is
#: `LIMITE_ASSETS_POR_RELEASE` minus these three -- 997 today for
#: `reglamentos`, plenty of headroom for `lineamientos`' ~126.
_RESERVADOS_PARTE_1 = 3

#: `partes.json`'s own file name, local to `--destino` -- never a published
#: asset (decision 4: recomputing it from a sorted/hashed rule would
#: disagree with the arbitrary split already live for already published
#: assets).
ARCHIVO_PARTES = "partes.json"


def _load_catalog(outdir: Path, coleccion: str) -> list[dict]:
    """Every instrument already seeded under ``<outdir>/<coleccion>/`` --
    each one's own `estado.json`, whole. Unlike `empaqueta_scjn_leyes.py`,
    there is no published index to fall back to for `nombre`: this release
    does not exist until this very script's first run packages it."""
    base = outdir / coleccion
    if not base.is_dir():
        raise SystemExit(
            f"{base} no existe -- corre primero discover_federal_{coleccion}.py y "
            f"seed_federal_reglamentos.py --coleccion {coleccion}"
        )
    catalogo = []
    for directorio in sorted(p for p in base.iterdir() if p.is_dir()):
        estado = lee_estado(directorio)
        if not (estado.get("id_ordenamiento") and estado.get("nombre")):
            raise SystemExit(f"{directorio}: sin 'id_ordenamiento'/'nombre' en su estado.json")
        catalogo.append(estado)
    return catalogo


@dataclass
class ResumenInstrumento:
    """One instrument's own row in the manifest -- the shared sibling of
    `empaqueta_scjn_leyes.ResumenInstrumento`, without a linking percentage:
    neither id-keyed collection has one to report (issue #220's own Scope).

    `total_snapshots == 0` (and `asset is None`) is decision 6's text-less
    case: the instrument was crawled (its own `estado.json` has
    `rastreado`), but the SCJN serves no consolidated text for it, so there
    is nothing to tar."""

    id_ordenamiento: str
    nombre: str
    total_snapshots: int
    asset: str | None = None
    bytes_comprimidos: int = 0
    #: This instrument's own `estado.json` -- `categoria_ordenamiento`,
    #: `vigencia`, `materia`, `resumen`, `rastreado`, whatever it has.
    estado: dict | None = None
    #: Whether this run rewrote the instrument's own tarball. False for the
    #: ones a `--instrumento` run left untouched and only re-listed, and for
    #: a text-less instrument, which never had one to rewrite.
    reempaquetado: bool = True


def resume_coleccion(
    outdir: Path, coleccion: str
) -> tuple[list[ResumenInstrumento], list[str]]:
    """Every `coleccion` instrument in the catalogue, summarized for the
    manifest, plus the names of the ones truly never crawled at all -- the
    shared sibling of `empaqueta_scjn_leyes.resume_coleccion`.

    An instrument with no snapshot on disk is *not* automatically "never
    crawled" (issue #222's decision 6): its own `estado.json` says so via
    `rastreado`, set unconditionally by `fetch_scjn_legislacion.py`'s
    `rastrea_por_id` the moment its crawl actually ran, text or not. One
    with `rastreado` set is returned in the main list with
    `total_snapshots=0` and `asset=None` -- indexed and manifested, just
    with nothing to download -- while one with no `rastreado` at all (seeded
    but not yet swept) is returned separately, exactly as before."""
    instrumentos = _load_catalog(outdir, coleccion)
    resumenes = []
    nunca_rastreados = []
    for estado in instrumentos:
        clave = instrumento_key(estado)
        destino = outdir / coleccion / clave
        versiones = versiones_de_directorio(destino) if destino.is_dir() else []
        if not versiones:
            if not estado.get("rastreado"):
                nunca_rastreados.append(estado["nombre"])
                continue
            resumenes.append(
                ResumenInstrumento(
                    id_ordenamiento=clave, nombre=estado["nombre"],
                    total_snapshots=0, asset=None, estado=estado, reempaquetado=False,
                )
            )
            continue
        resumenes.append(
            ResumenInstrumento(
                id_ordenamiento=clave, nombre=estado["nombre"],
                total_snapshots=len(versiones), estado=estado,
            )
        )
    return resumenes, nunca_rastreados


def carga_partes(destino: Path) -> list[dict]:
    """`partes.json`'s own ``{"tag", "assets"}`` list, or `[]` when the file
    does not exist yet -- a brand-new collection (or one that has never
    outgrown a single part) has nothing to load (issue #223)."""
    ruta = destino / ARCHIVO_PARTES
    if not ruta.is_file():
        return []
    return json.loads(ruta.read_text(encoding="utf-8"))["partes"]


def asigna_partes(
    partes: list[dict], resumenes: list[ResumenInstrumento], base_tag: str
) -> list[dict]:
    """Assign every instrument's own `.tgz` asset to a release part
    (decision 4): an asset already recorded in `partes` (read by
    `carga_partes`) keeps that part -- it never moves -- and only a new
    asset is placed, in the first part with room, opening a new part only
    once every existing one is full. Part 1's own budget is
    `LIMITE_ASSETS_POR_RELEASE - _RESERVADOS_PARTE_1` (decision 5); every
    other part's is the full cap. An instrument with no asset (decision 6's
    text-less case) is never assigned to a part -- there is nothing to
    upload.

    `partes` is not mutated -- the return value is the partition to write
    back with `guarda_partes`.
    """
    partes = [dict(tag=p["tag"], assets=list(p["assets"])) for p in partes]
    if not partes:
        partes = [{"tag": base_tag, "assets": []}]

    ya_asignados = {nombre for parte in partes for nombre in parte["assets"]}
    nuevos = sorted(
        (r.asset for r in resumenes if r.asset and r.asset not in ya_asignados),
        key=lambda nombre: int(nombre.removesuffix(".tgz")),
    )

    for nombre in nuevos:
        for i, parte in enumerate(partes):
            cupo = LIMITE_ASSETS_POR_RELEASE - _RESERVADOS_PARTE_1 if i == 0 \
                else LIMITE_ASSETS_POR_RELEASE
            if len(parte["assets"]) < cupo:
                parte["assets"].append(nombre)
                break
        else:
            partes.append({"tag": _tag_de_parte(base_tag, len(partes) + 1), "assets": [nombre]})
    return partes


def guarda_partes(destino: Path, partes: list[dict]) -> None:
    """Write `partes.json` back -- the one place the partition is recorded,
    read by the next run's `carga_partes` so an asset already published
    never moves (issue #223's decision 4). Not a release asset: publishing a
    copy would invite a reader to trust it over GitHub's own listing
    (decision 3)."""
    (destino / ARCHIVO_PARTES).write_text(
        json.dumps({"partes": partes}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _advierte_si_falta_partes_json(destino: Path, partes_originales: list[dict],
                                    total_tarballs: int, base_tag: str) -> None:
    """A loud warning -- never a `SystemExit`, and never a silent fresh
    partition -- when `partes.json` was missing and this corpus needs more
    than one part (issue #223). Packaging still proceeds with a fresh,
    deterministic partition (sorted by `id_ordenamiento`), which is enough
    to produce a valid, under-the-cap publish plan; but if this collection
    is *already* published in more than one part, that fresh partition can
    disagree with which asset actually lives in which live release. Fase 3's
    one-liner (`gh release view <tag> --json assets`, once per already
    published part) reads reality back into `partes.json` first."""
    if partes_originales or total_tarballs <= LIMITE_ASSETS_POR_RELEASE - _RESERVADOS_PARTE_1:
        return
    print(
        f"AVISO: {destino / ARCHIVO_PARTES} no existia y hay {total_tarballs} tarballs -- mas "
        "de lo que cabe en una sola parte. Se genero un reparto nuevo, determinista, pero si "
        "este release ya esta publicado en mas de una parte, siembralo desde la realidad antes "
        "de publicar:\n"
        f"  gh release view {base_tag} --repo INGEOTEC/LegalIA "
        "--json assets --jq '.assets[].name'   # repite por cada parte ya publicada",
        file=sys.stderr,
    )


def _archivos_instrumento(directorio: Path) -> list[Path]:
    """Every file worth shipping for one instrument: its snapshot `.md`
    files and its own `estado.json` -- no `indice.json`/`notas/` here at
    all (neither collection has DOF linking, issue #220's own Scope). A
    hidden bookkeeping file (a `.progreso.json`-style checkpoint) is never
    shipped."""
    return sorted(
        p for p in directorio.glob("**/*")
        if p.is_file() and not any(parte.startswith(".") for parte in p.relative_to(directorio).parts)
    )


def empaqueta(
    outdir: Path,
    coleccion: str,
    destino: Path,
    resumenes: list[ResumenInstrumento],
    solo: set[str] | None = None,
) -> None:
    """Write one ``<id_ordenamiento>.tgz`` per instrument *with text*,
    byte-reproducibly -- see `empaqueta_scjn_leyes.py`'s own docstring for
    the reproducibility recipe (gzip stamped with mtime 0, members in sorted
    order, fixed ownership/mode), unchanged here. `solo` restricts the
    rewriting to the named `id_ordenamiento` keys, same incremental
    contract. A text-less instrument (`total_snapshots == 0`, decision 6) is
    skipped outright -- there is nothing to tar, and its `asset` stays
    `None`."""
    con_texto = [r for r in resumenes if r.total_snapshots > 0]
    if not con_texto:
        raise SystemExit(f"{outdir / coleccion} no tiene nada que empaquetar")

    for resumen in con_texto:
        directorio = outdir / coleccion / resumen.id_ordenamiento
        salida = destino / f"{resumen.id_ordenamiento}.tgz"
        if solo is not None and resumen.id_ordenamiento not in solo and salida.is_file():
            resumen.asset = salida.name
            resumen.bytes_comprimidos = salida.stat().st_size
            resumen.reempaquetado = False
            continue
        with open(salida, "wb") as bruto, \
                gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w") as tar:
            for archivo in _archivos_instrumento(directorio):
                datos = archivo.read_bytes()
                relativo = archivo.relative_to(directorio).as_posix()
                info = tarfile.TarInfo(f"{resumen.id_ordenamiento}/{relativo}")
                info.size = len(datos)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(datos))
        resumen.asset = salida.name
        resumen.bytes_comprimidos = salida.stat().st_size


def escribe_indice_global(
    destino: Path, coleccion: str, resumenes: list[ResumenInstrumento], generado: str
) -> None:
    """Write `indice-global.json.gz` -- the shared sibling of
    `empaqueta_scjn_leyes.escribe_indice_global`, with no `codNota` section
    and no published-index fallback (this release does not exist until
    this run publishes it, unlike `scjn-leyes` which a metadata backfill
    can patch after the fact).

    `resumenes` includes decision 6's text-less-but-crawled instruments
    (`total_snapshots == 0`): `construye_indice_global_reglamentos`/
    `_lineamientos` only ever add an `asset` key when `snapshots` is
    truthy, so these are indexed correctly with no asset regardless of what
    is passed here."""
    construye_indice = _INDICES_POR_ID[coleccion]
    indice = construye_indice(
        [
            {
                "id_ordenamiento": r.id_ordenamiento,
                "nombre": r.nombre,
                "asset": r.asset or f"{r.id_ordenamiento}.tgz",
                "snapshots": r.total_snapshots,
                **{
                    campo: (r.estado or {}).get(campo)
                    for campo in (CAMPO_CATEGORIA_ORDENAMIENTO, *CAMPOS_METADATOS)
                },
            }
            for r in resumenes
        ],
        generado=generado,
    )
    crudo = json.dumps(indice, ensure_ascii=False, sort_keys=False).encode("utf-8")
    salida = destino / ASSET_INDICE_GLOBAL
    with open(salida, "wb") as bruto, \
            gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz:
        gz.write(crudo)


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _tamano(bytes_: int) -> str:
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.1f} MB"
    return f"{bytes_ / 1024:.0f} KB"


# --- Numbered release parts (issue #223) -----------------------------------
#
# A collection may outgrow a single GitHub release: a release holds at most
# `LIMITE_ASSETS_POR_RELEASE` assets, and a release body at most
# `LIMITE_CUERPO_NOTAS` characters. The functions below plan a publish
# across the resulting numbered series of tags (`asigna_partes`/
# `guarda_partes` above record *which* asset is in which part) instead of a
# fixed `gh` command sequence.


def _seccion_partes_manifiesto(partes: list[dict]) -> list[str]:
    """The manifest's own "Partes del release" section -- how many parts,
    how many assets each one carries, and how much room the last one has
    left, so a human reviewing the manifest sees the publish shape at a
    glance."""
    if len(partes) <= 1:
        return []
    lineas = [f"## Partes del release ({len(partes)})", ""]
    for i, parte in enumerate(partes, 1):
        cupo = LIMITE_ASSETS_POR_RELEASE - _RESERVADOS_PARTE_1 if i == 1 \
            else LIMITE_ASSETS_POR_RELEASE
        n = len(parte["assets"])
        lineas.append(
            f"- `{parte['tag']}`: {n}/{cupo} asset(s)"
            + (f", MANIFEST.md/SHA256SUMS.txt/{ASSET_INDICE_GLOBAL}" if i == 1 else "")
            + f" -- {cupo - n} de espacio libre."
        )
    lineas.append("")
    return lineas


def _release_notes(coleccion: str, parte_n: int, partes: list[dict],
                    resumenes: list[ResumenInstrumento], nunca_rastreados: list[str],
                    base_tag: str) -> str:
    """The generated body of one part's own release notes (decision 6) --
    short by construction, since the full manifest ships as an asset
    instead."""
    total_partes = len(partes)
    total_catalogo = len(resumenes) + len(nunca_rastreados)
    if parte_n == 1:
        titulo = f"SCJN — {coleccion}" if total_partes == 1 else \
            f"SCJN — {coleccion} (parte 1 de {total_partes})"
        lineas = [
            f"# {titulo}", "",
            f"{total_catalogo} instrumento(s) en el catálogo; {len(resumenes)} ya "
            "rastreado(s) por la SCJN. Manifiesto completo, con la tabla de cada "
            "instrumento: asset `MANIFEST.md`.",
            "",
        ]
        if total_partes > 1:
            lineas += [
                f"Este release se publica en {total_partes} partes -- GitHub limita un "
                "release a 1000 assets (`LIMITE_ASSETS_POR_RELEASE`, issue #223). Las "
                "partes siguientes son continuaciones, solo tarballs:",
                "",
            ]
            lineas += [f"- `{partes[n - 1]['tag']}`" for n in range(2, total_partes + 1)]
            lineas.append("")
    else:
        titulo = f"SCJN — {coleccion} (parte {parte_n} de {total_partes})"
        lineas = [
            f"# {titulo}", "",
            f"Parte {parte_n} de {total_partes} de la colección `{coleccion}` -- "
            f"continuación de `{base_tag}`, que publica el manifiesto completo "
            f"(`MANIFEST.md`) y el índice (`{ASSET_INDICE_GLOBAL}`).",
            "",
        ]
    texto = "\n".join(lineas) + "\n"
    assert len(texto) < LIMITE_CUERPO_NOTAS, (
        f"RELEASE_NOTES de la parte {parte_n} paso de {LIMITE_CUERPO_NOTAS} caracteres"
    )
    return texto


def escribe_partes(destino: Path, coleccion: str, partes: list[dict],
                    resumenes: list[ResumenInstrumento], nunca_rastreados: list[str],
                    base_tag: str) -> None:
    """Write every part's own `RELEASE_NOTES*.md` (decision 6) and
    `parte-<n>.txt` (the asset names of that part, one per line, for
    ``xargs -a`` -- issue #223)."""
    for i, parte in enumerate(partes, 1):
        nombre_notas = "RELEASE_NOTES.md" if i == 1 else f"RELEASE_NOTES-{i}.md"
        (destino / nombre_notas).write_text(
            _release_notes(coleccion, i, partes, resumenes, nunca_rastreados, base_tag),
            encoding="utf-8",
        )
        (destino / f"parte-{i}.txt").write_text(
            "".join(f"{nombre}\n" for nombre in parte["assets"]), encoding="utf-8"
        )


def genera_publicar(coleccion: str, partes: list[dict], repo: str = "INGEOTEC/LegalIA",
                     solo: set[str] | None = None) -> str:
    """`PUBLICAR.md` -- the exact, copy-pasteable `gh` command sequence for
    the corpus as it actually is (issue #223, decision 8): one
    ``gh release create``/``gh release upload -a parte-<n>.txt`` pair per
    part, plus the final ``gh release edit <part 1> --latest``. Publishing
    stays a human's decision (#115, Hallazgo C) -- this only removes the
    improvisation the human hit mid-upload on 2026-09-10.

    `solo` (an `--instrumento` run) narrows this to just the parts whose own
    `.tgz` a rewritten instrument lives in (its own incremental contract):
    those tarballs changed on disk, so only their part needs a fresh
    ``gh release upload --clobber``, never a fresh ``release create``.
    """
    lineas = ["# Publicar — comandos generados, correr a mano (issue #115, Hallazgo C)", ""]

    if solo is not None:
        nombres_solo = {f"{id_}.tgz" for id_ in solo}
        afectadas = [
            i for i, parte in enumerate(partes, 1) if nombres_solo & set(parte["assets"])
        ]
        lineas.append(
            "Corrida con `--instrumento`: solo cambiaron los `.tgz` listados abajo. Sube "
            "solo la(s) parte(s) que los contienen (más el índice/manifiesto, siempre):"
        )
        lineas.append("")
        lineas.append("```bash")
        lineas.append(
            f"gh release upload {partes[0]['tag']} --repo {repo} --clobber "
            f"{ASSET_INDICE_GLOBAL} MANIFEST.md SHA256SUMS.txt"
        )
        for i in afectadas:
            tag = partes[i - 1]["tag"]
            lineas.append(f"xargs -a parte-{i}.txt gh release upload {tag} --repo {repo} --clobber")
        lineas.append("```")
        lineas.append("")
        return "\n".join(lineas) + "\n"

    lineas.append(
        "Republicar solo índice + manifiesto, sin tocar ningún `.tgz` (p. ej. tras un "
        "backfill de metadatos que no reempaqueta tarballs):"
    )
    lineas.append("")
    lineas.append("```bash")
    lineas.append(
        f"gh release upload {partes[0]['tag']} --repo {repo} --clobber "
        f"{ASSET_INDICE_GLOBAL} MANIFEST.md"
    )
    lineas.append("```")
    lineas.append("")

    lineas.append("Primer publish, o repartir de cero (todas las partes):")
    lineas.append("")
    lineas.append("```bash")
    for i, parte in enumerate(partes, 1):
        tag = parte["tag"]
        notas = "RELEASE_NOTES.md" if i == 1 else f"RELEASE_NOTES-{i}.md"
        titulo = f"SCJN — {coleccion}" if len(partes) == 1 else \
            f"SCJN — {coleccion} (parte {i} de {len(partes)})"
        if i == 1:
            lineas.append(
                f'gh release create {tag} MANIFEST.md SHA256SUMS.txt {ASSET_INDICE_GLOBAL} '
                f'--repo {repo} --title "{titulo}" --notes-file {notas}'
            )
        else:
            lineas.append(
                f'gh release create {tag} --repo {repo} --title "{titulo}" '
                f"--notes-file {notas}"
            )
        lineas.append(f"xargs -a parte-{i}.txt gh release upload {tag} --repo {repo} --clobber")
    lineas.append(f"gh release edit {partes[0]['tag']} --repo {repo} --latest")
    lineas.append("```")
    lineas.append("")
    return "\n".join(lineas) + "\n"


def _formatea_manifiesto(coleccion: str, resumenes: list[ResumenInstrumento],
                          nunca_rastreados: list[str],
                          partes: list[dict] | None = None) -> str:
    con_texto = sorted((r for r in resumenes if r.total_snapshots > 0), key=lambda r: r.nombre)
    sin_texto = sorted((r for r in resumenes if r.total_snapshots == 0), key=lambda r: r.nombre)
    total_catalogo = len(resumenes) + len(nunca_rastreados)

    lineas = [
        f"# SCJN — {coleccion}: manifiesto de empaquetado",
        "",
        f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"{total_catalogo} instrumento(s) en el catálogo (`{coleccion}`); "
        f"{len(resumenes)} ya rastreado(s) por la SCJN.",
        "",
        "Sin enlace a DOF (issue #220, Scope): no hay `indice.json` por instrumento "
        "ni sección `codNota` en el índice global -- el enlace se implementa en un "
        f"issue futuro, para {coleccion} y leyes por igual.",
        "",
    ]
    lineas.extend(_seccion_partes_manifiesto(partes or []))

    if nunca_rastreados:
        lineas.append(f"## Nunca rastreados ({len(nunca_rastreados)})")
        lineas.append("")
        lineas.extend(f"- {nombre}" for nombre in sorted(nunca_rastreados))
        lineas.append("")

    if sin_texto:
        lineas.append(
            f"## Sin texto consolidado en la SCJN ({len(sin_texto)})"
        )
        lineas.append("")
        lineas.append(
            "Rastreados por la SCJN, pero cada fila de su tabla de reformas trae "
            "`tieneArticulos=false` -- la SCJN los clasifica y los conoce, pero no sirve "
            "texto consolidado alguno. Indexados con `snapshots: 0` y sin `asset` "
            "(issue #222's decision 6): no hay nada que descargar."
        )
        lineas.append("")
        lineas.extend(
            f"- {r.nombre} (`{r.id_ordenamiento}`), rastreado "
            f"{(r.estado or {}).get('rastreado', '—')}"
            for r in sin_texto
        )
        lineas.append("")

    reempaquetados = [r for r in con_texto if r.reempaquetado]
    if con_texto and len(reempaquetados) != len(con_texto):
        lineas.append(f"## Actualizados en esta corrida ({len(reempaquetados)})")
        lineas.append("")
        lineas.append(
            f"Los otros {len(con_texto) - len(reempaquetados)} instrumento(s) conservan el "
            "`.tgz` que ya estaba en el destino; sólo se recalcularon el manifiesto, "
            f"`SHA256SUMS.txt` y `{ASSET_INDICE_GLOBAL}`. **Sube sólo estos assets** "
            f"(más `{ASSET_INDICE_GLOBAL}`, `SHA256SUMS.txt` y `MANIFEST.md`):"
        )
        lineas.append("")
        lineas.extend(
            f"- {r.nombre} (`{r.asset}`), rastreado "
            f"{(r.estado or {}).get('rastreado', '—')}"
            for r in reempaquetados
        )
        lineas.append("")

    lineas.append("## Instrumentos empaquetados")
    lineas.append("")
    lineas.append(
        "| instrumento | id_ordenamiento | snapshots | categoría | vigencia | "
        "asset | tamaño | rastreado |"
    )
    lineas.append("|---|---|---|---|---|---|---|---|")
    for r in con_texto:
        estado = r.estado or {}
        lineas.append(
            f"| {r.nombre} | `{r.id_ordenamiento}` | {r.total_snapshots} "
            f"| {estado.get('categoria_ordenamiento', '—')} "
            f"| {estado.get('vigencia', '—')} | `{r.asset}` | {_tamano(r.bytes_comprimidos)} "
            f"| {estado.get('rastreado', '—')} |"
        )
    lineas.append("")
    lineas.append(
        "**Antes de publicar**: lee esta tabla completa. Nada de este corpus se publica de "
        "forma automática — issue #115, Hallazgo C — la decisión de que es seguro publicar "
        "es humana."
    )
    return "\n".join(lineas) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--coleccion", choices=tuple(COLECCIONES_POR_ID), required=True,
        help="que coleccion id-keyed empaquetar",
    )
    p.add_argument(
        "--outdir", type=Path, default=Path("scripts/scjn"),
        help="donde fetch_scjn_legislacion.py --coleccion <coleccion> ya escribio esa coleccion",
    )
    p.add_argument("--destino", type=Path, default=None,
                    help="default: <outdir>/<coleccion>-release")
    p.add_argument(
        "--instrumento", action="append", metavar="ID", dest="instrumentos",
        help="repetible; reescribe solo el .tgz de estos id_ordenamiento",
    )
    args = p.parse_args(argv)

    destino = args.destino or (args.outdir / f"{args.coleccion}-release")
    destino.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base_tag = COLECCIONES_POR_ID[args.coleccion].tag_base

    resumenes, nunca_rastreados = resume_coleccion(args.outdir, args.coleccion)
    solo = set(args.instrumentos) if args.instrumentos else None
    if solo is not None:
        faltantes = solo - {r.id_ordenamiento for r in resumenes}
        if faltantes:
            raise SystemExit(
                f"{sorted(faltantes)} no tiene(n) snapshots en {args.outdir / args.coleccion}"
            )
    empaqueta(args.outdir, args.coleccion, destino, resumenes, solo=solo)
    escribe_indice_global(destino, args.coleccion, resumenes, generado)

    partes_originales = carga_partes(destino)
    _advierte_si_falta_partes_json(destino, partes_originales, len(resumenes), base_tag)
    partes = asigna_partes(partes_originales, resumenes, base_tag)
    guarda_partes(destino, partes)

    manifiesto = destino / "MANIFEST.md"
    manifiesto.write_text(
        _formatea_manifiesto(args.coleccion, resumenes, nunca_rastreados, partes),
        encoding="utf-8",
    )

    con_asset = sorted((r for r in resumenes if r.asset), key=lambda r: r.id_ordenamiento)
    sumas = destino / "SHA256SUMS.txt"
    sumas.write_text(
        "".join(f"{sha256(destino / r.asset)}  {r.asset}\n" for r in con_asset)
        + f"{sha256(destino / ASSET_INDICE_GLOBAL)}  {ASSET_INDICE_GLOBAL}\n",
        encoding="utf-8",
    )

    escribe_partes(destino, args.coleccion, partes, resumenes, nunca_rastreados, base_tag)
    (destino / "PUBLICAR.md").write_text(
        genera_publicar(args.coleccion, partes, solo=solo), encoding="utf-8"
    )

    con_texto = [r for r in resumenes if r.total_snapshots > 0]
    sin_texto = [r for r in resumenes if r.total_snapshots == 0]
    total = sum(r.bytes_comprimidos for r in resumenes)
    reescritos = sum(1 for r in con_texto if r.reempaquetado)
    if solo is not None:
        print(
            f"{reescritos} asset(s) .tgz reescrito(s): "
            f"{sorted(r.id_ordenamiento for r in con_texto if r.reempaquetado)}",
            file=sys.stderr,
        )
    print(
        f"{len(con_texto)} asset(s) .tgz, {_tamano(total)} en total; "
        f"{len(sin_texto)} instrumento(s) rastreado(s) sin texto consolidado (sin asset); "
        f"{len(nunca_rastreados)} instrumento(s) sin rastrear; "
        f"{len(partes)} parte(s) de release",
        file=sys.stderr,
    )
    print(
        f"-> {destino}/  (<id_ordenamiento>.tgz, {ASSET_INDICE_GLOBAL}, MANIFEST.md, "
        "SHA256SUMS.txt, partes.json, RELEASE_NOTES*.md, parte-*.txt, PUBLICAR.md)",
        file=sys.stderr,
    )
    print(
        "\nLee MANIFEST.md completo, luego PUBLICAR.md. Nada de este corpus se publica solo.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
