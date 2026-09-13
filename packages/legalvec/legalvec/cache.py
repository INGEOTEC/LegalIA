"""On-disk cache for this project's own vector releases (issue #227's Fase 4).

Its own directory, not `scjn`'s and not `nota2md`'s, for the same reason
`scjn`'s is separate from `nota2md`'s: these are three independently
published corpora of derived data, and a package that reads one should not
have to know where another one caches.

Layout on disk, one subdirectory per collection:

    <CACHE_DIR>/scjn-leyes-vectors/units.parquet
    <CACHE_DIR>/scjn-leyes-vectors/vectors-<clave>-<model>-<K>.parquet
    <CACHE_DIR>/scjn-leyes-vectors/vectors-shared-<model>-<K>.parquet
    <CACHE_DIR>/scjn-reglamentos-vectors/...
    <CACHE_DIR>/scjn-lineamientos-vectors/...

`CACHE_DIR` defaults to the OS per-user cache directory (`~/.cache/legalvec`
on Linux), overridable with `$LEGALVEC_CACHE_DIR` or by reassigning
`CACHE_DIR` directly. Every reader is disk-first (the posture issue #209 set
for `scjn`): a missing asset raises `AssetNotCached` rather than reaching for
the network, and only `download_vectors_assets` downloads anything.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-legalvec/1.0)"}

#: Environment override, read on every resolution so a test can set it.
_ENV_VAR = "LEGALVEC_CACHE_DIR"

#: Suffix of a download still in flight — an interrupted file must never
#: count as a cache hit.
PARTIAL_SUFFIX = ".parcial"

#: The three vector releases, by collection name: the base tag of each one's
#: own release series, which is also its cache subdirectory. A collection
#: over GitHub's 1,000-asset ceiling is published as `<tag>`, `<tag>-2`, ...
#: (issue #223's scheme, unchanged here) — `scjn-reglamentos-vectors` needs
#: three parts for 2,166 vector files — but every part shares one
#: subdirectory, since an asset name is unique across the whole series.
RELEASE_TAGS = {
    "leyes": "scjn-leyes-vectors",
    "reglamentos": "scjn-reglamentos-vectors",
    "lineamientos": "scjn-lineamientos-vectors",
}

#: The repository the three releases live on.
REPO = "INGEOTEC/LegalIA"

#: How many parts a series walk probes before giving up — a publishing bug
#: that never 404s must raise rather than loop forever (the same cap
#: `scjn.release._MAX_PARTES` sets, for the same reason).
MAX_PARTS = 50


def default_cache_dir() -> Path:
    """Where assets are cached when a caller names no directory:
    ``$LEGALVEC_CACHE_DIR`` if set, else the OS per-user cache directory."""
    from_env = os.environ.get(_ENV_VAR)
    if from_env:
        return Path(from_env)
    return Path(platformdirs.user_cache_dir("legalvec"))


#: Where every reader looks when a caller passes no `cache_dir`. Reassign it
#: (``legalvec.cache.CACHE_DIR = Path("/mnt/datos/legalvec")``) to point the
#: whole package somewhere else.
CACHE_DIR = default_cache_dir()


def resolve_cache_dir(cache_dir=None) -> Path:
    """A `cache_dir` argument as an actual directory: `None` resolves to
    `CACHE_DIR`, read fresh so reassigning it still takes effect."""
    return Path(cache_dir) if cache_dir is not None else Path(CACHE_DIR)


def release_dir(coleccion: str, cache_dir=None) -> Path:
    """Where `coleccion`'s own assets live, cached or not."""
    if coleccion not in RELEASE_TAGS:
        raise ValueError(
            f"unknown collection: {coleccion!r} (expected one of {tuple(RELEASE_TAGS)})"
        )
    return resolve_cache_dir(cache_dir) / RELEASE_TAGS[coleccion]


def _tag_of_part(base: str, n: int) -> str:
    """Part `n` of a release series: part 1 keeps the bare tag, a
    continuation appends `-<n>` (issue #223)."""
    return base if n == 1 else f"{base}-{n}"


def _assets_of_release(tag: str, timeout: int) -> dict[str, str] | None:
    """Every asset of one release tag, name -> download URL, or `None` when
    GitHub has no such release — which is how a series walk knows it is
    done. Nothing published records a part count, so the only authority on
    how many parts exist is GitHub itself."""
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    respuesta = requests.get(url, headers=_HEADERS, timeout=timeout)
    if respuesta.status_code == 404:
        return None
    respuesta.raise_for_status()
    return {
        a["name"]: a["browser_download_url"] for a in respuesta.json().get("assets", [])
    }


def assets_of_series(base: str, timeout: int = 30) -> dict[str, str]:
    """Every asset of a whole release series, merged, probing
    `base`, `base-2`, ... until one 404s (issue #223's scheme)."""
    assets: dict[str, str] = {}
    for n in range(1, MAX_PARTS + 1):
        parte = _assets_of_release(_tag_of_part(base, n), timeout)
        if parte is None:
            if n == 1:
                raise KeyError(
                    f"no hay release '{base}' en {REPO} -- las vectores de esta "
                    "coleccion no se han publicado todavia"
                )
            return assets
        assets.update(parte)
    raise RuntimeError(f"'{base}' sigue teniendo partes despues de {MAX_PARTS}")


def download(url: str, timeout: int) -> bytes:
    respuesta = requests.get(url, headers=_HEADERS, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.content


def asset_in_cache(
    coleccion: str,
    name: str,
    url: str,
    *,
    cache_dir=None,
    refresh: bool = False,
    timeout: int = 60,
) -> Path:
    """The local path of `coleccion`'s asset `name`, downloading it from
    `url` first when it is not already there.

    A file already present is returned as-is, matched by name and never
    revalidated; `refresh=True` re-downloads over it. The download lands on a
    `PARTIAL_SUFFIX` file and is renamed into place only once it finished, so
    a dropped connection can never leave a truncated asset that later reads
    as a hit.
    """
    directorio = release_dir(coleccion, cache_dir)
    destino = directorio / name
    if destino.exists() and not refresh:
        return destino

    directorio.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + PARTIAL_SUFFIX)
    parcial.write_bytes(download(url, timeout))
    parcial.replace(destino)
    return destino
