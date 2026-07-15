import argparse
import datetime as dt
import sys
from pathlib import Path

from dof2md.converter import convert_to_markdown
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
    md_path = pdf_path.with_suffix(".md")

    print(f"Downloading: {url}")
    download_pdf(url, pdf_path)
    print(f"PDF saved to: {pdf_path}")

    print("Converting to Markdown...")
    convert_to_markdown(pdf_path, md_path)
    print(f"Markdown saved to: {md_path}")


if __name__ == "__main__":
    main()
