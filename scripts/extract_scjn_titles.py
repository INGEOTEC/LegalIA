#!/usr/bin/env python3
"""Write the federal-law catalogue -- `nombre`, `abrev` and `actualizado` --
to ``<outdir>/leyes/catalogo.json``, from the SCJN and the DOF alone.

    ./scripts/extract_scjn_titles.py --outdir scripts/scjn
    ./scripts/extract_scjn_titles.py --outdir scripts/scjn --dof-only
    ./scripts/extract_scjn_titles.py --outdir scripts/scjn --discover

Issue #186 (Fase 1 of #184) cut the last dependency this pipeline had on the
Cámara de Diputados. Nothing here requests `diputados.gob.mx`, and the
catalogue's *shape* is unchanged, so `fetch_scjn_legislacion.py`,
`enlaza_scjn_legislacion.py` and `empaqueta_scjn_leyes.py` keep reading it
untouched. What changed is where each field comes from.

Only `leyes` exists now: reglamentos, tratados and Normas Oficiales Mexicanas
left the project with #184, so this script has no `--coleccion` flag and no
collection tuple, and `abrev` is always present (it used to be optional
because tratados had none).

`nombre` and `abrev`
    Read back off the `scjn-leyes` release, which has been publishing the
    seed all along as a by-product of the corpus:
    `nota2md.download_scjn_leyes_catalog` over `indice-global.json.gz`. No
    new release, no scraping.

    **Order.** The catalogue is written sorted by `slug_instrumento(entry)`
    — the `abrev` normalized, which is exactly the order
    `indice-global.json.gz` already has. Diputados' own index order is gone
    with its source and is not reconstructed. Anything positional therefore
    has to be re-derived: `fetch_scjn_legislacion.py`'s resume checkpoint
    (`.progreso.json`, a 1-based position in this file's order) is deleted by
    this script whenever the order it writes differs from the order of the
    file it is replacing, so a run interrupted under the old order can never
    resume into the wrong instrument.

    **`abrev` is never re-derived.** An `abrev` is the release's slug and
    asset name, so an existing one is carried over verbatim from the previous
    `catalogo.json` — including the 14 that contain an underscore
    (`lif_2026`, `pef_2026`, `reg_senado`, …) where the release's slug has a
    hyphen. That is why the previous catalogue is matched by
    `slug_instrumento` and not by `abrev`.

    **The previous catalogue is the floor.** A law that drops out of the
    release's index for any reason is kept and reported, never silently
    dropped: this file feeds the crawl that produces the corpus that
    publishes the index that feeds this file.

`actualizado`
    The ISO date of the law's own most recent reform, and the whole input to
    `nota2md.scjn.motivo_pendiente`'s "has this changed since we crawled it?".
    It is the **newest of two independent answers**, not a preference order:

    1. the newest `fecha_publicacion` in the SCJN's own reform table
       (`scjn_api.reformas_of_ordenamiento`), addressed by the
       `id_ordenamiento` the law's own `estado.json` already records, so a
       law already crawled costs one request and no search;
    2. the newest DOF legal provision whose title both names the law and
       opens with "DECRETO"/"LEY"
       (`nota2md.scjn.newest_dof_publication_dates`), off the
       `notas-archivo` cache the pipeline already populates — no network.

    Taking the newest is not hedging; each source has a failure mode the
    other does not, and both were measured against the 316-law catalogue
    while this was written:

    - **The DOF alone under-dates 91 of 316 laws.** An omnibus decree
      ("...por el que se reforman diversas disposiciones de diversos
      ordenamientos legales", DOF 2025-11-14 and 2024-04-01 among others)
      reforms dozens of laws without naming any of them in its title, so no
      title-based match can see it. `lic` came back 2022-03-11 against a real
      2025-11-14.
    - **The SCJN alone silently freezes a law it has not indexed yet.** Its
      reform table cannot report a reform it does not have, so an
      SCJN-derived `actualizado` never moves, the law is never reported
      pending, and the reform is missed even after the SCJN catches up. That
      is the `lfca` case issue #124 built the permanent retry for, and the
      DOF half is what keeps it working.

    Neither source yielding a date leaves `actualizado` **absent** — never a
    placeholder. Absent is `PENDIENTE_SIN_ACTUALIZADO`: "nothing can tell
    whether it changed", which the planner reports apart and a human decides
    on. Inventing a date would turn that into a silent "up to date".

    `--dof-only` skips step 1 and makes the whole run offline, at the cost of
    the 91 under-dated laws above. It is for a quick local refresh, not for a
    run whose plan anyone acts on.

`nombre_scjn`
    Unchanged: an optional manual override naming the exact string to search
    the SCJN with, added by hand to `catalogo.json` for the rare law whose
    own `nombre` the SCJN's full-text search never matches (`lisipl`, whose
    name carries a 250+ character trailing parenthetical alternate title).
    This script never sets it and always carries it forward
    (`nota2md.scjn.merge_catalog_overrides`).

`--discover`
    Reports federal laws the SCJN lists and this catalogue does not, and
    stops. It never writes them: a new federal law is a handful a year, and
    a wrong `abrev` renames a release asset and orphans it, so the decision
    is a human's. Each candidate is confirmed against the DOF the same way
    `actualizado` is (a title that names it and opens with DECRETO/LEY) —
    without that, the SCJN's own `CODIGO` category alone contributes ~180
    "CÓDIGO DE CONDUCTA DE ..." administrative documents that are not laws.
    A suggested `abrev` is printed with each candidate
    (`nota2md.scjn.mint_abrev`), to be written down once and never
    recomputed.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from dofjson.titulos import legal_provisions_titles  # noqa: E402
from nota2md import download_scjn_leyes_catalog  # noqa: E402
from nota2md.scjn import (  # noqa: E402
    apply_actualizado,
    lee_estado,
    merge_catalog_overrides,
    merge_catalog_with_previous,
    mint_abrev,
    newest_dof_publication_dates,
    slug_instrumento,
    slugify,
)
from nota2md.scjn_api import (  # noqa: E402
    ScjnApi,
    elige_ordenamiento,
    grupo_de_categoria,
)

#: The one collection left (#184). Kept as a name rather than inlined because
#: it is also the directory the whole pipeline lays its output out under.
COLECCION = "leyes"

#: `BusquedaFrase` has no "list everything" mode -- an empty `q` answers zero
#: results -- so `--discover` pages a phrase search per category of the leyes
#: group instead, using the category's own word as the phrase. Every federal
#: LEY/CÓDIGO/CONSTITUCIÓN has that word in its title by construction.
_FRASES_DESCUBRIMIENTO = (("ley", "LEY"), ("codigo", "CODIGO"), ("constitucion", "CONSTITUCION"))


def _iso(fecha: str | None) -> str | None:
    """`DD-MM-YYYY` (the SCJN reform table's own shape) as ISO `YYYY-MM-DD`."""
    if not fecha or len(fecha) != 10:
        return None
    return f"{fecha[6:10]}-{fecha[3:5]}-{fecha[0:2]}"


def seed_catalog(*, cache_dir=None, timeout: int = 60) -> list[dict]:
    """The catalogue's `nombre`/`abrev` as the `scjn-leyes` release publishes
    them. `freshness=False`: the release's `estado.json` records the
    `actualizado` a past crawl ran against, which is this script's own old
    output and would make the field a function of the crawl it schedules."""
    return download_scjn_leyes_catalog(
        freshness=False, cache_dir=cache_dir, timeout=timeout
    )


def scjn_dates(
    catalogo: list[dict], outdir: Path, *, api: ScjnApi | None = None, log=None
) -> dict[str, str]:
    """slug -> the newest `fecha_publicacion` the SCJN's reform table reports,
    for every law it answers for.

    `id_ordenamiento` comes from the law's own `estado.json` under `outdir`
    when it has been crawled; otherwise the law is searched by name first,
    through exactly the candidate selection the crawl uses
    (`elige_ordenamiento`), so this can never date a law off the wrong
    document. A law the SCJN does not answer for at all is simply absent —
    that is the `lfca` case, and the DOF half covers it.

    A failure on one law never aborts the catalogue over one law's metadata:
    it is warned about and skipped, the same posture the crawl itself takes.
    """
    api = api or ScjnApi()
    fechas: dict[str, str] = {}
    for entrada in catalogo:
        slug = slug_instrumento(entrada)
        id_ordenamiento = lee_estado(outdir / COLECCION / slug).get("id_ordenamiento")
        try:
            if id_ordenamiento is None:
                elegido = elige_ordenamiento(
                    api.search_ordenamiento(entrada.get("nombre_scjn") or entrada["nombre"]),
                    entrada["nombre"],
                )
                if elegido is None:
                    continue
                id_ordenamiento = elegido.idOrdenamiento
            reformas = api.reformas_of_ordenamiento(id_ordenamiento)
        except Exception as exc:
            if log:
                log(f"  warning: SCJN gave no reform table for {slug}: {exc}")
            continue
        publicadas = [_iso(r.fecha_publicacion) for r in reformas]
        publicadas = [f for f in publicadas if f]
        if publicadas:
            fechas[slug] = max(publicadas)
    return fechas


def dof_dates(catalogo: list[dict], *, cache_dir=None, log=None) -> dict[str, str]:
    """slug -> the newest DOF publication date whose title names the law and
    opens with DECRETO/LEY. One pass over the whole titles stream for the
    whole catalogue, not one lookup per law."""
    instrumentos = {slug_instrumento(e): e["nombre"] for e in catalogo}
    titulos = (
        legal_provisions_titles(log=(lambda *_a, **_k: None))
        if cache_dir is None
        else legal_provisions_titles(cache_dir, log=(lambda *_a, **_k: None))
    )
    if log:
        log(f"  leyendo titulos del DOF para {len(instrumentos)} ley(es)...")
    return newest_dof_publication_dates(instrumentos, titulos)


def discover(
    catalogo: list[dict], *, api: ScjnApi | None = None, cache_dir=None, log=print
) -> list[dict]:
    """Federal laws the SCJN lists that `catalogo` does not have, each
    confirmed against the DOF and carrying a suggested `abrev`.

    Never writes anything. See the module docstring for why the DOF
    confirmation is not optional."""
    api = api or ScjnApi()
    conocidos = {slug_instrumento(e) for e in catalogo}
    nombres_conocidos = {e["nombre"].strip().upper() for e in catalogo}

    hallados: dict[str, str] = {}
    for frase, categoria in _FRASES_DESCUBRIMIENTO:
        pagina = 1
        while True:
            lote = api.search_ordenamiento(
                frase, tamanio_pagina=100, categoria=categoria, ambito="FEDERAL",
                vigencia="VIGENTE", pagina=pagina,
            )
            for hit in lote:
                # `grupo_de_categoria` is the SCJN's own answer to "is this a
                # ley/código at all"; the filter is cheap insurance against
                # the category filter being ignored by the API.
                if grupo_de_categoria(hit.categoriaOrdenamiento) != "ley":
                    continue
                hallados[hit.ordenamiento.strip().upper()] = hit.idOrdenamiento
            if len(lote) < 100:
                break
            pagina += 1

    desconocidos = {n: i for n, i in hallados.items() if n not in nombres_conocidos}
    desconocidos = {
        n: i for n, i in desconocidos.items() if slugify(n) not in conocidos
    }
    if not desconocidos:
        return []

    log(f"  confirmando {len(desconocidos)} candidato(s) contra el DOF...")
    confirmadas = newest_dof_publication_dates(
        {n: n for n in desconocidos},
        legal_provisions_titles(log=(lambda *_a, **_k: None))
        if cache_dir is None
        else legal_provisions_titles(cache_dir, log=(lambda *_a, **_k: None)),
    )

    tomados = set(conocidos)
    candidatos = []
    for nombre in sorted(confirmadas):
        abrev = mint_abrev(nombre, tomados)
        tomados.add(abrev)
        candidatos.append({
            "nombre": nombre,
            "abrev": abrev,
            "actualizado": confirmadas[nombre],
            "idOrdenamiento": desconocidos[nombre],
        })
    return candidatos


def _borra_progreso_si_cambio_el_orden(
    destino: Path, anterior: list[dict] | None, catalogo: list[dict], log
) -> None:
    """Drop `.progreso.json` when this run reorders the catalogue.

    That checkpoint is a 1-based *position* in `catalogo.json`'s own order
    (`fetch_scjn_legislacion._lee_progreso`), so an interrupted run resuming
    against a reordered catalogue would skip laws it never attempted. Only
    the order matters here — a run that merely adds a law at the end is not
    affected, but detecting that separately buys nothing over re-sweeping."""
    if anterior is None:
        return
    if [slug_instrumento(e) for e in anterior] == [slug_instrumento(e) for e in catalogo]:
        return
    progreso = destino / ".progreso.json"
    if progreso.is_file():
        progreso.unlink()
        log(f"  el orden del catalogo cambio: {progreso} descartado")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument(
        "--dof-only",
        action="store_true",
        help="skip the SCJN reform table; offline, but under-dates the laws "
             "reformed only by omnibus decrees (see the module docstring)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="report federal laws the SCJN lists and the catalogue does not, "
             "then stop without writing anything",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    destino = args.outdir / COLECCION
    archivo_catalogo = destino / "catalogo.json"
    anterior = (
        json.loads(archivo_catalogo.read_text(encoding="utf-8"))
        if archivo_catalogo.is_file()
        else None
    )

    if args.discover:
        if anterior is None:
            raise SystemExit(f"{archivo_catalogo} no existe -- corre primero sin --discover")
        candidatos = discover(anterior, log=log)
        if not candidatos:
            log("descubrimiento: ninguna ley federal nueva confirmada contra el DOF")
            return 0
        log(f"descubrimiento: {len(candidatos)} candidato(s) -- NO se escribieron:")
        for c in candidatos:
            log(f"    {c['abrev']}  {c['actualizado']}  (idOrdenamiento {c['idOrdenamiento']})")
            log(f"        {c['nombre']}")
        log("  revisa cada uno y agregalo a mano a catalogo.json; el 'abrev' "
            "sugerido se escribe una vez y no se vuelve a calcular")
        return 0

    catalogo, faltantes = merge_catalog_with_previous(seed_catalog(), anterior)
    if faltantes:
        log(
            f"aviso: {len(faltantes)} ley(es) del catalogo anterior no estan en el "
            "indice del release; se conservan (el catalogo anterior es el piso):"
        )
        for entrada in faltantes:
            log(f"    {slug_instrumento(entrada)}  {entrada['nombre']}")

    fuentes = []
    if not args.dof_only:
        fuentes.append(scjn_dates(catalogo, args.outdir, log=log))
    fuentes.append(dof_dates(catalogo, log=log))
    catalogo = apply_actualizado(catalogo, *fuentes)
    catalogo = merge_catalog_overrides(catalogo, anterior)

    _borra_progreso_si_cambio_el_orden(destino, anterior, catalogo, log)
    destino.mkdir(parents=True, exist_ok=True)
    archivo_catalogo.write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sin_fecha = [e for e in catalogo if not e.get("actualizado")]
    log(
        f"{COLECCION}: {len(catalogo)} instrumento(s), {len(sin_fecha)} sin "
        f"'actualizado' -> {archivo_catalogo}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
