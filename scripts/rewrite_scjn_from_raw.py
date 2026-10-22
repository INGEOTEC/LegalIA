#!/usr/bin/env python3
"""Rewrite every snapshot of one SCJN corpus from the raw API answers a
re-crawl kept (review fix-2 of #291), with no request to the SCJN.

`recrawl_scjn_outline.py` saves what the API answered for each reform it
fetched under ``<outdir>/raw-api/<coleccion>/<key>/<snapshot>.json.gz``. When
the writer changes after that (a marker attribute, a classification rule),
this script writes each snapshot again from those files: the provenance
header already on disk is kept verbatim, the body is whatever
`scjn.api.articulos_a_markdown(..., outline=True)` now makes of the raw
blocks. A snapshot with no raw file is reported and left alone.

    ./scripts/rewrite_scjn_from_raw.py --coleccion tratados
"""

import argparse
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import articulos_a_markdown, articulos_de_crudo  # noqa: E402


def rewrite(base: Path, raw_base: Path) -> tuple[int, int, int]:
    """``(rewritten, unchanged, without raw)`` over every snapshot under `base`."""
    rewritten = unchanged = missing = 0
    for md in sorted(base.glob("*/*.md")):
        raw = raw_base / md.parent.name / f"{md.stem}.json.gz"
        if not raw.exists():
            missing += 1
            continue
        header, _, _body = md.read_text(encoding="utf-8").partition("\n\n")
        new = f"{header}\n\n{articulos_a_markdown(articulos_de_crudo(raw), outline=True)}"
        if md.read_text(encoding="utf-8") == new:
            unchanged += 1
            continue
        tmp = md.with_name(md.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(md)
        rewritten += 1
    return rewritten, unchanged, missing


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--coleccion", required=True)
    p.add_argument("--outdir", type=Path, default=Path("scripts/scjn"))
    p.add_argument("--raw-dir", type=Path, default=None)
    args = p.parse_args(argv)
    raw_base = (args.raw_dir or args.outdir / "raw-api") / args.coleccion
    rewritten, unchanged, missing = rewrite(args.outdir / args.coleccion, raw_base)
    print(f"{args.coleccion}: {rewritten} rewritten, {unchanged} unchanged, {missing} with no raw file",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
