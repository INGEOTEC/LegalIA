"""On-disk cache for what `nota2md` itself derives or fetches (issue #117).

Deliberately the same idea `dofjson.titulos` already uses for the
`notas-archivo` release — a `platformdirs`-backed `CACHE_DIR`, a
`SIN_CACHE_DIR` sentinel so "the caller passed nothing" is distinguishable
from an explicit ``cache_dir=None`` ("skip the cache for this call") — but its
own directory, not `dofjson`'s.

Layout on disk:

    <CACHE_DIR>/scjn-leyes/md/<slug>-<archivo>
    <CACHE_DIR>/dof/nota-<codNota>.md

Since issue #209, the `scjn-leyes` release's own assets (`indice-global.json.gz`,
`<slug>.tgz`, `SHA256SUMS.txt`) no longer live here — they moved to the `scjn`
package's own cache (`scjn.cache.CACHE_DIR`), a separate directory with its
own lifecycle, since `scjn` must never depend on `nota2md` to find them. This
module keeps only what `nota2md` itself derives: `scjn-leyes/md/` is the one
snapshot a `legal_provisions` caller asked for, extracted out of `scjn`'s
tarball and cached here as derived text — deletable at any time and
re-derivable by reading the tarball again, so it does not belong in `scjn`'s
own asset cache. `dof/` holds what `legal_provisions(codNota)` builds when
the SCJN corpus does not cover the note (issue #165): a sibling of
`scjn-leyes/`, not a subdirectory of it, because it does not come from that
release and clearing the corpus must not take DOF text with it.

Freshness for `dof/nota-<codNota>.md`: none — it carries no version and the
HTML/OCR it is built from can change, so it is rebuilt on every call. The
`scjn-leyes/md/` cache is a cache proper: a file already there is returned
without opening the tarball, matched by name and never revalidated, the same
rule `scjn.cache` follows for the tarball it came from.
"""

import os
from pathlib import Path

import platformdirs

#: Suffix of a write still in flight. A file cut off half-way by an
#: interrupted process must never count as a cache hit: the bytes land here
#: first and are renamed into place only once the write completed.
SUFIJO_PARCIAL = ".parcial"

#: Environment override, read once here rather than on every call — same as
#: any other module-level default, and reassigning `CACHE_DIR` directly stays
#: the way to move the cache from inside a process.
_VARIABLE_ENTORNO = "NOTA2MD_CACHE_DIR"


def directorio_cache_predeterminado() -> Path:
    """The directory `nota2md`'s own derived/fetched text is cached in when a
    caller does not name one: ``$NOTA2MD_CACHE_DIR`` if set, otherwise the
    OS-appropriate per-user cache directory (e.g. ``~/.cache/nota2md`` on
    Linux, ``~/Library/Caches/nota2md`` on macOS) via `platformdirs`."""
    del_entorno = os.environ.get(_VARIABLE_ENTORNO)
    if del_entorno:
        return Path(del_entorno)
    return Path(platformdirs.user_cache_dir("nota2md"))


#: Where `legal_provisions` looks for/writes its own derived output when a
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


#: Where a snapshot extracted out of a `scjn-leyes` tarball is materialized,
#: and where `legal_provisions` writes a note built from the DOF, when the
#: caller named no `outdir` of their own — relative to the resolved cache
#: directory. See the module docstring's layout diagram.
SUBDIR_MD_SCJN = ("scjn-leyes", "md")
SUBDIR_DOF = ("dof",)


def directorio_de_salida(cache_dir, *partes) -> Path:
    """``<cache_dir>/<*partes>``, created if it is not there yet — the
    destination directory of a `legal_provisions` call that named no `outdir`.

    ``cache_dir=None`` ("skip the cache") has no answer to give here: with no
    `outdir` either, there is nowhere left to write. That combination raises
    `ValueError` rather than silently picking a directory."""
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is None:
        raise ValueError(
            "cache_dir=None means 'no cache', and outdir=None means 'write it "
            "into the cache': together they leave nowhere to write. Pass an "
            "outdir, or let cache_dir default to nota2md.cache.CACHE_DIR"
        )
    ruta = directorio.joinpath(*partes)
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def escribe_texto(destino: Path, texto: str) -> Path:
    """Write `texto` to `destino` through a `SUFIJO_PARCIAL` file, renamed
    into place only once the write finished — a run interrupted half-way can
    never leave behind a truncated file that the next call reads as a hit."""
    parcial = destino.with_name(destino.name + SUFIJO_PARCIAL)
    parcial.write_text(texto, encoding="utf-8")
    parcial.replace(destino)
    return destino
