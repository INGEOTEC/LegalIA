"""`nota2md`'s command line.

Two verbs, one of them implicit:

    nota2md <codNota> [...]              build one legal provision's Markdown
    nota2md download federal-laws        put the scjn-leyes release on disk
    nota2md download gazette-metadata    put the notas-archivo release on disk
    nota2md download all                 both of them

The build form is written without a verb on purpose: it is the command this
CLI had before `download` existed (`nota2md 5793655 --outdir output`, as the
README and the website document it), and breaking it to gain a subcommand
layer would buy nothing. Anything whose first argument is not a known
subcommand is therefore parsed as the build form — see `parse_args`.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from nota2md import cache
from nota2md.builder import legal_provisions

#: Verbs that shadow the implicit build form. A `codNota` can never collide
#: with one of these (they are not digits), so the dispatch in `parse_args`
#: is unambiguous.
SUBCOMANDOS = ("download",)


def _agrega_argumentos_build(parser):
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
        help="Directory this note's own Markdown is cached under (see "
        "nota2md.cache); also where the SCJN path's scjn-leyes tarball is read "
        "from, when this is given explicitly. Not given: nota2md's own output "
        "still uses nota2md.cache.CACHE_DIR, while the SCJN path reads "
        "scjn.cache.CACHE_DIR instead (a separate directory, issue #209) -- "
        "run `scjn download` (or `nota2md download federal-laws`) to "
        "populate it ahead of time. 'none': skip nota2md's own cache "
        "(download the DOF path fresh every time); no longer means "
        "'skip the SCJN's cache too' -- the SCJN path is disk-first and never "
        "downloads on demand any more.",
    )
    parser.add_argument(
        "--refrescar", action="store_true",
        help="Re-extract the SCJN path's already-cached snapshot into this note's "
        "own Markdown cache even if one is already there; no longer re-downloads "
        "anything (issue #209 made every scjn.release reader disk-only)",
    )
    parser.add_argument(
        "--notas", metavar="PATH",
        help="Path to a saved day's legal provisions as JSON (e.g. from `dofjson DATE`) "
        "to source the next note's title from, instead of fetching it (image path only). "
        "Either shape: get_notas()'s per-edition dict or fetch_daily_legal_provisions()'s "
        "flat list (issue #180)",
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
    parser.add_argument(
        "--outdir", default=None,
        help="Output directory. Omitted, the Markdown is written into nota2md's "
        "own cache (scjn-leyes/md/ or dof/, see --cache-dir) and its path "
        "printed — the caller does not have to pick a directory to get a note",
    )
    return parser


_DESCRIPCION_BUILD = (
    "Build the Markdown of a single legal provision by its codNota: "
    "the SCJN's consolidated text of the law at that reform when the corpus "
    "covers it, otherwise the DOF (Mexico's official gazette) — its HTML content "
    "or OCR of its scanned page(s)."
)


def _parser_build():
    """The verbless form, `nota2md <codNota> [...]`, on its own — what argv
    is parsed with when it does not start with a subcommand."""
    parser = argparse.ArgumentParser(prog="nota2md", description=_DESCRIPCION_BUILD)
    return _agrega_argumentos_build(parser)


def _parser_completo():
    """Every form the CLI accepts, for `--help` and for parsing a subcommand
    invocation. The build form appears here too (as the `build` verb) so
    `nota2md --help` documents it rather than only listing `download`."""
    parser = argparse.ArgumentParser(
        prog="nota2md",
        description=_DESCRIPCION_BUILD
        + " Called with a codNota and no verb — `nota2md 5793655` — that is what "
        "runs; `nota2md download ...` instead puts the GitHub releases this "
        "project reads on disk.",
    )
    sub = parser.add_subparsers(dest="comando")

    _agrega_argumentos_build(
        sub.add_parser(
            "build",
            help="Build one legal provision's Markdown (the default: the verb may "
            "be left out entirely)",
            description=_DESCRIPCION_BUILD,
        )
    )

    descarga = sub.add_parser(
        "download",
        help="Download the GitHub releases this project reads into the per-user "
        "cache directory",
        description="Download the GitHub releases this project reads into the "
        "per-user cache directory platformdirs picks, so no script has to be "
        "written first. Assets already on disk are matched by name and never "
        "revalidated, so a second run costs no download at all — pass --refrescar "
        "to force one.",
    )
    releases = descarga.add_subparsers(dest="release")

    leyes = releases.add_parser(
        "federal-laws",
        help="The scjn-leyes release: the SCJN's consolidated text of every "
        "federal law at each of its reforms",
        description="Download the scjn-leyes release — the reverse index plus one "
        "tarball per law (~380 MB for all of them) — delegating to the scjn "
        "package's own downloader (`scjn download`), into scjn's own cache "
        "directory. This is what the SCJN path of `nota2md <codNota>` reads.",
    )
    leyes.add_argument(
        "--slug", action="append", default=None, metavar="SLUG", dest="slugs",
        help="Only this law's tarball, by slug (repeatable). Not given: every law "
        "the release publishes. The reverse index is always downloaded.",
    )
    leyes.add_argument(
        "--cache-dir", default=None,
        help="Directory to write into. Not given: scjn.cache.CACHE_DIR (the "
        "OS-appropriate per-user cache, overridable with $SCJN_CACHE_DIR) -- "
        "not nota2md's own cache directory, since the scjn-leyes release "
        "moved to the scjn package's cache (issue #209).",
    )
    leyes.add_argument(
        "--refrescar", action="store_true",
        help="Re-download assets already present instead of keeping them",
    )

    gaceta = releases.add_parser(
        "gazette-metadata",
        help="The notas-archivo release: every legal provision ever published, "
        "as DOF metadata (written to DOFJSON's cache, not nota2md's)",
        description="Download the notas-archivo release — one tarball per "
        "year/month of the gazette — into DOFJSON'S OWN cache directory "
        "(dofjson.titulos.CACHE_DIR: ~/.cache/dofjson on Linux, "
        "~/Library/Caches/dofjson on macOS, %LOCALAPPDATA%\\dofjson\\Cache on "
        "Windows), NOT nota2md's. The two releases have two lifecycles "
        "and deliberately do not share a directory, so --cache-dir here names a "
        "dofjson directory and has nothing to do with --cache-dir on the other "
        "subcommands.",
    )
    gaceta.add_argument(
        "--cache-dir", default=None,
        help="Directory to write into. Not given: dofjson.titulos.CACHE_DIR — "
        "dofjson's cache, not nota2md's (see this subcommand's description).",
    )
    gaceta.add_argument(
        "--refrescar", action="store_true",
        help="Re-download assets already present instead of keeping them",
    )

    todo = releases.add_parser(
        "all",
        help="Both releases, each into its own cache directory",
        description="Download both releases: scjn-leyes into the scjn package's "
        "own cache and notas-archivo into dofjson's. Each keeps its own "
        "directory — this shorthand saves two invocations, it does not merge "
        "the two caches.",
    )
    todo.add_argument(
        "--slug", action="append", default=None, metavar="SLUG", dest="slugs",
        help="Limit the scjn-leyes half to these laws (repeatable); notas-archivo "
        "is downloaded whole either way",
    )
    todo.add_argument(
        "--refrescar", action="store_true",
        help="Re-download assets already present instead of keeping them",
    )

    return parser


def parse_args(argv=None):
    """Parsed arguments, with `comando` naming the verb (None for the
    verbless build form, which is what the CLI accepted before `download`
    existed and still accepts)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # No codNota and no verb: the full help lists both forms, which the
        # build parser's own "cod_nota is required" would not.
        _parser_completo().error("a codNota or a subcommand is required")
    if argv and (argv[0] in SUBCOMANDOS or argv[0] in ("-h", "--help", "build")):
        return _parser_completo().parse_args(argv)
    args = _parser_build().parse_args(argv)
    args.comando = None
    return args


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


