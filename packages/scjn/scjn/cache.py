"""On-disk cache for the `scjn-leyes` release's own assets (issue #209).

Its own directory, not a downstream package's: this package's dependency
direction is one way (see `tests/test_boundary.py`), so it can neither
import nor even name where a downstream package used to cache this release
before this one existed -- see `migrate_legacy_assets` for how a caller
carrying that knowledge hands it off instead.

Layout on disk:

    <CACHE_DIR>/scjn-leyes/indice-global.json.gz
    <CACHE_DIR>/scjn-leyes/<slug>.tgz
    <CACHE_DIR>/scjn-leyes/SHA256SUMS.txt

`CACHE_DIR` defaults to the OS per-user cache directory (`~/.cache/scjn` on
Linux), overridable with `$SCJN_CACHE_DIR` or by reassigning `CACHE_DIR`
directly. There is no `cache_dir=None` ("no cache, read into memory") mode
(issue #209): every reader in `scjn.release` reads off disk, so `cache_dir=None`
on any of them means "use `CACHE_DIR`", not "skip caching" — see
`resuelve_cache_dir`.
"""

import os
from pathlib import Path

import platformdirs
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-scjn/1.0)"}

#: Suffix of a download still in flight — an interrupted `.tgz` must never
#: count as a cache hit: the bytes land here first and are renamed into place
#: only once the download completed.
SUFIJO_PARCIAL = ".parcial"

#: Environment override, read once here rather than on every call.
_VARIABLE_ENTORNO = "SCJN_CACHE_DIR"

#: The subdirectory every `scjn-leyes` asset lives under.
_SCJN_LEYES_RELEASE = "scjn-leyes"

#: Asset names `migrate_legacy_assets` moves verbatim -- everything except a
#: `.tgz`, matched by suffix below. A downstream package's own derived output
#: living next to these assets (e.g. Markdown extracted out of a snapshot) is
#: deliberately not in this list: it is not a release asset, and it is the
#: caller's job to know it should stay behind, not this function's.
_ASSET_NOMBRES_EXACTOS = ("indice-global.json.gz", "SHA256SUMS.txt")


def directorio_cache_predeterminado() -> Path:
    """The directory `scjn-leyes` assets are cached in when a caller does not
    name one: ``$SCJN_CACHE_DIR`` if set, otherwise the OS-appropriate
    per-user cache directory via `platformdirs`."""
    del_entorno = os.environ.get(_VARIABLE_ENTORNO)
    if del_entorno:
        return Path(del_entorno)
    return Path(platformdirs.user_cache_dir("scjn"))


#: Where every reader in `scjn.release` looks for a cached asset when a
#: caller passes no `cache_dir` of its own. Reassign it (e.g.
#: ``scjn.cache.CACHE_DIR = Path("/mnt/datos/scjn")``) to point the whole
#: package somewhere else.
CACHE_DIR = directorio_cache_predeterminado()


def resuelve_cache_dir(cache_dir=None) -> Path:
    """A `cache_dir` argument as an actual directory: `None` (the default
    every reader in `scjn.release` uses) resolves to `CACHE_DIR`, read fresh
    so reassigning it still takes effect; anything else is used as-is.

    Unlike a downstream package's own cache resolver, there is no sentinel
    and no `None` -> "no cache" branch: this package's readers are
    disk-first, so "no cache" is not a mode they support (issue #209)."""
    return Path(cache_dir) if cache_dir is not None else Path(CACHE_DIR)


def _es_asset_de_release(ruta: Path) -> bool:
    """Whether `ruta` (a file directly under a `scjn-leyes/` directory) is a
    release asset `migrate_legacy_assets` should move."""
    if ruta.name in _ASSET_NOMBRES_EXACTOS:
        return True
    return ruta.name.endswith(".tgz") or ruta.name.endswith(".tgz" + SUFIJO_PARCIAL)


def migrate_legacy_assets(cache_dir: Path, legacy_dir: Path) -> int:
    """Move `scjn-leyes` release assets out of `legacy_dir` into `cache_dir`
    (this package's own), with `os.replace` — the one-time consequence of
    this package's cache not existing before issue #209.

    This package has no notion of where a downstream package used to cache
    this release (its dependency direction forbids that — see
    `tests/test_boundary.py`), so `legacy_dir` is always an explicit
    parameter, never computed here. It is the caller's job to know that
    location and to call this once; a downstream package's own `download`
    verb is the natural place, since it already knows where this release used
    to live before this package existed.

    Only release assets move (`*.tgz`, an interrupted `*.tgz.parcial`,
    `indice-global.json.gz`, `SHA256SUMS.txt`) — anything else under
    `legacy_dir` (a downstream package's own derived output living alongside
    them, say) is left exactly where it is; this function only recognizes
    release assets, nothing about what else a caller may have stored next to
    them. Returns how many files were actually moved (0 when `legacy_dir`
    does not exist, is empty of assets, or every asset failed to move).

    A file that cannot be moved (e.g. `cache_dir` is on a different
    filesystem) is left where it is rather than copied — copying ~380 MB
    silently would be its own kind of surprise; the caller is expected to
    still be able to read it from `legacy_dir` in that case.
    """
    if not legacy_dir.is_dir():
        return 0

    archivos = sorted(p for p in legacy_dir.iterdir() if p.is_file() and _es_asset_de_release(p))
    if not archivos:
        return 0

    destino = Path(cache_dir)
    destino.mkdir(parents=True, exist_ok=True)
    movidos = 0
    for origen in archivos:
        try:
            os.replace(origen, destino / origen.name)
        except OSError:
            continue
        movidos += 1
    return movidos


def descarga(url: str, timeout: int = 60) -> bytes:
    """An asset's bytes, straight into memory — the single place
    `scjn.release`'s downloader (`download_scjn_leyes_assets`) makes an HTTP
    request for an asset it already knows the URL of."""
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
    revalidated; `refrescar=True` re-downloads over it. The download is
    written to a `SUFIJO_PARCIAL` file and renamed into place only once it
    finished, so a connection dropped mid-way can never leave behind a
    truncated asset that later reads as a hit.
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
