"""`legalvec`'s command line: putting the vector releases on disk, and
saying what is already there.

    legalvec download [--collection {leyes,reglamentos,lineamientos,all}]
                      [--key KEY]... [--model MODEL]...
                      [--cache-dir DIR] [--refresh]
    legalvec status [--cache-dir DIR]

Two verbs, and only two. `download` is an argparse front end over
`legalvec.download_vectors_assets` — it resolves no release series, builds no
asset list and makes no request of its own, so the CLI and the Python API can
never mean different things. `status` is its offline counterpart: it answers
"what do I already have", which is a question about a directory, and it makes
no HTTP request whatsoever. Reading the vectors themselves stays the Python
API's job (`load_vectors`, `load_units`); there is no search verb here.

`--collection` defaults to `all`, so the bare `legalvec download` fetches all
three releases for both models — ~2.08 GB across ~3,067 assets, which is what
the flag's help says out loud. Naming one collection, one `--key` or one
`--model` is how that gets smaller.

The flags are in English (`--collection`, `--key`, `--refresh`) where
`scjn`/`nota2md` spell theirs in Spanish: this is new code, and CLAUDE.md's
language policy is that new code is written in English while existing
identifiers are left alone. One `--key` covers all three collections — a law's
slug, an `id_ordenamiento` otherwise — because `units.parquet` has had one
uniform `clave` column since issue #227 and `download_vectors_assets` takes
one uniform `claves` list.
"""

import argparse
import re

from legalvec import cache
from legalvec.release import METADATA_ASSETS, MODELS, download_vectors_assets, model_slug

#: A published vector file, as `status` reads it back: everything between
#: `vectors-` and the trailing `-<K>.parquet`. That middle is
#: `<clave>-<slug>` for an instrument's own file and `shared-<slug>` for the
#: file a model's shared texts live in — and it cannot be split on dashes,
#: since both halves carry them (`lif-2026`, `qwen3-0.6b`).
_VECTOR_FILE = re.compile(r"^vectors-(?P<middle>.+)-(?P<k>\d+)\.parquet$")


def _parser():
    parser = argparse.ArgumentParser(
        prog="legalvec",
        description="Download this project's vector releases -- one embedding per "
        "distinct text of every federal law, reglamento and lineamiento -- into this "
        "package's own per-user cache directory, so every reader in legalvec finds "
        "them already there; and report what the cache already holds.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser(
        "download",
        help="Download a vector release's assets",
        description="Download the vector releases' assets. Already-cached assets are "
        "matched by name and never revalidated, so a second run costs no download at "
        "all -- pass --refresh to force one.",
    )
    download.add_argument(
        "--collection", choices=(*cache.RELEASE_TAGS, "all"), default="all",
        help="Which release to download (default: %(default)s -- all three, for both "
        "models, ~2.08 GB across ~3,067 assets).",
    )
    download.add_argument(
        "--key", action="append", default=None, metavar="KEY", dest="keys",
        help="Only this instrument's vector files, by clave -- a law's slug, an "
        "id_ordenamiento for a reglamento or a lineamiento (repeatable, and it needs "
        "a single --collection). Not given: every instrument the release publishes. "
        "Each model's shared file and the five metadata assets are always downloaded, "
        "since neither an instrument's vectors nor their texts can be read without them.",
    )
    download.add_argument(
        "--model", action="append", default=None, metavar="MODEL", dest="models",
        help="Only this model's vector files, named either way "
        "(Qwen/Qwen3-Embedding-0.6B or qwen3-0.6b; repeatable). Not given: both models, "
        "exactly what legalvec.download_vectors_assets fetches.",
    )
    download.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="Directory to write into. Not given: legalvec.cache.CACHE_DIR (the "
        "OS-appropriate per-user cache, overridable with $LEGALVEC_CACHE_DIR).",
    )
    download.add_argument(
        "--refresh", action="store_true",
        help="Re-download assets already present instead of keeping them",
    )

    status = sub.add_parser(
        "status",
        help="Report what the cache already holds (offline)",
        description="Report, per collection, whether its release is cached, which of "
        "the metadata assets are there, how many vector files each model has and how "
        "much disk it all takes. Strictly offline: no request is made.",
    )
    status.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="Directory to read. Not given: legalvec.cache.CACHE_DIR (the "
        "OS-appropriate per-user cache, overridable with $LEGALVEC_CACHE_DIR).",
    )
    return parser


def _main_download(args, log=print):
    if args.keys and args.collection == "all":
        raise SystemExit(
            "--key needs a single --collection: a clave belongs to one collection, "
            "so applying it to all three would download nothing but the metadata for "
            "the other two"
        )

    colecciones = (
        tuple(cache.RELEASE_TAGS) if args.collection == "all" else (args.collection,)
    )
    models = tuple(args.models) if args.models else MODELS
    for coleccion in colecciones:
        resultados = download_vectors_assets(
            coleccion, args.keys, models=models, cache_dir=args.cache_dir,
            refresh=args.refresh, log=log,
        )
        nuevos = sum(1 for _ruta, descargado in resultados if descargado)
        destino = cache.release_dir(coleccion, args.cache_dir)
        log(
            f"{cache.RELEASE_TAGS[coleccion]}: {len(resultados)} assets in {destino} "
            f"({nuevos} downloaded, {len(resultados) - nuevos} already cached)"
        )


