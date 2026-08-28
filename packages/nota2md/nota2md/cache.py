"""On-disk cache for the GitHub-release assets `nota2md` reads (issue #117).

Deliberately the same idea `dofjson.titulos` already uses for the
`notas-archivo` release — a `platformdirs`-backed `CACHE_DIR`, a
`SIN_CACHE_DIR` sentinel so "the caller passed nothing" is distinguishable
from an explicit ``cache_dir=None`` ("skip the cache for this call") — but its
own directory, not `dofjson`'s: `scjn-leyes` and `notas-archivo` are two
different releases with two different lifecycles, and sharing a directory
would make clearing one clear the other.

Layout on disk, one subdirectory per release, asset files named exactly as
the release names them:

    <CACHE_DIR>/scjn-leyes/indice-global.json.gz
    <CACHE_DIR>/scjn-leyes/<slug>.tgz

The `.tgz` is stored as-is rather than unpacked: it is one file per law, it
can be checked against the release's own `SHA256SUMS.txt`, and it is what
`dofjson.download_dof_assets` already does with `notas-archivo`'s assets.

Freshness works like `dofjson`'s: an asset already on disk is a hit **by file
name, with no revalidation** — these assets are only ever republished by hand
(see `scripts/empaqueta_scjn_leyes.py`), so an HTTP round-trip per call to
learn nothing changed would cost more than it buys. Pass ``refrescar=True``
to force the re-download.
"""

import os
from pathlib import Path

import platformdirs
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-nota2md/1.0)"}

#: Suffix of a download still in flight. A `.tgz` cut off half-way by a
#: dropped connection must never count as a cache hit: the bytes land here
#: first and are renamed into place only once the download completed, so an
#: interrupted run leaves behind a file no lookup will ever match.
SUFIJO_PARCIAL = ".parcial"

#: Environment override, read once here rather than on every call — same as
#: any other module-level default, and reassigning `CACHE_DIR` directly stays
#: the way to move the cache from inside a process.
_VARIABLE_ENTORNO = "NOTA2MD_CACHE_DIR"


def directorio_cache_predeterminado() -> Path:
    """The directory release assets are cached in when a caller does not name
    one: ``$NOTA2MD_CACHE_DIR`` if set, otherwise the OS-appropriate per-user
    cache directory (e.g. ``~/.cache/nota2md`` on Linux,
    ``~/Library/Caches/nota2md`` on macOS) via `platformdirs` — the right
    place for data a program can always re-download."""
    del_entorno = os.environ.get(_VARIABLE_ENTORNO)
    if del_entorno:
        return Path(del_entorno)
    return Path(platformdirs.user_cache_dir("nota2md"))


#: Where the release readers in `nota2md.scjn` look for a cached asset when a
#: caller passes no `cache_dir` of their own. Set it once (e.g.
#: ``nota2md.cache.CACHE_DIR = Path("/mnt/datos/nota2md")``) to point the
#: whole package somewhere else; pass an explicit ``cache_dir=None`` to a
#: single call instead to skip the cache for just that call.
CACHE_DIR = directorio_cache_predeterminado()

#: Sentinel default for a `cache_dir` parameter, so a function can tell "the
#: caller passed nothing" (use `CACHE_DIR`, read fresh at call time) apart
#: from an explicit ``cache_dir=None`` ("skip the cache for this call").
SIN_CACHE_DIR = object()


def resuelve_cache_dir(cache_dir) -> Path | None:
    """A `cache_dir` argument as an actual directory (or None, "no cache"):
    `SIN_CACHE_DIR` -> the package-wide `CACHE_DIR`, read now rather than
    frozen at import time, so reassigning it still takes effect; None -> None;
    anything else -> that path."""
    if cache_dir is SIN_CACHE_DIR:
        return Path(CACHE_DIR)
    if cache_dir is None:
        return None
    return Path(cache_dir)


def descarga(url: str, timeout: int = 60) -> bytes:
    """An asset's bytes, straight into memory — the no-cache path, and the
    single place the release readers make their HTTP request."""
    response = requests.get(url, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content


def asset_en_cache(
    release: str,
    nombre: str,
    url: str,
    *,
    cache_dir: Path,
    refrescar: bool = False,
    timeout: int = 60,
) -> Path:
    """The local path of `release`'s asset `nombre`, downloading it from `url`
    into ``<cache_dir>/<release>/`` first if it is not already there.

    A file already present is returned as-is, matched by name and never
    revalidated (see the module docstring); `refrescar=True` re-downloads over
    it. The download is written to a `SUFIJO_PARCIAL` file and renamed into
    place only once it finished, so a connection dropped mid-way can never
    leave behind a truncated asset that later reads as a hit.

    `cache_dir` is a real directory here: deciding whether to cache at all is
    the caller's, via `resuelve_cache_dir` — a None means "download into
    memory" and never reaches this function.
    """
    directorio = Path(cache_dir) / release
    destino = directorio / nombre
    if destino.exists() and not refrescar:
        return destino

    directorio.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + SUFIJO_PARCIAL)
    parcial.write_bytes(descarga(url, timeout))
    parcial.replace(destino)
    return destino


def bytes_de_asset(
    release: str,
    nombre: str,
    url: str,
    *,
    cache_dir,
    refrescar: bool = False,
    timeout: int = 60,
) -> bytes:
    """An asset's bytes, through the cache when there is one and straight into
    memory when there is not — the one call the release readers in
    `nota2md.scjn` need, so neither of them repeats the
    resolve-then-branch dance."""
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is None:
        return descarga(url, timeout)
    ruta = asset_en_cache(
        release, nombre, url,
        cache_dir=directorio, refrescar=refrescar, timeout=timeout,
    )
    return ruta.read_bytes()
