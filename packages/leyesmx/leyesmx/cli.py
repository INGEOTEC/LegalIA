"""Build a law's reform list: `python -m leyesmx --ley cpeum`."""

import argparse
import concurrent.futures as cf
import json
import sys
from pathlib import Path

from dofjson.titulos import SIN_CACHE_DIR, legal_provisions_titles
from leyesmx import diputados, dof, normas, tratados


def _titulos(args):
    """The DOF titles as a fresh stream (issue #166).

    Called once per pass: the stream is not re-iterable — every pass
    re-reads the notas-archivo cache — so a caller that needs the titles
    twice calls this again rather than keeping the generator around.
    """
    return legal_provisions_titles(
        _resolver_cache_dir(args.cache_dir), log=lambda *_: None
    )


def _resolver_cache_dir(valor: str | None):
    """--cache-dir's value, resolved the way dofjson's own CLI resolves it:
    not given -> SIN_CACHE_DIR (dofjson.titulos.CACHE_DIR); 'none' -> None
    (download into memory, nothing on disk); anything else -> that path."""
    if valor is None:
        return SIN_CACHE_DIR
    if valor.lower() == "none":
        return None
    return Path(valor)


def lista_de_codnota(enlazadas) -> list[int | None]:
    """The reforms as a plain list of codNota, indexed by Diputados' numbering.

    Only the codNota is stored: everything else about a note — its title, its
    date, its issuing branch — is already in the dataset that
    `dofjson.titulos.legal_provisions_titles` streams, and is recovered by
    joining on codNota. Keeping a copy here would only let the two drift apart.

    **Index N is reform N**, and index 0 is the law's original publication.
    Each entry is placed by its number rather than by position, so the
    invariant holds even where it otherwise would not: a few laws (`ccf`,
    `ccom`) have no original publication on their page, and a reform whose
    note is absent from the DOF stays in place as `null` instead of shifting
    everything after it. A gap left visible in the data beats one that only
    shows up in prose.
    """
    maximo = max((e.no for e in enlazadas if e.no is not None), default=0)
    lista: list[int | None] = [None] * (maximo + 1)
    for e in enlazadas:
        lista[e.no if e.no is not None else 0] = e.codNota
    return lista


