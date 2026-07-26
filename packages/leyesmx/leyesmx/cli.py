"""Build a law's reform table: `python -m leyesmx --ley cpeum`."""

import argparse
import csv
import sys
from pathlib import Path

from leyesmx import diputados, dof

CAMPOS = ["ley", "no", "fecha", "codNota", "confianza", "titulo_dof", "decreto_dip"]


def escribe_csv(enlazadas, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        for e in enlazadas:
            w.writerow({c: getattr(e, c) for c in CAMPOS})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ley", default="cpeum",
                   help="LeyesBiblio abbreviation (default: cpeum)")
    p.add_argument("--titulos", type=Path, default=Path("titulos.jsonl.gz"),
                   help="dataset from dofjson.titulos.download_titulos")
    p.add_argument("--out", type=Path, default=None,
                   help="output CSV (default: data/reformas/<ley>.csv)")
    args = p.parse_args(argv)

    from microtc.utils import tweet_iterator

    if not args.titulos.exists():
        from dofjson.titulos import download_titulos
        print(f"descargando títulos del DOF -> {args.titulos}", file=sys.stderr)
        download_titulos(args.titulos, log=lambda *_: None)

    reformas = diputados.parse_reformas(diputados.descarga(args.ley), args.ley)
    enlazadas = dof.enlaza(reformas, tweet_iterator(str(args.titulos)))

    destino = args.out or Path("data/reformas") / f"{args.ley}.csv"
    escribe_csv(enlazadas, destino)

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
