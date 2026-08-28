import argparse
import datetime as dt
import json
from pathlib import Path

from nota2md import cache
from nota2md.builder import legal_provisions


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the Markdown of a single legal provision by its codNota: "
        "the SCJN's consolidated text of the law at that reform when the corpus "
        "covers it, otherwise the DOF (Mexico's official gazette) — its HTML content "
        "or OCR of its scanned page(s)."
    )
    parser.add_argument("cod_nota", type=int, help="The note's codNota")
    parser.add_argument(
        "--fecha", type=dt.date.fromisoformat, default=None, metavar="YYYY-MM-DD",
        help="The note's publication date. Some codigos (seen from 1999-2000) the "
        "DOF website only resolves when given alongside their own date (issue "
        "#109/#111) — pass it when it is already known.",
    )
    parser.add_argument(
        "--source", choices=["auto", "dof", "html", "image", "pdf"], default="auto",
        help="Where to build the Markdown from: 'auto' (the SCJN's consolidated text "
        "of the whole law at that reform when the scjn-leyes release covers this "
        "codNota, otherwise the DOF), 'dof' (skip the SCJN and go to the original "
        "source), 'html', 'image', or 'pdf' (OCR the note's own PDF, sliced from the "
        "edition) (default: auto)",
    )
    parser.add_argument(
        "--instrumento", default=None, metavar="SLUG",
        help="Which law is meant, by its slug, when one decree reformed several at "
        "once — the SCJN path refuses to guess (issue #117)",
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory the scjn-leyes release assets are cached in (see "
        "nota2md.cache). Not given: uses nota2md.cache.CACHE_DIR (the "
        "OS-appropriate default, overridable with $NOTA2MD_CACHE_DIR). "
        "'none': always download, skipping the cache entirely.",
    )
    parser.add_argument(
        "--refrescar", action="store_true",
        help="Re-download the release assets the SCJN path needs even if they are "
        "already cached (they are matched by name and never revalidated)",
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
    parser.add_argument(
        "--keep-mineru-output", action="store_true",
        help="Also keep mineru's own raw output (layout/model JSON, rendered PDFs...) "
        "under nota-<codNota>_mineru/, instead of discarding it (image/pdf paths only)",
    )
    parser.add_argument("--outdir", default="output", help="Output directory (default: output/)")
    return parser.parse_args(argv)


def _resolver_cache_dir(valor: str | None):
    """--cache-dir's value as a legal_provisions()-ready argument: not given
    at all -> cache.SIN_CACHE_DIR (its own default, cache.CACHE_DIR); 'none'
    -> None (skip the cache entirely); anything else -> that path. Same
    convention as `dofjson.cli`, deliberately."""
    if valor is None:
        return cache.SIN_CACHE_DIR
    if valor.lower() == "none":
        return None
    return Path(valor)


def main(argv=None):
    args = parse_args(argv)

    notas_del_dia = None
    if args.notas:
        notas_del_dia = json.loads(Path(args.notas).read_text(encoding="utf-8"))

    dest = legal_provisions(
        args.cod_nota,
        Path(args.outdir),
        source=args.source,
        fecha=args.fecha,
        notas_del_dia=notas_del_dia,
        min_confidence=args.min_confidence,
        keep_pages=args.keep_pages,
        keep_mineru_output=args.keep_mineru_output,
        instrumento=args.instrumento,
        cache_dir=_resolver_cache_dir(args.cache_dir),
        refrescar=args.refrescar,
    )
    print(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