def escribe_json(enlazadas, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump(lista_de_codnota(enlazadas), fh, indent=1)
        fh.write("\n")


def todas_las_leyes(args) -> int:
    """Build the reform list of every law in LeyesBiblio's index.

    The reform pages are fetched first and the DOF dataset is grouped once,
    over the union of every law's dates: grouping is a full pass over ~1.2
    million notes, and doing it per law would cost 316 of them.
    """
    destino_dir = args.out or Path("data/reformas")
    leyes = diputados.lista_leyes(diputados.descarga_indice())
    print(f"leyes en el índice de LeyesBiblio: {len(leyes)}", file=sys.stderr)

    reformas_por_ley, fallidas = {}, []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futuros = {
            ex.submit(lambda l=l: diputados.parse_reformas(
                diputados.descarga(l.abrev), l.abrev, l.nombre)): l
            for l in leyes
        }
        for fut in cf.as_completed(futuros):
            ley = futuros[fut]
            try:
                reformas_por_ley[ley.abrev] = fut.result()
            except Exception as exc:
                fallidas.append((ley, f"{type(exc).__name__}: {exc}"))

    fechas = {r.fecha for rs in reformas_por_ley.values() for r in rs}
    print(f"agrupando el DOF por fecha ({len(fechas)} fechas distintas)…",
          file=sys.stderr)
    porf = dof.notas_por_fecha(_titulos(args), fechas)

    catalogo, totales = [], {"reformas": 0, "con_nota": 0, "exactas": 0}
    for ley in leyes:
        reformas = reformas_por_ley.get(ley.abrev)
        if reformas is None:
            continue
        enlazadas = dof.enlaza_agrupadas(reformas, porf)
        escribe_json(enlazadas, destino_dir / f"{ley.abrev}.json")
        con = sum(e.enlazada for e in enlazadas)
        totales["reformas"] += len(enlazadas)
        totales["con_nota"] += con
        totales["exactas"] += sum(e.confianza >= 0.99 for e in enlazadas)
        catalogo.append({"no": ley.no, "abrev": ley.abrev, "nombre": ley.nombre,
                         "reformas": len(enlazadas), "conNota": con})

    indice = destino_dir / "leyes.json"
    indice.parent.mkdir(parents=True, exist_ok=True)
    with open(indice, "w", encoding="utf-8") as fh:
        json.dump(catalogo, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    print(f"\n{len(catalogo)} leyes -> {destino_dir}/  (índice en {indice})")
    print(f"{totales['reformas']} reformas | {totales['con_nota']} con codNota "
          f"({totales['exactas']} coincidencia exacta)")
    sin = totales["reformas"] - totales["con_nota"]
    if sin:
        print(f"{sin} sin nota en el DOF")
    for ley, err in fallidas:
        print(f"  falló {ley.abrev} ({ley.nombre[:50]}): {err}", file=sys.stderr)
    return 1 if fallidas else 0


def todos_los_reglamentos(args) -> int:
    """Build the reform list of every federal regulation LeyesBiblio lists.

    Regulations go in their own subdirectory: their identifiers come from
    Diputados' file names (`reg_ladua`) and the laws' from its index
    (`reg_senado`), so nothing stops the two from colliding one day.
    """
    destino_dir = args.out or Path("data/reformas") / "reglamentos"
    reglamentos = diputados.parse_reglamentos(diputados.descarga_reglamentos())
    print(f"reglamentos en LeyesBiblio: {len(reglamentos)}", file=sys.stderr)

    desacuerdos = diputados.numeracion_declarada(reglamentos)
    if desacuerdos:
        print(f"aviso: {len(desacuerdos)} reforma(s) cuyo número declarado no "
              f"coincide con su posición cronológica: {desacuerdos[:5]}",
              file=sys.stderr)

    fechas = {r.fecha for reg in reglamentos for r in reg.reformas}
    print(f"agrupando el DOF por fecha ({len(fechas)} fechas distintas)…",
          file=sys.stderr)
    porf = dof.notas_por_fecha(_titulos(args), fechas)

    catalogo, totales = [], {"reformas": 0, "con_nota": 0, "exactas": 0}
    for reg in reglamentos:
        enlazadas = dof.enlaza_agrupadas(reg.reformas, porf, por_nombre=True)
        escribe_json(enlazadas, destino_dir / f"{reg.abrev}.json")
        con = sum(e.enlazada for e in enlazadas)
        totales["reformas"] += len(enlazadas)
        totales["con_nota"] += con
        totales["exactas"] += sum(e.confianza >= 0.99 for e in enlazadas)
        catalogo.append({"no": reg.no, "abrev": reg.abrev, "nombre": reg.nombre,
                         "reformas": len(enlazadas), "conNota": con})

    indice = destino_dir / "reglamentos.json"
    indice.parent.mkdir(parents=True, exist_ok=True)
    with open(indice, "w", encoding="utf-8") as fh:
        json.dump(catalogo, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    print(f"\n{len(catalogo)} reglamentos -> {destino_dir}/  (índice en {indice})")
    print(f"{totales['reformas']} entradas | {totales['con_nota']} con codNota "
          f"({totales['exactas']} coincidencia exacta)")
    sin = totales["reformas"] - totales["con_nota"]
    if sin:
        print(f"{sin} sin nota en el DOF")
    return 0


def _escribe(datos, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=1, sort_keys=isinstance(datos, dict))
        fh.write("\n")


def todas_las_normas(args) -> int:
    """Build every Norma Oficial Mexicana's history from the DOF titles alone.

    Unlike the laws and regulations these need no second source: the NOM's code
    is in its own DOF title (see `normas`). One file holds the whole mapping
    rather than one file per NOM — there are 4,674 of them and each history is
    a handful of numbers, so thousands of tiny files would cost more than they
    tell.
    """
    destino_dir = args.out or Path("data/normas")
    grupos = normas.agrupa(_titulos(args))
    instrumentos, ambiguas = normas.resuelve_citas_parciales(grupos)

    _escribe({c: normas.historia(v) for c, v in instrumentos.items()},
             destino_dir / "noms.json")
    _escribe(normas.catalogo(instrumentos), destino_dir / "catalogo.json")
    # Kept rather than dropped: these notes do concern a NOM, only which one
    # cannot be told from a code cited too short to identify it.
    _escribe({c: normas.historia(v) for c, v in ambiguas.items()},
             destino_dir / "citas-ambiguas.json")

    notas_inst = sum(len(v) for v in instrumentos.values())
    print(f"\n{len(instrumentos)} normas -> {destino_dir}/noms.json "
          f"(catálogo en {destino_dir}/catalogo.json)")
    print(f"{notas_inst} notas del DOF | "
          f"{sum(1 for v in instrumentos.values() if len(v) >= 2)} normas con más de una")
    if ambiguas:
        print(f"{len(ambiguas)} código(s) citados demasiado cortos para "
              f"identificar una norma, con {sum(len(v) for v in ambiguas.values())} "
              f"nota(s) -> {destino_dir}/citas-ambiguas.json")
    return 0


def todos_los_tratados(args) -> int:
    """Build every international treaty's history from the DOF titles alone.

    Neither LeyesBiblio nor the SRE's register can serve as the spine (see
    `tratados`), so the gazette is read directly. A treaty has no code, only a
    name, so the two decrees that make it up are matched rather than compared
    verbatim — and where they cannot be matched the treaty keeps its single
    note, which for most older ones is the truth rather than a miss.
    """
    destino_dir = args.out or Path("data/tratados")
    decretos = tratados.decretos(_titulos(args))
    print(f"decretos de tratado en el DOF: {len(decretos)}", file=sys.stderr)

    grupos = tratados.empareja(decretos)
    _escribe([tratados.historia(g) for g in grupos], destino_dir / "tratados.json")
    _escribe(tratados.catalogo(grupos), destino_dir / "catalogo.json")

    exactas = sum(1 for g in grupos if g["certeza"] == "exacta")
    pares = sum(1 for g in grupos if isinstance(g["certeza"], float))
    sueltos = sum(1 for g in grupos if g["certeza"] is None)
    print(f"\n{len(grupos)} tratados -> {destino_dir}/tratados.json "
          f"(catálogo en {destino_dir}/catalogo.json)")
    print(f"{sum(len(g['notas']) for g in grupos)} decretos | "
          f"{exactas} con los dos decretos por nombre idéntico, {pares} emparejados, "
          f"{sueltos} con un solo decreto")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ley", default="cpeum",
                   help="LeyesBiblio abbreviation; 'todas' for every law in its "
                        "index, 'reglamentos' for every federal regulation, "
                        "'normas' for every Norma Oficial Mexicana, "
                        "'tratados' for every international treaty "
                        "(default: cpeum)")
    p.add_argument("--cache-dir", default=None, metavar="DIR",
                   help="directory holding the notas-archivo .tgz assets the DOF "
                        "titles are streamed from (populate it with `nota2md "
                        "download gazette-metadata`). Not given: "
                        "dofjson.titulos.CACHE_DIR; 'none': download them into "
                        "memory instead of reading disk")
    p.add_argument("--out", type=Path, default=None,
                   help="output JSON (default: data/reformas/<ley>.json)")
    p.add_argument("--decretos", type=Path, default=None, metavar="DIR",
                   help="download from Diputados the decrees the DOF cannot "
                        "serve, as a fallback route to the primary source")
    args = p.parse_args(argv)

    if args.ley == "todas":
        return todas_las_leyes(args)
    if args.ley == "reglamentos":
        return todos_los_reglamentos(args)
    if args.ley == "normas":
        return todas_las_normas(args)
    if args.ley == "tratados":
        return todos_los_tratados(args)

    reformas = diputados.parse_reformas(diputados.descarga(args.ley), args.ley)
    enlazadas = dof.enlaza(reformas, _titulos(args))

    destino = args.out or Path("data/reformas") / f"{args.ley}.json"
    escribe_json(enlazadas, destino)

    if args.decretos:
        faltantes = [r for r, e in zip(reformas, enlazadas) if not e.enlazada]
        for r in faltantes:
            destino_pdf = args.decretos / f"{args.ley}_ref_{r.no}_{r.fecha}.pdf"
            diputados.descarga_decreto(r, destino_pdf)
            print(f"  decreto {r.no} ({r.fecha}) desde Diputados -> {destino_pdf}",
                  file=sys.stderr)
        if not faltantes:
            print("  el DOF tiene todas las notas; nada que respaldar",
                  file=sys.stderr)

    con = sum(e.enlazada for e in enlazadas)
    exactas = sum(e.confianza >= 0.99 for e in enlazadas)
    print(f"{args.ley}: {len(enlazadas)} reformas | {con} con codNota "
          f"({exactas} coincidencia exacta) -> {destino}")
    for e in enlazadas:
        if not e.enlazada:
            print(f"  sin nota en el DOF: {e.fecha} (reforma {e.no})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
