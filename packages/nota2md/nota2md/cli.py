import argparse
import json
from pathlib import Path

from nota2md.builder import build_nota_markdown


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the Markdown of a single DOF (Mexico's official gazette) "
        "note by its codNota, from its HTML content or by OCR'ing its scanned page(s)."
    )
    parser.add_argument("cod_nota", type=int, help="The note's codNota")
    parser.add_argument(
        "--source", choices=["auto", "html", "image", "pdf"], default="auto",
        help="Where to build the Markdown from: 'auto' (HTML when the note has it, "
        "otherwise its scanned page images), 'html', 'image', or 'pdf' (OCR the "
        "note's own PDF, sliced from the edition) (default: auto)",
    )
    parser.add_argument(
        "--notas", metavar="PATH",
        help="Path to a saved get_notas() JSON (e.g. from `dofjson DATE`) to source "
        "the next note's title from, instead of fetching it (image path only)",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.6,
        help="Minimum title-match confidence (0..1) before a cut boundary is applied "
        "on the image path; below it, more text is kept rather than less (default: 0.6)",
    )
    parser.add_argument(
        "--keep-pages", action="store_true",
        help="Also keep the uncut, full-page OCR output as nota-<codNota>.full.md",
    )
    parser.add_argument("--outdir", default="output", help="Output directory (default: output/)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    notas_del_dia = None
    if args.notas:
        notas_del_dia = json.loads(Path(args.notas).read_text(encoding="utf-8"))

    dest = build_nota_markdown(
        args.cod_nota,
        Path(args.outdir),
        source=args.source,
        notas_del_dia=notas_del_dia,
        min_confidence=args.min_confidence,
        keep_pages=args.keep_pages,
    )
    print(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
