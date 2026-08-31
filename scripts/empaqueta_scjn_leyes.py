#!/usr/bin/env python3
"""Package the SCJN-based `leyes` corpus (snapshots + codNota links + the DOF
notes each link was decided against, issue #123/#128) into one
byte-reproducible tarball *per instrument*, plus a human-readable manifest
and a checksum file — same tarball pattern as `scripts/empaqueta_historial.py`.

## Manual publish only — never automated

Unlike `historial-legislativo` (Diputados/DOF, already reliable, republished
monthly by `.github/workflows/reformas.yml` with no human in the loop), the
SCJN's own search can return a completely wrong document for an instrument
(issue #115, Hallazgo C) — so nothing this script produces is ever published
automatically, now or in the future. The packaging step (this script) and
the publish step (a person running `gh` by hand) are deliberately kept
apart: this script never calls `gh` itself, and no `.github/workflows/` file
should ever call it and then publish on its own.

    # Defaults: --outdir scripts/scjn, --destino scripts/scjn/leyes-release
    # (the directories the corpus actually lives in today, both under the
    # `scripts/scjn/` that .gitignore excludes).
    ./scripts/empaqueta_scjn_leyes.py
    less scripts/scjn/leyes-release/MANIFEST.md   # read it. all of it.

    # first publish — the release itself, with the small text assets and the
    # reverse index (issue #117) every reader resolves a codNota against:
    cd scripts/scjn/leyes-release
    gh release create scjn-leyes MANIFEST.md SHA256SUMS.txt indice-global.json.gz \\
        --repo INGEOTEC/LegalIA --title "SCJN — leyes" --notes-file MANIFEST.md

    # then the ~315 per-law tarballs, in batches (gh takes many paths at
    # once, but a batch that fails mid-way is cheaper to retry than one run
    # of 315). `--clobber` makes re-running after a network failure
    # idempotent, so a batch can simply be repeated:
    ls *.tgz | xargs -n 20 gh release upload scjn-leyes \\
        --repo INGEOTEC/LegalIA --clobber

    # a later run only has to re-upload the laws that changed -- but always
    # the reverse index with them: it is the union of every indice.json, so
    # any change to any law makes the published one stale.
    gh release upload scjn-leyes lft.tgz indice-global.json.gz \\
        SHA256SUMS.txt MANIFEST.md --repo INGEOTEC/LegalIA --clobber

## What goes in each tarball

One `<slug>.tgz` per instrument, not one for the whole collection: a
consumer almost always wants a single law, and an incremental update only
has to re-upload the asset of the law that changed. Every member is
prefixed with `<slug>/`, so a tarball can be unpacked anywhere without
stepping on anything:

    <slug>/<fecha>.md          the snapshots, with their provenance header
    <slug>/indice.json         the codNota link + #115/#126/#127's signals
    <slug>/estado.json         when it was crawled/linked, and against which
                               Diputados `actualizado` (issue #148)
    <slug>/notas/nota-<cod>.md the DOF text of every candidate considered

`estado.json` travels with them so the release itself carries its own
freshness metadata: which `actualizado` the published snapshots correspond
to is then readable from the asset, with nothing to trust in local scratch.

## Updating one law (issue #148)

``--instrumento SLUG`` (repeatable) rewrites only the named instruments'
tarballs and leaves every other `.tgz` already in `--destino` exactly as it
is — the manifest says which ones were actually rewritten, so the human
review before publishing is a handful of rows instead of 315. The manifest,
`SHA256SUMS.txt` and `indice-global.json.gz` are always recomputed over the
whole corpus, though: the reverse index is the union of every `indice.json`,
so one law changing makes the published one stale.

    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes --plan
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes \\
        --instrumento lft
    ./scripts/enlaza_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes \\
        --instrumento lft
    ./scripts/empaqueta_scjn_leyes.py --instrumento lft
    less scripts/scjn/leyes-release/MANIFEST.md   # short now. still read it all.

The DOF notes travel *with* the snapshots on purpose: `notas/` is the text
each link was decided against (#126/#127), so shipping both makes the link
auditable without going back to the network.

An instrument crawled but not yet linked is still packaged (its raw
snapshots, no `indice.json`) rather than held back; the manifest calls that
out explicitly instead of hiding it.

Needs `leyes`' own ``catalogo.json`` (`extract_scjn_titles.py`), already
written under ``<outdir>/leyes/``, to list every instrument the Diputados
catalogue names — including one never crawled at all (Fase 1 pendiente,
issue #124), which the manifest lists rather than silently omits.
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

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md.scjn import (  # noqa: E402
    ASSET_INDICE_GLOBAL,
    construye_indice_global,
    lee_estado,
    slug_instrumento,
    versiones_de_directorio,
)

COLECCION = "leyes"


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


@dataclass
class ResumenInstrumento:
    """One instrument's own row in the manifest: how much of it is
    actually linked to a codNota (`porcentaje_enlazado`, `total_snapshots`,
    `confirmados_por_contenido` — issue #127's count of links given extra
    certainty by content diff). `porcentaje_enlazado` is None when the
    instrument was crawled (Fase 1) but never linked (Fase 2 pendiente, no
    `indice.json` yet) — a fact the manifest calls out, not hides.

    `asset`/`bytes_comprimidos` are filled in by `empaqueta` once the
    instrument's own tarball exists; `notas_dof` is how many DOF notes
    (`notas/nota-<cod>.md`) travelled inside it. `indice` is the instrument's
    own `indice.json`, already parsed — kept on the summary so
    `escribe_indice_global` builds the reverse index off the same read this
    one did, rather than walking every instrument's directory a second time."""

    slug: str
    nombre: str
    total_snapshots: int
    porcentaje_enlazado: float | None
    confirmados_por_contenido: int
    notas_dof: int = 0
    asset: str | None = None
    bytes_comprimidos: int = 0
    indice: list[dict] | None = None
    #: This instrument's own `estado.json` (issue #148) — when it was last
    #: crawled and linked, and against which Diputados `actualizado`. Shown
    #: in the manifest so the human reviewing an incremental update can see
    #: at a glance which laws the run actually refreshed.
    estado: dict | None = None
    #: Whether this run rewrote the instrument's own tarball. False for the
    #: ones a `--instrumento` run left untouched and only re-listed.
    reempaquetado: bool = True


