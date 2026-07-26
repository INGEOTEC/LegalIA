"""Build a law's reform list: `python -m leyesmx --ley cpeum`."""

import argparse
import json
import sys
from pathlib import Path

from leyesmx import diputados, dof


def escribe_json(enlazadas, destino: Path) -> None:
    """The reforms as a plain list of codNota, oldest first.

    Only the codNota is stored: everything else about a note — its title, its
    date, its issuing branch — is already in the dataset that
    `dofjson.titulos.download_titulos` builds, and is recovered by joining on
    codNota. Keeping a copy here would only let the two drift apart.

    A reform whose note is absent from the DOF is written as `null` rather
    than skipped. That keeps the list aligned with Diputados' own numbering —
    index 0 is the law's original publication and index N is reform N — and
    leaves the gap visible in the data instead of only in prose.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump([e.codNota for e in enlazadas], fh, indent=1)
        fh.write("\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ley", default="cpeum",
                   help="LeyesBiblio abbreviation (default: cpeum)")
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
