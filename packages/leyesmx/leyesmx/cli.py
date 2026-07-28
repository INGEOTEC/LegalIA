"""Build a law's reform list: `python -m leyesmx --ley cpeum`."""

import argparse
import concurrent.futures as cf
import json
import sys
from pathlib import Path

from leyesmx import diputados, dof, normas


def lista_de_codnota(enlazadas) -> list[int | None]:
    """The reforms as a plain list of codNota, indexed by Diputados' numbering.

    Only the codNota is stored: everything else about a note — its title, its
    date, its issuing branch — is already in the dataset that
    `dofjson.titulos.download_titulos` builds, and is recovered by joining on
    codNota. Keeping a copy here would only let the two drift apart.

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


def todas_las_leyes(args, tweet_iterator) -> int:
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
    porf = dof.notas_por_fecha(tweet_iterator(str(args.titulos)), fechas)

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


def todos_los_reglamentos(args, tweet_iterator) -> int:
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
    porf = dof.notas_por_fecha(tweet_iterator(str(args.titulos)), fechas)

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


def todas_las_normas(args, tweet_iterator) -> int:
    """Build every Norma Oficial Mexicana's history from the DOF titles alone.

    Unlike the laws and regulations these need no second source: the NOM's code
    is in its own DOF title (see `normas`). One file holds the whole mapping
    rather than one file per NOM — there are 4,674 of them and each history is
    a handful of numbers, so thousands of tiny files would cost more than they
    tell.
    """
    destino_dir = args.out or Path("data/normas")
    grupos = normas.agrupa(tweet_iterator(str(args.titulos)))
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ley", default="cpeum",
                   help="LeyesBiblio abbreviation; 'todas' for every law in its "
                        "index, 'reglamentos' for every federal regulation, "
                        "'normas' for every Norma Oficial Mexicana "
                        "(default: cpeum)")
    p.add_argument("--titulos", type=Path, default=Path("titulos.jsonl.gz"),
                   help="dataset from dofjson.titulos.download_titulos")
    p.add_argument("--out", type=Path, default=None,
                   help="output JSON (default: data/reformas/<ley>.json)")
    p.add_argument("--decretos", type=Path, default=None, metavar="DIR",
                   help="download from Diputados the decrees the DOF cannot "
                        "serve, as a fallback route to the primary source")
    args = p.parse_args(argv)

    from microtc.utils import tweet_iterator

    if not args.titulos.exists():
        from dofjson.titulos import download_titulos
        print(f"descargando títulos del DOF -> {args.titulos}", file=sys.stderr)
        download_titulos(args.titulos, log=lambda *_: None)

    if args.ley == "todas":
        return todas_las_leyes(args, tweet_iterator)
    if args.ley == "reglamentos":
        return todos_los_reglamentos(args, tweet_iterator)
    if args.ley == "normas":
        return todas_las_normas(args, tweet_iterator)

    reformas = diputados.parse_reformas(diputados.descarga(args.ley), args.ley)
    enlazadas = dof.enlaza(reformas, tweet_iterator(str(args.titulos)))

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