def resume_coleccion(outdir: Path) -> tuple[list[ResumenInstrumento], list[str]]:
    """Every `leyes` instrument in the Diputados catalogue, summarized for
    the manifest, plus the catalogue names of the ones never crawled at all
    (issue #124's Fase 1 pendiente) — returned separately, since a
    never-crawled instrument has nothing on disk to classify or link."""
    instrumentos = _load_catalog(outdir, COLECCION)
    resumenes = []
    nunca_rastreados = []
    for entrada in instrumentos:
        slug = slug_instrumento(entrada)
        destino = outdir / COLECCION / slug
        versiones = versiones_de_directorio(destino) if destino.is_dir() else []
        if not versiones:
            nunca_rastreados.append(entrada["nombre"])
            continue

        porcentaje = None
        confirmados = 0
        indice = None
        indice_path = destino / "indice.json"
        if indice_path.is_file():
            indice = json.loads(indice_path.read_text(encoding="utf-8"))
            total = len(indice)
            enlazados = sum(1 for e in indice if e.get("codNota") is not None)
            porcentaje = (enlazados / total) if total else 0.0
            confirmados = sum(
                1 for e in indice if e.get("content_diff_confirmed_codNota") is not None
            )

        resumenes.append(
            ResumenInstrumento(
                slug=slug, nombre=entrada["nombre"],
                total_snapshots=len(versiones), porcentaje_enlazado=porcentaje,
                confirmados_por_contenido=confirmados,
                notas_dof=len(list((destino / "notas").glob("nota-*.md")))
                if (destino / "notas").is_dir() else 0,
                indice=indice,
                estado=lee_estado(destino) or None,
            )
        )
    return resumenes, nunca_rastreados