def _descarga_federal_laws(slugs, cache_dir, refrescar, log=print):
    """`download federal-laws`: delegates to `scjn.release`'s own downloader
    (issue #209) — this verb puts the scjn-leyes release on disk under
    `scjn`'s own cache directory by default, not `nota2md`'s, since the two
    packages keep separate caches now. `cache_dir` here is `nota2md`'s own
    `--cache-dir` convention: `cache.SIN_CACHE_DIR` (not given) translates to
    `None` ("use `scjn.cache.CACHE_DIR`"), while an explicit path is
    forwarded unchanged.

    When `cache_dir` is left at its default (no `--cache-dir` given), first
    moves over whatever this release's assets a pre-#209 install already
    cached under `nota2md`'s own default directory (the only place they used
    to live) into `scjn`'s own default directory — `scjn` cannot do this
    itself, since it must never know `nota2md`'s cache layout (see
    `scjn.cache.migrate_legacy_assets`); this is the one place in `nota2md`
    that hands that knowledge off, so an upgrade does not silently start
    downloading ~380 MB it already has. An explicit `--cache-dir` names a
    directory for `scjn.release`'s downloader to use directly, with no
    migration — that is a directory the caller is actively choosing now, not
    the legacy default.
    """
    from scjn.cache import CACHE_DIR as SCJN_CACHE_DIR
    from scjn.cache import migrate_legacy_assets
    from scjn.release import download_scjn_leyes_assets

    if cache_dir is cache.SIN_CACHE_DIR:
        legacy_dir = Path(cache.CACHE_DIR) / "scjn-leyes"
        movidos = migrate_legacy_assets(SCJN_CACHE_DIR, legacy_dir)
        if movidos:
            log(
                f"scjn-leyes: migrated {movidos} asset(s) from the pre-#209 "
                f"nota2md cache ({legacy_dir}) into {SCJN_CACHE_DIR / 'scjn-leyes'}"
            )
        cache_dir_scjn = None
    else:
        cache_dir_scjn = cache_dir

    resultados = download_scjn_leyes_assets(
        slugs, cache_dir=cache_dir_scjn, refrescar=refrescar, log=log,
    )
    nuevos = sum(1 for _, descargado in resultados if descargado)
    destino = resultados[0][0].parent
    log(
        f"scjn-leyes: {len(resultados)} assets in {destino} "
        f"({nuevos} downloaded, {len(resultados) - nuevos} already cached)"
    )
    return resultados


