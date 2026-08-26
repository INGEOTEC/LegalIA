#!/usr/bin/env python3
"""Package the SCJN-based `leyes` corpus (snapshots + codNota links, issue
#123/#128) into a byte-reproducible tarball, plus a human-readable manifest
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

    ./scripts/empaqueta_scjn_leyes.py --outdir scjn-legislacion --destino leyes-release
    less leyes-release/MANIFEST.md   # read it. all of it. before the next line.

    # first publish:
    gh release create scjn-leyes leyes-release/leyes.tgz leyes-release/MANIFEST.md \\
        leyes-release/SHA256SUMS.txt --repo INGEOTEC/LegalIA --title "SCJN — leyes" \\
        --notes-file leyes-release/MANIFEST.md

    # updating an existing one, after a later run:
    gh release upload scjn-leyes leyes-release/leyes.tgz leyes-release/SHA256SUMS.txt \\
        --repo INGEOTEC/LegalIA --clobber

## What goes in the tarball

Every already-crawled `leyes` instrument directory under
``<outdir>/leyes/<slug>/`` (as `fetch_scjn_legislacion.py` wrote them): each
snapshot `.md` file with its own provenance header, and — when
`enlaza_scjn_legislacion.py` has already run for it — its own `indice.json`,
carrying issue #115/#126/#127's confidence signals alongside each snapshot's
`codNota`. An instrument crawled but not yet linked is still packaged (its
raw snapshots, no `indice.json`) rather than held back; the manifest calls
that out explicitly instead of hiding it.
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

from nota2md import download_legal_provisions_provenance_ids  # noqa: E402
from nota2md.scjn import (  # noqa: E402
    UMBRAL_CONFIANZA_SIMILITUD,
    UMBRAL_MINIMO_SIMILITUD,
    es_acuerdo_interno,
    grupo_instrumento,
    lee_cabecera,
    ratio_similitud,
    slug_instrumento,
    versiones_de_directorio,
)

COLECCION = "leyes"


def _clasifica(nombre_catalogo: str, ordenamiento_guardado: str, ratio: float) -> str:
    """The same #115 confidence classification `scripts/audita_scjn_legislacion.py`
    already computes (`confiable`/`sospechoso`/`bajo_umbral`/`acuerdo_interno`/
    `grupo_incompatible`) — kept as its own small copy, built from the same
    public `nota2md.scjn` primitives that script uses, rather than importing
    across scripts: there is no precedent for that in this repo, and the
    logic itself is thin enough that duplicating it here is cheaper than
    inventing a shared module for #128 alone."""
    if es_acuerdo_interno(ordenamiento_guardado):
        return "acuerdo_interno"
    grupo_nombre = grupo_instrumento(nombre_catalogo)
    grupo_titulo = grupo_instrumento(ordenamiento_guardado)
    if grupo_nombre is not None and grupo_titulo is not None and grupo_nombre != grupo_titulo:
        return "grupo_incompatible"
    if ratio < UMBRAL_MINIMO_SIMILITUD:
        return "bajo_umbral"
    if ratio < UMBRAL_CONFIANZA_SIMILITUD:
        return "sospechoso"
    return "confiable"


@dataclass
class ResumenInstrumento:
    """One instrument's own row in the manifest: how it was found
    (`motivo`/`ratio`, issue #115's classification) and how much of it is
    actually linked to a codNota (`porcentaje_enlazado`, `total_snapshots`,
    `confirmados_por_contenido` — issue #127's count of links given extra
    certainty by content diff). `porcentaje_enlazado` is None when the
    instrument was crawled (Fase 1) but never linked (Fase 2 pendiente, no
    `indice.json` yet) — a fact the manifest calls out, not hides."""

    slug: str
    nombre: str
    motivo: str | None
    ratio: float | None
    total_snapshots: int
    porcentaje_enlazado: float | None
    confirmados_por_contenido: int


def resume_coleccion(outdir: Path) -> tuple[list[ResumenInstrumento], list[str]]:
    """Every `leyes` instrument in the Diputados catalogue, summarized for
    the manifest, plus the catalogue names of the ones never crawled at all
    (issue #124's Fase 1 pendiente) — returned separately, since a
    never-crawled instrument has nothing on disk to classify or link."""
    instrumentos = download_legal_provisions_provenance_ids(COLECCION)
    resumenes = []
    nunca_rastreados = []
    for entrada in instrumentos:
        slug = slug_instrumento(entrada)
        destino = outdir / COLECCION / slug
        versiones = versiones_de_directorio(destino) if destino.is_dir() else []
        if not versiones:
            nunca_rastreados.append(entrada["nombre"])
            continue

        ordenamiento = lee_cabecera(versiones[0].archivo).get("ordenamiento")
        ratio = ratio_similitud(ordenamiento, entrada["nombre"]) if ordenamiento else None
        motivo = _clasifica(entrada["nombre"], ordenamiento, ratio) if ordenamiento else None

        porcentaje = None
        confirmados = 0
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
                slug=slug, nombre=entrada["nombre"], motivo=motivo, ratio=ratio,
                total_snapshots=len(versiones), porcentaje_enlazado=porcentaje,
                confirmados_por_contenido=confirmados,
            )
        )
    return resumenes, nunca_rastreados