def _archivos_instrumento(directorio: Path) -> list[Path]:
    """Every file worth shipping for one instrument: its snapshot `.md`
    files, its `indice.json` (when `enlaza_scjn_legislacion.py` has already
    run for it) and its own `notas/nota-<cod>.md` — the DOF text each link
    was decided against, which stopped being scratch the moment the link
    became something to audit (issue #128). A hidden bookkeeping file (a
    `.progreso.json`-style checkpoint) is never shipped."""
    return sorted(
        p for p in directorio.glob("**/*")
        if p.is_file() and not any(parte.startswith(".") for parte in p.relative_to(directorio).parts)
    )


def empaqueta(
    outdir: Path,
    destino: Path,
    resumenes: list[ResumenInstrumento],
    solo: set[str] | None = None,
) -> None:
    """Write one `<slug>.tgz` per instrument, each byte-reproducibly — same
    pattern as `scripts/empaqueta_historial.py` (gzip stamped mtime 0,
    members added in sorted order, fixed ownership/mode); what changed for
    #128 is the walk, not the recipe. Every member is prefixed with
    `<slug>/` so the tarball unpacks anywhere without stepping on anything.

    `solo` (issue #148) restricts the rewriting to the named slugs — the
    incremental case, where one law changed and re-tarring the other ~314 is
    pure work for a byte-identical result. Every other instrument keeps the
    tarball already sitting in `destino` and is measured from it, so the
    manifest and the checksums still describe the whole corpus; one that has
    no tarball there yet is packaged regardless, since "skip it" would mean
    publishing a manifest row for an asset that does not exist.

    Fills each summary's `asset`/`bytes_comprimidos` in place, so the
    manifest can report what was actually written."""
    if not resumenes:
        raise SystemExit(f"{outdir / COLECCION} no tiene nada que empaquetar")

    for resumen in resumenes:
        directorio = outdir / COLECCION / resumen.slug
        salida = destino / f"{resumen.slug}.tgz"
        if solo is not None and resumen.slug not in solo and salida.is_file():
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
                info = tarfile.TarInfo(f"{resumen.slug}/{relativo}")
                info.size = len(datos)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(datos))
        resumen.asset = salida.name
        resumen.bytes_comprimidos = salida.stat().st_size


