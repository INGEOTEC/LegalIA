import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from dof2md.batch import BatchConverter
from dof2md.converter import DEFAULT_TIMEOUT_SECONDS
from dof2md.downloader import build_url, download_pdf


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert a DOF (Mexico's official gazette) edition, or any local PDF or "
        "set of scanned page images, to Markdown."
    )
    parser.add_argument(
        "date", nargs="?", default=None,
        help="Edition date, format YYYY-MM-DD (e.g. 2010-01-05) — downloads that edition's "
        "PDF from the DOF site and converts it. Omit when using --pdf/--images instead.",
    )
    parser.add_argument(
        "--edition", choices=["MAT", "VES"], default="MAT",
        help="Edition: MAT (morning, default) or VES (evening) — matches the DOF site's own "
        "file naming. Only applies to a date.",
    )
    parser.add_argument(
        "--pdf", default=None,
        help="Convert this local PDF file instead of downloading one by date.",
    )
    parser.add_argument(
        "--images", nargs="+", default=None, metavar="PATH",
        help="Convert this ordered list of scanned page image files (one note spanning "
        "several pages) instead of downloading a PDF by date.",
    )
    parser.add_argument(
        "--filename", default=None,
        help="Output Markdown filename. Defaults to the date/edition, or the --pdf file's own "
        "name; required with --images, since there's no single input name to derive one from.",
    )
    parser.add_argument("--outdir", default="output", help="Output directory (default: output/)")
    parser.add_argument(
        "--titulo", default=None,
        help="Title of the note to keep — crops the converted Markdown down to just that note "
        "(the whole edition's Markdown is kept by default). Combine with --titulo-siguiente.",
    )
    parser.add_argument(
        "--titulo-siguiente", default=None,
        help="Title of the note right after --titulo, marking where the kept note ends",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.6,
        help="Minimum title-match confidence (0..1) required to apply a --titulo/--titulo-siguiente "
        "boundary; a weaker match falls back to keeping more text rather than dropping content "
        "(default: 0.6)",
    )
    parser.add_argument(
        "--keep-pages", action="store_true",
        help="With --titulo, also keep the uncropped Markdown as <outdir>/<pdf stem>.full.md",
    )
    parser.add_argument(
        "--keep-mineru-output", action="store_true",
        help="Keep mineru's raw output (layout/model JSON, rendered PDFs...) in "
        "<outdir>/<pdf stem>_mineru/ instead of discarding it",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    sources_given = sum(x is not None for x in (args.date, args.pdf, args.images))
    if sources_given != 1:
        sys.exit("Provide exactly one of: a date (to download a DOF edition), --pdf, or --images.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pdf_path = None

    if args.date is not None:
        try:
            date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            sys.exit(f"Invalid date: {args.date}. Use YYYY-MM-DD format.")

        url, filename = build_url(date, args.edition)
        pdf_path = outdir / filename
        md_filename = args.filename or pdf_path.with_suffix(".md").name

        print(f"Downloading: {url}")
        download_pdf(url, pdf_path)
        print(f"PDF saved to: {pdf_path}")
        path_or_paths = pdf_path
    elif args.pdf is not None:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            sys.exit(f"--pdf file not found: {pdf_path}")
        md_filename = args.filename or pdf_path.with_suffix(".md").name
        path_or_paths = pdf_path
    else:
        if args.filename is None:
            sys.exit("--filename is required with --images (there's no single input name to derive one from).")
        image_paths = [Path(p) for p in args.images]
        missing = [p for p in image_paths if not p.is_file()]
        if missing:
            sys.exit(f"--images file(s) not found: {', '.join(str(p) for p in missing)}")
        md_filename = args.filename
        path_or_paths = image_paths

    print("Converting to Markdown (mineru)...")
    try:
        with BatchConverter() as convert:
            md_path = convert(
                path_or_paths, outdir, md_filename, args.titulo, args.titulo_siguiente,
                min_confidence=args.min_confidence,
                keep_pages=args.keep_pages,
                keep_mineru_output=args.keep_mineru_output,
            )
    except subprocess.TimeoutExpired:
        hint = f" The partial PDF is at {pdf_path}." if args.date is not None else ""
        sys.exit(
            f"Conversion timed out after {DEFAULT_TIMEOUT_SECONDS}s. "
            f"This document may be unusually large.{hint}"
        )
    print(f"Markdown saved to: {md_path}")


if __name__ == "__main__":
    main()