def _archivos_corpus(outdir: Path) -> list[Path]:
    """Every file worth shipping under `<outdir>/leyes/`: each instrument's
    own snapshot `.md` files and its `indice.json` (when it has one) —
    `<outdir>/leyes/`'s own `.progreso.json` checkpoint
    (`fetch_scjn_legislacion.py`'s in-progress-run bookkeeping) sits one
    level above any instrument directory, so this glob never reaches it."""
    base = outdir / COLECCION
    return sorted(p for p in base.glob("*/*") if p.is_file())


def empaqueta(outdir: Path, destino: Path) -> Path:
    """Write `leyes.tgz`, byte-reproducibly — same pattern as
    `scripts/empaqueta_historial.py` (gzip stamped mtime 0, members added in
    sorted order, fixed ownership/mode), just walking
    `<outdir>/leyes/<slug>/*` instead of a flat directory of JSON files."""
    archivos = _archivos_corpus(outdir)
    if not archivos:
        raise SystemExit(f"{outdir / COLECCION} no tiene nada que empaquetar")

    salida = destino / "leyes.tgz"
    with open(salida, "wb") as bruto, \
            gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w") as tar:
        for archivo in archivos:
            datos = archivo.read_bytes()
            info = tarfile.TarInfo(archivo.relative_to(outdir / COLECCION).as_posix())
            info.size = len(datos)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(datos))
    return salida


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _formatea_manifiesto(
    resumenes: list[ResumenInstrumento], nunca_rastreados: list[str]
) -> str:
    ordenados = sorted(resumenes, key=lambda r: r.ratio if r.ratio is not None else -1.0)
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

    lineas.append("## Clasificación de confianza (issue #115), del menos al más confiable")
    lineas.append("")
    lineas.append(
        "| ratio | clasificación | instrumento | slug | snapshots | % enlazado | "
        "confirmados por contenido (#127) |"
    )
    lineas.append("|---|---|---|---|---|---|---|")
    for r in ordenados:
        # `ratio`/`motivo` are only None if a snapshot's own header were
        # missing `ordenamiento:` entirely — `_cabecera` always writes it,
        # so this is defensive, not an expected case.
        ratio = f"{r.ratio:.3f}" if r.ratio is not None else "—"
        motivo = r.motivo if r.motivo is not None else "sin_ordenamiento"
        porcentaje = f"{r.porcentaje_enlazado:.0%}" if r.porcentaje_enlazado is not None else "—"
        lineas.append(
            f"| {ratio} | {motivo} | {r.nombre} | `{r.slug}` | {r.total_snapshots} "
            f"| {porcentaje} | {r.confirmados_por_contenido} |"
        )
    lineas.append("")

    por_motivo: dict[str, int] = {}
    for r in resumenes:
        por_motivo[r.motivo] = por_motivo.get(r.motivo, 0) + 1
    lineas.append(f"Resumen por clasificación: {por_motivo}")
    lineas.append("")
    lineas.append(
        "**Antes de publicar**: lee esta tabla completa, empezando por lo menos confiable "
        "(`acuerdo_interno`/`grupo_incompatible`/`bajo_umbral` son casi-certeza de documento "
        "equivocado — issue #115). Nada de este corpus se publica de forma automática — "
        "issue #128 — la decisión de que es seguro publicar es humana."
    )
    return "\n".join(lineas) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="donde fetch_scjn_legislacion.py / enlaza_scjn_legislacion.py ya escribieron 'leyes'",
    )
    p.add_argument("--destino", type=Path, default=Path("scjn-leyes"))
    args = p.parse_args(argv)

    args.destino.mkdir(parents=True, exist_ok=True)

    resumenes, nunca_rastreados = resume_coleccion(args.outdir)
    tarball = empaqueta(args.outdir, args.destino)

    manifiesto = args.destino / "MANIFEST.md"
    manifiesto.write_text(_formatea_manifiesto(resumenes, nunca_rastreados), encoding="utf-8")

    sumas = args.destino / "SHA256SUMS.txt"
    sumas.write_text(f"{sha256(tarball)}  {tarball.name}\n", encoding="utf-8")

    print(f"leyes.tgz: {tarball.stat().st_size} bytes", file=sys.stderr)
    print(
        f"{len(resumenes)} instrumento(s) rastreado(s), {len(nunca_rastreados)} sin rastrear",
        file=sys.stderr,
    )
    print(f"-> {args.destino}/  (leyes.tgz, MANIFEST.md, SHA256SUMS.txt)", file=sys.stderr)
    print(
        "\nLee MANIFEST.md completo antes de publicar. Nada de este corpus se publica solo.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
