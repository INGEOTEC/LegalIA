"""`scjn`'s command line: the one verb this package needs, putting the
`scjn-leyes` release on disk.

    scjn download [--slug SLUG] [--cache-dir DIR] [--refrescar]

A downstream package's own `download` subcommands delegate to this same
downloader (`scjn.release.download_scjn_leyes_assets`) rather than
reimplementing it.
"""

import argparse

from scjn.cache import CACHE_DIR
from scjn.release import download_scjn_leyes_assets


def _parser():
    parser = argparse.ArgumentParser(
        prog="scjn",
        description="Download the scjn-leyes release -- the reverse index plus one "
        "tarball per federal law -- into this package's own per-user cache "
        "directory, so every reader in scjn.release finds it already there.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    descarga = sub.add_parser(
        "download",
        help="Download the scjn-leyes release's assets",
        description="Download the scjn-leyes release's assets. Already-cached "
        "assets are matched by name and never revalidated, so a second run "
        "costs no download at all -- pass --refrescar to force one.",
    )
    descarga.add_argument(
        "--slug", action="append", default=None, metavar="SLUG", dest="slugs",
        help="Only this law's tarball, by slug (repeatable). Not given: every law "
        "the release publishes. The reverse index is always downloaded.",
    )
    descarga.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="Directory to write into. Not given: scjn.cache.CACHE_DIR (the "
        "OS-appropriate per-user cache, overridable with $SCJN_CACHE_DIR).",
    )
    descarga.add_argument(
        "--refrescar", action="store_true",
        help="Re-download assets already present instead of keeping them",
    )
    return parser


def _main_download(args, log=print):
    cache_dir = args.cache_dir if args.cache_dir is not None else CACHE_DIR
    resultados = download_scjn_leyes_assets(
        args.slugs, cache_dir=cache_dir, refrescar=args.refrescar, log=log,
    )
    nuevos = sum(1 for _, descargado in resultados if descargado)
    destino = resultados[0][0].parent
    log(
        f"scjn-leyes: {len(resultados)} assets in {destino} "
        f"({nuevos} downloaded, {len(resultados) - nuevos} already cached)"
    )


def main(argv=None):
    # No `return` of a subcommand's own result: the console-script entry
    # point does `sys.exit(main())`, and a truthy non-int return value (a
    # list of results, say) would be printed and treated as a failing exit
    # code.
    args = _parser().parse_args(argv)
    if args.comando == "download":
        _main_download(args)


if __name__ == "__main__":
    main()
