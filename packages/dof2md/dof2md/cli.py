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
        description="Download a DOF (Mexico's official gazette) edition as PDF and convert it to Markdown."
    )
    parser.add_argument("date", help="Edition date, format YYYY-MM-DD (e.g. 2010-01-05)")
    parser.add_argument(
        "--edition", choices=["MAT", "VES"], default="MAT",
        help="Edition: MAT (morning, default) or VES (evening) — matches the DOF site's own file naming",
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
    try:
        date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Invalid date: {args.date}. Use YYYY-MM-DD format.")

    url, filename = build_url(date, args.edition)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pdf_path = outdir / filename
    md_filename = pdf_path.with_suffix(".md").name

    print(f"Downloading: {url}")
    download_pdf(url, pdf_path)
    print(f"PDF saved to: {pdf_path}")

    print("Converting to Markdown (mineru)...")
    try:
        with BatchConverter() as convert:
            md_path = convert(
                pdf_path, outdir, md_filename, args.titulo, args.titulo_siguiente,
                min_confidence=args.min_confidence,
                keep_pages=args.keep_pages,
                keep_mineru_output=args.keep_mineru_output,
            )
    except subprocess.TimeoutExpired:
        sys.exit(
            f"Conversion timed out after {DEFAULT_TIMEOUT_SECONDS}s. "
            f"This edition may be unusually large; the partial PDF is at {pdf_path}."
        )
    print(f"Markdown saved to: {md_path}")


if __name__ == "__main__":
    main()