def _model_of(middle: str, slugs) -> str | None:
    """The model slug of a vector file whose name reads `vectors-<middle>-<K>`,
    matched against the `slugs` already known to be in play.

    A dash split cannot do this: a clave carries dashes (`lif-2026`) and so
    does a slug (`qwen3-0.6b`), so `vectors-lif-2026-qwen3-0.6b-1024.parquet`
    has no parseable boundary on its own. What makes it unambiguous is the
    shared file — `vectors-shared-<slug>-<K>.parquet` has no clave in it at
    all, so every model present in a directory names itself there, and an
    instrument's file is then the one ending in that slug.
    """
    for slug in sorted(slugs, key=len, reverse=True):
        if middle == f"shared-{slug}" or middle.endswith(f"-{slug}"):
            return slug
    return None


def _cached_summary(cache_dir=None) -> dict:
    """What the cache holds, per collection — the whole of what `status`
    reports, with no request made.

    Per collection: its release tag and directory, whether that directory
    exists, which of `METADATA_ASSETS` are present, how many vector files each
    model has (`instrumentos`, plus whether its shared file is there) and the
    bytes on disk. The models are read off the published file names rather
    than taken from `MODELS`, so a release written at another revision — or
    at another `K` — is reported as what it actually is; `MODELS` is only
    consulted as a fallback for a directory holding an instrument's file
    without the shared file that would name its model.
    """
    raiz = cache.resolve_cache_dir(cache_dir)
    resumen = {}
    for coleccion, tag in cache.RELEASE_TAGS.items():
        directorio = raiz / tag
        datos = {
            "tag": tag,
            "directorio": directorio,
            "existe": directorio.is_dir(),
            "metadatos": (),
            "modelos": {},
            "sin_modelo": 0,
            "bytes": 0,
        }
        resumen[coleccion] = datos
        if not datos["existe"]:
            continue

        archivos = [p for p in directorio.iterdir() if p.is_file()]
        datos["metadatos"] = tuple(
            n for n in METADATA_ASSETS if (directorio / n).exists()
        )
        datos["bytes"] = sum(
            p.stat().st_size
            for p in archivos
            if not p.name.endswith(cache.PARTIAL_SUFFIX)
        )

        medios = []
        slugs = set()
        for ruta in archivos:
            match = _VECTOR_FILE.match(ruta.name)
            if match is None:
                continue
            medio = match.group("middle")
            medios.append(medio)
            if medio.startswith("shared-"):
                slugs.add(medio[len("shared-"):])
        slugs.update(model_slug(m) for m in MODELS)

        for medio in medios:
            slug = _model_of(medio, slugs)
            if slug is None:
                datos["sin_modelo"] += 1
                continue
            modelo = datos["modelos"].setdefault(
                slug, {"instrumentos": 0, "compartido": False}
            )
            if medio == f"shared-{slug}":
                modelo["compartido"] = True
            else:
                modelo["instrumentos"] += 1
    return resumen


def _human_bytes(n: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def _main_status(args, log=print):
    resumen = _cached_summary(args.cache_dir)
    log(f"cache: {cache.resolve_cache_dir(args.cache_dir)}")
    ancho = max(len(datos["tag"]) for datos in resumen.values())
    for datos in resumen.values():
        if not datos["existe"]:
            log(f"  {datos['tag']:<{ancho}}  not downloaded")
            continue
        partes = [
            f"metadata {len(datos['metadatos'])}/{len(METADATA_ASSETS)}",
            _human_bytes(datos["bytes"]),
        ]
        for slug, modelo in sorted(datos["modelos"].items()):
            falta = "" if modelo["compartido"] else ", shared file missing"
            n = modelo["instrumentos"]
            partes.append(f"{slug}: {n} instrument{'' if n == 1 else 's'}{falta}")
        if not datos["modelos"]:
            partes.append("no vector files")
        if datos["sin_modelo"]:
            partes.append(f"{datos['sin_modelo']} files of an unrecognized model")
        log(f"  {datos['tag']:<{ancho}}  " + "; ".join(partes))


def main(argv=None):
    # No `return` of a subcommand's own result: the console-script entry
    # point does `sys.exit(main())`, and a truthy non-int return value (a
    # list of results, say) would be printed and treated as a failing exit
    # code. The same reason `scjn.cli.main` returns nothing.
    args = _parser().parse_args(argv)
    if args.command == "download":
        _main_download(args)
    elif args.command == "status":
        _main_status(args)


if __name__ == "__main__":
    main()
