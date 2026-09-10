"""`scjn`'s command line: the one verb this package needs, putting a release
on disk.

    scjn download [--coleccion {leyes,reglamentos,lineamientos}]
                  [--slug SLUG | --id ID] [--cache-dir DIR] [--refrescar]

A downstream package's own `download` subcommands delegate to this same
downloader (`scjn.release.download_scjn_leyes_assets`/
`download_scjn_reglamentos_assets`/`download_scjn_lineamientos_assets`)
rather than reimplementing it.

`--coleccion` (issue #220, #222) is dispatched through
`scjn.cache.COLECCIONES_POR_ID` for the two id-keyed collections
(`reglamentos`, `lineamientos`) -- both share the exact same download path
(issue #222's Fase 0), so adding one more id-keyed collection here means one
more dict entry, not a new literal branch. `leyes` stays its own separate
branch: it takes `--slug`, not `--id`, and has no `Coleccion` descriptor at
all."""

import argparse

from scjn.cache import CACHE_DIR, _SCJN_LEYES_RELEASE, COLECCIONES_POR_ID
from scjn.release import (
    _ULTIMO_NUMERO_DE_PARTES,
    download_scjn_leyes_assets,
    download_scjn_lineamientos_assets,
    download_scjn_reglamentos_assets,
)

#: Each id-keyed collection's own downloader -- keyed by `Coleccion.nombre`,
#: same as `COLECCIONES_POR_ID` itself. A third id-keyed collection adds one
#: entry here, never a new `if`/`elif` branch.
_DESCARGAS_POR_ID = {
    "reglamentos": download_scjn_reglamentos_assets,
    "lineamientos": download_scjn_lineamientos_assets,
}


def _parser():
    parser = argparse.ArgumentParser(
        prog="scjn",
        description="Download an scjn-leyes/scjn-reglamentos/scjn-lineamientos release "
        "-- the index plus one tarball per instrument -- into this package's own "
        "per-user cache directory, so every reader in scjn.release finds it already there.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    descarga = sub.add_parser(
        "download",
        help="Download a release's assets",
        description="Download the scjn-leyes (default), scjn-reglamentos or "
        "scjn-lineamientos release's assets. Already-cached assets are matched by name "
        "and never revalidated, so a second run costs no download at all -- pass "
        "--refrescar to force one.",
    )
    descarga.add_argument(
        "--coleccion", choices=("leyes", *COLECCIONES_POR_ID), default="leyes",
        help="Which release to download (default: %(default)s).",
    )
    descarga.add_argument(
        "--slug", action="append", default=None, metavar="SLUG", dest="slugs",
        help="Only this law's tarball, by slug (repeatable, --coleccion leyes only). "
        "Not given: every law the release publishes. The index is always downloaded.",
    )
    descarga.add_argument(
        "--id", action="append", default=None, metavar="ID", dest="ids",
        help="Only this instrument's tarball, by id_ordenamiento (repeatable, "
        "--coleccion reglamentos/lineamientos only). Not given: every instrument the "
        "release publishes. The index is always downloaded.",
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
    if args.ids and args.coleccion not in COLECCIONES_POR_ID:
        raise SystemExit(
            f"--id solo aplica con --coleccion {'/'.join(COLECCIONES_POR_ID)}"
        )
    if args.slugs and args.coleccion != "leyes":
        raise SystemExit("--slug solo aplica con --coleccion leyes (default)")

    cache_dir = args.cache_dir if args.cache_dir is not None else CACHE_DIR
    if args.coleccion in COLECCIONES_POR_ID:
        resultados = _DESCARGAS_POR_ID[args.coleccion](
            args.ids, cache_dir=cache_dir, refrescar=args.refrescar, log=log,
        )
    else:
        resultados = download_scjn_leyes_assets(
            args.slugs, cache_dir=cache_dir, refrescar=args.refrescar, log=log,
        )
    nuevos = sum(1 for _, descargado in resultados if descargado)
    destino = resultados[0][0].parent
    base = (
        COLECCIONES_POR_ID[args.coleccion].tag_base
        if args.coleccion in COLECCIONES_POR_ID
        else _SCJN_LEYES_RELEASE
    )
    partes = _ULTIMO_NUMERO_DE_PARTES.get(base)
    sufijo_partes = f" ({partes} partes)" if partes and partes > 1 else ""
    log(
        f"scjn-{args.coleccion}: {len(resultados)} assets in {destino} "
        f"({nuevos} downloaded, {len(resultados) - nuevos} already cached){sufijo_partes}"
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