def escribe_indice_global(
    destino: Path, resumenes: list[ResumenInstrumento], generado: str
) -> dict:
    """Write `indice-global.json.gz` next to the tarballs and return the tally
    of what went in — the reverse index `codNota -> instrumento` a consumer
    needs to answer "which law does this decree reform" without downloading
    380 MB of corpus (issue #117, Paso 1).

    Written like every other asset here: gzip stamped with mtime 0, keys in
    sorted order (`construye_indice_global` sorts by slug and, numerically, by
    codNota), so an unchanged corpus re-packages to the same bytes except for
    its own `generado` stamp — the same caveat `MANIFEST.md` already carries,
    and the reason the tarballs are the byte-identical ones.

    Like the tarballs, this asset is **published by hand**: it has to be
    re-uploaded whenever the corpus changes, or a reader will resolve codigos
    against a corpus that no longer matches — the same manual step issue #148
    has to respect when it starts updating law by law.
    """
    indice, conteos = construye_indice_global(
        [
            {
                "slug": r.slug,
                "nombre": r.nombre,
                "asset": r.asset or f"{r.slug}.tgz",
                "snapshots": r.total_snapshots,
                "indice": r.indice,
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
    conteos["bytes_comprimidos"] = salida.stat().st_size
    return conteos


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _tamano(bytes_: int) -> str:
    """A size a human reads at a glance in the manifest's own table — the
    exact byte count is in the asset itself, not something to eyeball."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.1f} MB"
    return f"{bytes_ / 1024:.0f} KB"


def _formatea_manifiesto(
    resumenes: list[ResumenInstrumento],
    nunca_rastreados: list[str],
    conteos_indice: dict | None = None,
) -> str:
    ordenados = sorted(resumenes, key=lambda r: r.nombre)
    total_catalogo = len(resumenes) + len(nunca_rastreados)

    lineas = [
        "# SCJN — leyes: manifiesto de empaquetado",
        "",
        f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"{total_catalogo} instrumento(s) en el catálogo de Diputados (`leyes`); "
        f"{len(resumenes)} ya rastreado(s) por la SCJN.",
        "",
    ]

    if nunca_rastreados:
        lineas.append(f"## Nunca rastreados — Fase 1 pendiente ({len(nunca_rastreados)}, issue #124)")
        lineas.append("")
        lineas.extend(f"- {nombre}" for nombre in sorted(nunca_rastreados))
        lineas.append("")

    sin_enlazar = [r for r in ordenados if r.porcentaje_enlazado is None]
    if sin_enlazar:
        lineas.append(f"## Rastreados pero nunca enlazados — Fase 2 pendiente ({len(sin_enlazar)})")
        lineas.append("")
        lineas.extend(f"- {r.nombre} (`{r.slug}`)" for r in sin_enlazar)
        lineas.append("")

    # La clasificación de confianza de #115 (ratio/motivo) ya no aparece
    # aquí: los títulos se revisaron y corrigieron a mano, así que ordenar
    # por sospecha dejó de decir nada — la tabla va por nombre.
    # Issue #148: on an incremental run only a handful of laws were actually
    # rewritten, and those are the only rows a human has to read closely
    # (¿la SCJN devolvió el documento correcto?, ¿bajó el % de enlace?).
    # They get their own section above the full table, which stays complete
    # because the checksums and the reverse index still cover everything.
    reempaquetados = [r for r in ordenados if r.reempaquetado]
    if len(reempaquetados) != len(ordenados):
        lineas.append(f"## Actualizados en esta corrida ({len(reempaquetados)}, issue #148)")
        lineas.append("")
        lineas.append(
            f"Los otros {len(ordenados) - len(reempaquetados)} instrumento(s) conservan el "
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
        "| instrumento | slug | snapshots | % enlazado | "
        "confirmados por contenido (#127) | asset | tamaño | notas DOF | rastreado |"
    )
    lineas.append("|---|---|---|---|---|---|---|---|---|")
    for r in ordenados:
        porcentaje = f"{r.porcentaje_enlazado:.0%}" if r.porcentaje_enlazado is not None else "—"
        lineas.append(
            f"| {r.nombre} | `{r.slug}` | {r.total_snapshots} "
            f"| {porcentaje} | {r.confirmados_por_contenido} "
            f"| `{r.asset}` | {_tamano(r.bytes_comprimidos)} | {r.notas_dof} "
            f"| {(r.estado or {}).get('rastreado', '—')} |"
        )
    lineas.append("")

    if conteos_indice is not None:
        # Lo que el índice inverso sí sabe, y lo que dejó fuera y por qué: una
        # entrada sin `codNota` de certeza no entra (issue #117, D2), así que
        # el total de aquí es siempre menor que el de snapshots empaquetados.
        fuera = {
            clave: valor for clave, valor in conteos_indice.items()
            if clave not in ("linked", "bytes_comprimidos")
        }
        lineas.append(f"## Índice inverso — `{ASSET_INDICE_GLOBAL}` (#117)")
        lineas.append("")
        lineas.append(
            f"{conteos_indice['linked']} snapshot(s) con `codNota` de certeza en el "
            f"índice; {_tamano(conteos_indice['bytes_comprimidos'])} comprimido."
        )
        lineas.append("")
        lineas.append("Fuera del índice, por motivo:")
        lineas.append("")
        lineas.extend(f"- `{clave}`: {valor}" for clave, valor in sorted(fuera.items()))
        lineas.append("")
        lineas.append(
            f"Este asset se sube a mano igual que los tarballs — y hay que volver a "
            f"subirlo cada vez que el corpus cambie, o `snapshot_de_codNota` "
            f"resolverá contra un corpus que ya no corresponde."
        )
        lineas.append("")

    lineas.append(
        "**Antes de publicar**: lee esta tabla completa. Nada de este corpus se publica de "
        "forma automática — issue #128 — la decisión de que es seguro publicar es humana."
    )
    return "\n".join(lineas) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Los defaults son los directorios que el corpus usa hoy: `scripts/scjn`
    # es donde fetch/enlaza ya escribieron `leyes/`, y el destino cuelga de
    # ahi mismo — ambos bajo el `scripts/scjn/` que .gitignore excluye, para
    # que ninguna corrida deje datos rastreables por git.
    p.add_argument(
        "--outdir", type=Path, default=Path("scripts/scjn"),
        help="donde fetch_scjn_legislacion.py / enlaza_scjn_legislacion.py ya escribieron 'leyes'",
    )
    p.add_argument("--destino", type=Path, default=Path("scripts/scjn/leyes-release"))
    p.add_argument(
        "--sin-indice-global", action="store_true",
        help=f"no escribir {ASSET_INDICE_GLOBAL} (el índice inverso "
        "codNota -> instrumento, issue #117); por default se genera",
    )
    p.add_argument(
        "--instrumento", action="append", metavar="SLUG",
        help=(
            "repetible; reescribe solo el .tgz de estos instrumentos (issue #148). El "
            f"manifiesto, SHA256SUMS.txt y {ASSET_INDICE_GLOBAL} se recalculan siempre "
            "completos: el indice inverso es la union de todos los indice.json, asi que "
            "cualquier cambio en una ley deja obsoleto el publicado"
        ),
    )
    args = p.parse_args(argv)

    args.destino.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).isoformat(timespec="seconds")

    resumenes, nunca_rastreados = resume_coleccion(args.outdir)
    solo = set(args.instrumento) if args.instrumento else None
    if solo is not None:
        faltantes = solo - {r.slug for r in resumenes}
        if faltantes:
            raise SystemExit(
                f"{sorted(faltantes)} no tiene(n) snapshots en {args.outdir / COLECCION} -- "
                "corre primero ./scripts/fetch_scjn_legislacion.py --instrumento <slug>"
            )
    empaqueta(args.outdir, args.destino, resumenes, solo=solo)

    conteos_indice = None
    if not args.sin_indice_global:
        conteos_indice = escribe_indice_global(args.destino, resumenes, generado)

    manifiesto = args.destino / "MANIFEST.md"
    manifiesto.write_text(
        _formatea_manifiesto(resumenes, nunca_rastreados, conteos_indice),
        encoding="utf-8",
    )

    # One line per asset, in the format `sha256sum -c` accepts.
    sumas = args.destino / "SHA256SUMS.txt"
    sumas.write_text(
        "".join(
            f"{sha256(args.destino / r.asset)}  {r.asset}\n"
            for r in sorted(resumenes, key=lambda r: r.slug)
        )
        + (
            f"{sha256(args.destino / ASSET_INDICE_GLOBAL)}  {ASSET_INDICE_GLOBAL}\n"
            if conteos_indice is not None else ""
        ),
        encoding="utf-8",
    )

    total = sum(r.bytes_comprimidos for r in resumenes)
    reescritos = sum(1 for r in resumenes if r.reempaquetado)
    if solo is not None:
        print(
            f"{reescritos} asset(s) .tgz reescrito(s): "
            f"{sorted(r.slug for r in resumenes if r.reempaquetado)}",
            file=sys.stderr,
        )
    print(
        f"{len(resumenes)} asset(s) .tgz, {_tamano(total)} en total; "
        f"{len(nunca_rastreados)} instrumento(s) sin rastrear (sin asset)",
        file=sys.stderr,
    )
    if conteos_indice is not None:
        print(
            f"{ASSET_INDICE_GLOBAL}: {conteos_indice['linked']} codNota con certeza, "
            f"{_tamano(conteos_indice['bytes_comprimidos'])}",
            file=sys.stderr,
        )
    print(
        f"-> {args.destino}/  (<slug>.tgz, {ASSET_INDICE_GLOBAL}, MANIFEST.md, "
        "SHA256SUMS.txt)",
        file=sys.stderr,
    )
    print(
        "\nLee MANIFEST.md completo antes de publicar. Nada de este corpus se publica solo.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
