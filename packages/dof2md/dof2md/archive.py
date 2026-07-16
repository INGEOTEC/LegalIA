"""Downloads a single day's DOF edition(s), converts them to Markdown, and
records provenance (source URL, date, edition) in a manifest CSV.

Designed to be invoked once per day by a scheduled CI job — each run is
idempotent against the manifest, so re-running for an already-processed date
is a no-op. Also useful for ad-hoc backfilling of a specific date.
"""
import argparse
import csv
import datetime as dt
import subprocess
from pathlib import Path

from dof2md.converter import convert_to_markdown
from dof2md.downloader import build_url, download_pdf
from dof2md.mineru_server import MineruServer

EDITIONS = ("MAT", "VES")
MANIFEST_FIELDS = ["date", "edition", "source_url", "markdown_filename", "release_tag"]


def load_processed_keys(manifest_path: Path) -> set:
    if not manifest_path.exists():
        return set()
    with manifest_path.open(newline="", encoding="utf-8") as f:
        return {(row["date"], row["edition"]) for row in csv.DictReader(f)}


def append_manifest_row(manifest_path: Path, row: dict) -> None:
    is_new = not manifest_path.exists()
    with manifest_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def process_edition(date: dt.date, edition: str, outdir: Path) -> dict | None:
    """Downloads and converts one edition. Returns its manifest row, or None
    if that edition doesn't exist for this date (e.g. weekends/holidays, or
    no evening edition), or if conversion timed out. A timed-out edition is
    never recorded in the manifest, so it's automatically retried the next
    time this date is processed rather than blocking the rest of the batch."""
    url, filename = build_url(date, edition)
    pdf_path = outdir / filename
    md_path = pdf_path.with_suffix(".md")

    try:
        download_pdf(url, pdf_path)
    except ValueError:
        print(f"No {edition} edition for {date}")
        return None

    try:
        convert_to_markdown(pdf_path, md_path)
    except subprocess.TimeoutExpired:
        print(f"Conversion timed out for {date} {edition}, skipping (will retry on next run)")
        return None

    return {
        "date": date.isoformat(),
        "edition": edition,
        "source_url": url,
        "markdown_filename": md_path.name,
        "release_tag": f"dof-{date.year}",
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to process, YYYY-MM-DD (default: today)")
    parser.add_argument("--outdir", default="dof-output", help="Where to write PDFs/Markdown")
    parser.add_argument("--manifest", default="manifest.csv", help="Path to the manifest CSV")
    parser.add_argument(
        "--produced-list", default="produced_files.txt",
        help="File to write the list of newly produced Markdown paths (for a CI upload step)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest)

    processed = load_processed_keys(manifest_path)
    produced_files = []

    with MineruServer():
        for edition in EDITIONS:
            key = (date.isoformat(), edition)
            if key in processed:
                print(f"Skipping {date} {edition}: already in manifest")
                continue

            print(f"Processing {date} {edition}...")
            row = process_edition(date, edition, outdir)
            if row is None:
                continue

            append_manifest_row(manifest_path, row)
            produced_files.append(str(outdir / row["markdown_filename"]))
            print(f"Done: {row['markdown_filename']}")

    Path(args.produced_list).write_text("\n".join(produced_files), encoding="utf-8")
    if not produced_files:
        print(f"No editions produced for {date}.")


if __name__ == "__main__":
    main()
