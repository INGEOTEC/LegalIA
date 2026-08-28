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

    # first publish — the release itself, with the two small text assets:
    cd scripts/scjn/leyes-release
    gh release create scjn-leyes MANIFEST.md SHA256SUMS.txt \\
        --repo INGEOTEC/LegalIA --title "SCJN — leyes" --notes-file MANIFEST.md

    # then the ~315 per-law tarballs, in batches (gh takes many paths at
    # once, but a batch that fails mid-way is cheaper to retry than one run
    # of 315). `--clobber` makes re-running after a network failure
    # idempotent, so a batch can simply be repeated:
    ls *.tgz | xargs -n 20 gh release upload scjn-leyes \\
        --repo INGEOTEC/LegalIA --clobber

    # a later run only has to re-upload the laws that changed:
    gh release upload scjn-leyes lft.tgz SHA256SUMS.txt MANIFEST.md \\
        --repo INGEOTEC/LegalIA --clobber

## What goes in each tarball

One `<slug>.tgz` per instrument, not one for the whole collection: a
consumer almost always wants a single law, and an incremental update only
has to re-upload the asset of the law that changed. Every member is
prefixed with `<slug>/`, so a tarball can be unpacked anywhere without
stepping on anything:

    <slug>/<fecha>.md          the snapshots, with their provenance header
    <slug>/indice.json         the codNota link + #115/#126/#127's signals
    <slug>/notas/nota-<cod>.md the DOF text of every candidate considered

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
    (`notas/nota-<cod>.md`) travelled inside it."""

    slug: str
    nombre: str
    total_snapshots: int
    porcentaje_enlazado: float | None
    confirmados_por_contenido: int
    notas_dof: int = 0
    asset: str | None = None
    bytes_comprimidos: int = 0


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


def empaqueta(outdir: Path, destino: Path, resumenes: list[ResumenInstrumento]) -> None:
    """Write one `<slug>.tgz` per instrument, each byte-reproducibly — same
    pattern as `scripts/empaqueta_historial.py` (gzip stamped mtime 0,
    members added in sorted order, fixed ownership/mode); what changed for
    #128 is the walk, not the recipe. Every member is prefixed with
    `<slug>/` so the tarball unpacks anywhere without stepping on anything.

    Fills each summary's `asset`/`bytes_comprimidos` in place, so the
    manifest can report what was actually written."""
    if not resumenes:
        raise SystemExit(f"{outdir / COLECCION} no tiene nada que empaquetar")

    for resumen in resumenes:
        directorio = outdir / COLECCION / resumen.slug
        salida = destino / f"{resumen.slug}.tgz"
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


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _tamano(bytes_: int) -> str:
    """A size a human reads at a glance in the manifest's own table — the
    exact byte count is in the asset itself, not something to eyeball."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.1f} MB"
    return f"{bytes_ / 1024:.0f} KB"


def _formatea_manifiesto(
    resumenes: list[ResumenInstrumento], nunca_rastreados: list[str]
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
    lineas.append("## Instrumentos empaquetados")
    lineas.append("")
    lineas.append(
        "| instrumento | slug | snapshots | % enlazado | "
        "confirmados por contenido (#127) | asset | tamaño | notas DOF |"
    )
    lineas.append("|---|---|---|---|---|---|---|---|")
    for r in ordenados:
        porcentaje = f"{r.porcentaje_enlazado:.0%}" if r.porcentaje_enlazado is not None else "—"
        lineas.append(
            f"| {r.nombre} | `{r.slug}` | {r.total_snapshots} "
            f"| {porcentaje} | {r.confirmados_por_contenido} "
            f"| `{r.asset}` | {_tamano(r.bytes_comprimidos)} | {r.notas_dof} |"
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
    args = p.parse_args(argv)

    args.destino.mkdir(parents=True, exist_ok=True)

    resumenes, nunca_rastreados = resume_coleccion(args.outdir)
    empaqueta(args.outdir, args.destino, resumenes)

    manifiesto = args.destino / "MANIFEST.md"
    manifiesto.write_text(_formatea_manifiesto(resumenes, nunca_rastreados), encoding="utf-8")

    # One line per asset, in the format `sha256sum -c` accepts.
    sumas = args.destino / "SHA256SUMS.txt"
    sumas.write_text(
        "".join(
            f"{sha256(args.destino / r.asset)}  {r.asset}\n"
            for r in sorted(resumenes, key=lambda r: r.slug)
        ),
        encoding="utf-8",
    )

    total = sum(r.bytes_comprimidos for r in resumenes)
    print(
        f"{len(resumenes)} asset(s) .tgz, {_tamano(total)} en total; "
        f"{len(nunca_rastreados)} instrumento(s) sin rastrear (sin asset)",
        file=sys.stderr,
    )
    print(f"-> {args.destino}/  (<slug>.tgz, MANIFEST.md, SHA256SUMS.txt)", file=sys.stderr)
    print(
        "\nLee MANIFEST.md completo antes de publicar. Nada de este corpus se publica solo.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
