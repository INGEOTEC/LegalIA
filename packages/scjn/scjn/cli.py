"""`scjn`'s command line: the one verb this package needs, putting a release
on disk.

    scjn download [--coleccion {leyes,reglamentos}] [--slug SLUG | --id ID]
                  [--cache-dir DIR] [--refrescar]

A downstream package's own `download` subcommands delegate to this same
downloader (`scjn.release.download_scjn_leyes_assets`/
`download_scjn_reglamentos_assets`) rather than reimplementing it.

`--coleccion` (issue #220) is a two-valued parameter, not a registry: adding
a third collection here would mean adding a third literal branch, the same
way `scjn-reglamentos` added a second one, never a `COLECCIONES` dict."""

import argparse

from scjn.cache import CACHE_DIR
from scjn.release import download_scjn_leyes_assets, download_scjn_reglamentos_assets


def _parser():
    parser = argparse.ArgumentParser(
        prog="scjn",
        description="Download an scjn-leyes/scjn-reglamentos release -- the index plus "
        "one tarball per instrument -- into this package's own per-user cache "
        "directory, so every reader in scjn.release finds it already there.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    descarga = sub.add_parser(
        "download",
        help="Download a release's assets",
        description="Download the scjn-leyes (default) or scjn-reglamentos release's "
        "assets. Already-cached assets are matched by name and never revalidated, so "
        "a second run costs no download at all -- pass --refrescar to force one.",
    )
    descarga.add_argument(
        "--coleccion", choices=("leyes", "reglamentos"), default="leyes",
        help="Which release to download (default: %(default)s).",
    )
    descarga.add_argument(
        "--slug", action="append", default=None, metavar="SLUG", dest="slugs",
        help="Only this law's tarball, by slug (repeatable, --coleccion leyes only). "
        "Not given: every law the release publishes. The index is always downloaded.",
    )
    descarga.add_argument(
        "--id", action="append", default=None, metavar="ID", dest="ids",
        help="Only this reglamento's tarball, by id_ordenamiento (repeatable, "
        "--coleccion reglamentos only). Not given: every reglamento the release "
        "publishes. The index is always downloaded.",
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
    if args.ids and args.coleccion != "reglamentos":
        raise SystemExit("--id solo aplica con --coleccion reglamentos")
    if args.slugs and args.coleccion != "leyes":
        raise SystemExit("--slug solo aplica con --coleccion leyes (default)")

    cache_dir = args.cache_dir if args.cache_dir is not None else CACHE_DIR
    if args.coleccion == "reglamentos":
        resultados = download_scjn_reglamentos_assets(
            args.ids, cache_dir=cache_dir, refrescar=args.refrescar, log=log,
        )
    else:
        resultados = download_scjn_leyes_assets(
            args.slugs, cache_dir=cache_dir, refrescar=args.refrescar, log=log,
        )
    nuevos = sum(1 for _, descargado in resultados if descargado)
    destino = resultados[0][0].parent
    log(
        f"scjn-{args.coleccion}: {len(resultados)} assets in {destino} "
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
