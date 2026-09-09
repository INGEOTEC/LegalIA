#!/usr/bin/env python3
"""Package the SCJN-based `reglamentos` corpus (issue #220, Fase 4) into one
byte-reproducible tarball *per instrument*, plus a human-readable manifest
and a checksum file -- the `reglamentos` sibling of
`empaqueta_scjn_leyes.py`, without `indice.json`/`notas/` or a `codNota`
section: this corpus has no DOF linking at all (issue #220's own Scope),
so every ``<id_ordenamiento>.tgz`` ships only
``<id_ordenamiento>/<fecha>.md`` and ``<id_ordenamiento>/estado.json``.

## Manual publish only — never automated

Same rule as `scjn-leyes` (issue #115, Hallazgo C): nothing this script
produces is ever published automatically, now or in the future. The
packaging step (this script) and the publish step (a person running `gh`
by hand) are deliberately kept apart.

    # Defaults: --outdir scripts/scjn, --destino scripts/scjn/reglamentos-release
    ./scripts/empaqueta_scjn_reglamentos.py
    less scripts/scjn/reglamentos-release/MANIFEST.md   # read it. all of it.

    # first publish -- the release itself, plus the reverse index:
    cd scripts/scjn/reglamentos-release
    gh release create scjn-reglamentos MANIFEST.md SHA256SUMS.txt \\
        indice-global.json.gz --repo INGEOTEC/LegalIA \\
        --title "SCJN — reglamentos" --notes-file MANIFEST.md

    # then the ~1087 per-instrument tarballs, in batches:
    ls *.tgz | xargs -n 20 gh release upload scjn-reglamentos \\
        --repo INGEOTEC/LegalIA --clobber

## What goes in each tarball

    <id_ordenamiento>/<fecha>.md      the snapshots, with their provenance header
    <id_ordenamiento>/estado.json     when it was crawled, and its own
                                       categoria_ordenamiento/vigencia/materia/resumen

No `abrev` (this corpus has none, issue #220's own decision 3: the SCJN
reissues a reglamento as a brand-new `idOrdenamiento` rather than as a
reform of the previous one, so a title-derived key would collide), no
`indice.json`, no `notas/` -- nothing here depends on a DOF link that does
not exist for this collection yet.

## Updating one reglamento

``--instrumento ID`` (repeatable, by `id_ordenamiento`) rewrites only the
named instruments' tarballs and leaves every other `.tgz` already in
`--destino` exactly as it is -- the same incremental contract
`empaqueta_scjn_leyes.py --instrumento` has.

Needs at least one reglamento already seeded under
``<outdir>/reglamentos/`` (`discover_federal_reglamentos.py` +
`seed_federal_reglamentos.py`).
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

from scjn.catalog import reglamento_key  # noqa: E402
from scjn.header import versiones_de_directorio  # noqa: E402
from scjn.release import (  # noqa: E402
    ASSET_INDICE_GLOBAL,
    CAMPO_CATEGORIA_ORDENAMIENTO,
    CAMPOS_METADATOS,
    construye_indice_global_reglamentos,
)
from scjn.state import lee_estado  # noqa: E402

COLECCION = "reglamentos"


def _load_catalog(outdir: Path) -> list[dict]:
    """Every reglamento already seeded under ``<outdir>/reglamentos/`` --
    each one's own `estado.json`, whole. Unlike `empaqueta_scjn_leyes.py`,
    there is no published index to fall back to for `nombre`: this release
    does not exist until this very script's first run packages it."""
    base = outdir / COLECCION
    if not base.is_dir():
        raise SystemExit(
            f"{base} no existe -- corre primero discover_federal_reglamentos.py y "
            "seed_federal_reglamentos.py"
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
    """One reglamento's own row in the manifest -- the `reglamentos`
    sibling of `empaqueta_scjn_leyes.ResumenInstrumento`, without a linking
    percentage: this corpus has none to report (issue #220's own Scope)."""

    id_ordenamiento: str
    nombre: str
    total_snapshots: int
    asset: str | None = None
    bytes_comprimidos: int = 0
    #: This instrument's own `estado.json` -- `categoria_ordenamiento`,
    #: `vigencia`, `materia`, `resumen`, `rastreado`, whatever it has.
    estado: dict | None = None
    #: Whether this run rewrote the instrument's own tarball. False for the
    #: ones a `--instrumento` run left untouched and only re-listed.
    reempaquetado: bool = True


def resume_coleccion(outdir: Path) -> tuple[list[ResumenInstrumento], list[str]]:
    """Every `reglamentos` instrument in the catalogue, summarized for the
    manifest, plus the names of the ones never crawled at all -- the
    `reglamentos` sibling of `empaqueta_scjn_leyes.resume_coleccion`."""
    instrumentos = _load_catalog(outdir)
    resumenes = []
    nunca_rastreados = []
    for estado in instrumentos:
        clave = reglamento_key(estado)
        destino = outdir / COLECCION / clave
        versiones = versiones_de_directorio(destino) if destino.is_dir() else []
        if not versiones:
            nunca_rastreados.append(estado["nombre"])
            continue
        resumenes.append(
            ResumenInstrumento(
                id_ordenamiento=clave, nombre=estado["nombre"],
                total_snapshots=len(versiones), estado=estado,
            )
        )
    return resumenes, nunca_rastreados


def _archivos_instrumento(directorio: Path) -> list[Path]:
    """Every file worth shipping for one instrument: its snapshot `.md`
    files and its own `estado.json` -- no `indice.json`/`notas/` here at
    all (this corpus has no DOF linking, issue #220's own Scope). A hidden
    bookkeeping file (a `.progreso.json`-style checkpoint) is never
    shipped."""
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
    """Write one ``<id_ordenamiento>.tgz`` per instrument, byte-reproducibly
    -- the `reglamentos` sibling of `empaqueta_scjn_leyes.empaqueta`; see
    its own docstring for the reproducibility recipe (gzip stamped with
    mtime 0, members in sorted order, fixed ownership/mode), unchanged
    here. `solo` restricts the rewriting to the named `id_ordenamiento`
    keys, same incremental contract."""
    if not resumenes:
        raise SystemExit(f"{outdir / COLECCION} no tiene nada que empaquetar")

    for resumen in resumenes:
        directorio = outdir / COLECCION / resumen.id_ordenamiento
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
    destino: Path, resumenes: list[ResumenInstrumento], generado: str
) -> None:
    """Write `indice-global.json.gz` -- the `reglamentos` sibling of
    `empaqueta_scjn_leyes.escribe_indice_global`, with no `codNota` section
    and no published-index fallback (this release does not exist until
    this run publishes it, unlike `scjn-leyes` which a metadata backfill
    can patch after the fact)."""
    indice = construye_indice_global_reglamentos(
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


def _formatea_manifiesto(resumenes: list[ResumenInstrumento], nunca_rastreados: list[str]) -> str:
    ordenados = sorted(resumenes, key=lambda r: r.nombre)
    total_catalogo = len(resumenes) + len(nunca_rastreados)

    lineas = [
        "# SCJN — reglamentos: manifiesto de empaquetado",
        "",
        f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        f"{total_catalogo} instrumento(s) en el catálogo (`reglamentos`); "
        f"{len(resumenes)} ya rastreado(s) por la SCJN.",
        "",
        "Sin enlace a DOF (issue #220, Scope): no hay `indice.json` por instrumento "
        "ni sección `codNota` en el índice global -- el enlace se implementa en un "
        "issue futuro, para reglamentos y leyes por igual.",
        "",
    ]

    if nunca_rastreados:
        lineas.append(f"## Nunca rastreados ({len(nunca_rastreados)})")
        lineas.append("")
        lineas.extend(f"- {nombre}" for nombre in sorted(nunca_rastreados))
        lineas.append("")

    reempaquetados = [r for r in ordenados if r.reempaquetado]
    if len(reempaquetados) != len(ordenados):
        lineas.append(f"## Actualizados en esta corrida ({len(reempaquetados)})")
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
        "| instrumento | id_ordenamiento | snapshots | categoría | vigencia | "
        "asset | tamaño | rastreado |"
    )
    lineas.append("|---|---|---|---|---|---|---|---|")
    for r in ordenados:
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
        "--outdir", type=Path, default=Path("scripts/scjn"),
        help="donde fetch_scjn_legislacion.py --coleccion reglamentos ya escribio 'reglamentos'",
    )
    p.add_argument("--destino", type=Path, default=Path("scripts/scjn/reglamentos-release"))
    p.add_argument(
        "--instrumento", action="append", metavar="ID", dest="instrumentos",
        help="repetible; reescribe solo el .tgz de estos id_ordenamiento",
    )
    args = p.parse_args(argv)

    args.destino.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).isoformat(timespec="seconds")

    resumenes, nunca_rastreados = resume_coleccion(args.outdir)
    solo = set(args.instrumentos) if args.instrumentos else None
    if solo is not None:
        faltantes = solo - {r.id_ordenamiento for r in resumenes}
        if faltantes:
            raise SystemExit(
                f"{sorted(faltantes)} no tiene(n) snapshots en {args.outdir / COLECCION}"
            )
    empaqueta(args.outdir, args.destino, resumenes, solo=solo)
    escribe_indice_global(args.destino, resumenes, generado)

    manifiesto = args.destino / "MANIFEST.md"
    manifiesto.write_text(_formatea_manifiesto(resumenes, nunca_rastreados), encoding="utf-8")

    sumas = args.destino / "SHA256SUMS.txt"
    sumas.write_text(
        "".join(
            f"{sha256(args.destino / r.asset)}  {r.asset}\n"
            for r in sorted(resumenes, key=lambda r: r.id_ordenamiento)
        )
        + f"{sha256(args.destino / ASSET_INDICE_GLOBAL)}  {ASSET_INDICE_GLOBAL}\n",
        encoding="utf-8",
    )

    total = sum(r.bytes_comprimidos for r in resumenes)
    reescritos = sum(1 for r in resumenes if r.reempaquetado)
    if solo is not None:
        print(
            f"{reescritos} asset(s) .tgz reescrito(s): "
            f"{sorted(r.id_ordenamiento for r in resumenes if r.reempaquetado)}",
            file=sys.stderr,
        )
    print(
        f"{len(resumenes)} asset(s) .tgz, {_tamano(total)} en total; "
        f"{len(nunca_rastreados)} instrumento(s) sin rastrear (sin asset)",
        file=sys.stderr,
    )
    print(
        f"-> {args.destino}/  (<id_ordenamiento>.tgz, {ASSET_INDICE_GLOBAL}, MANIFEST.md, "
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