def _descarga_gazette_metadata(cache_dir, refrescar, log=print):
    """`download gazette-metadata`: the notas-archivo assets into *dofjson's*
    cache — a different package's directory, on purpose (see the subcommand's
    own help)."""
    from dofjson.titulos import CACHE_DIR as DOFJSON_CACHE_DIR
    from dofjson.titulos import download_dof_assets

    destino = Path(cache_dir) if cache_dir is not None else Path(DOFJSON_CACHE_DIR)
    previos = {p.name for p in destino.glob("*.tgz")} if destino.exists() else set()
    rutas = download_dof_assets(destino, log=log, refrescar=refrescar)
    nuevos = sum(1 for p in rutas if refrescar or p.name not in previos)
    log(
        f"notas-archivo: {len(rutas)} assets in {destino} "
        f"({nuevos} downloaded, {len(rutas) - nuevos} already cached)"
    )
    return rutas


def _main_download(args, log=print):
    if args.release is None:
        _parser_completo().parse_args(["download", "--help"])
    if args.release in ("federal-laws", "all"):
        cache_dir = (
            _resolver_cache_dir(getattr(args, "cache_dir", None))
            if args.release == "federal-laws"
            else cache.SIN_CACHE_DIR
        )
        if cache_dir is None:
            raise SystemExit(
                "--cache-dir none does not apply to `download federal-laws`: it "
                "writes the release to disk, and 'no cache' has nowhere to write"
            )
        _descarga_federal_laws(args.slugs, cache_dir, args.refrescar, log=log)
    if args.release in ("gazette-metadata", "all"):
        bruto = getattr(args, "cache_dir", None) if args.release == "gazette-metadata" else None
        _descarga_gazette_metadata(bruto, args.refrescar, log=log)


def _main_build(args):
    notas_del_dia = None
    if args.notas:
        notas_del_dia = json.loads(Path(args.notas).read_text(encoding="utf-8"))

    dest = legal_provisions(
        args.cod_nota,
        Path(args.outdir) if args.outdir else None,
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


def main(argv=None):
    args = parse_args(argv)
    if args.comando == "download":
        return _main_download(args)
    if args.comando is None or args.comando == "build":
        return _main_build(args)
    _parser_completo().parse_args(["--help"])


if __name__ == "__main__":
    main()
