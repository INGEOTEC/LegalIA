"""Build a compact codNota+titulo+fecha+codOrgaUno dataset from the notas-archivo release.

The `notas-archivo` GitHub release (see `archivo.py`) publishes one
`notas-YYYY.tgz` per year (1917-2025) and one `notas-YYYY-MM.tgz` per month
of the current year, each holding the per-day notes-index JSON files. This
module reads every asset from the on-disk cache by default (populating it via
`download_dof_assets`, the same cache `dofjson.api` reads; pass
``cache_dir=None`` to download straight into memory instead), extracts
its daily JSONs, and keeps only `codNota`, `titulo`
(Spanish for "title"), `fecha` (Spanish for "date") and `codOrgaUno` (the
note's top-level organism/branch code) from each note — `codNota` to fetch
that note's full content later, `titulo` for exploratory analysis of the
titles themselves, `fecha` to place each title in time (e.g. grouping by
year), `codOrgaUno` to group notes by issuing branch without carrying its
full name on every row.

`legal_provisions_titles` yields that projection as a stream. It used to write
it out as a gzipped JSONL file plus an `organigrama.json` map, back when
neither the on-disk asset cache nor an iterator over it existed; both do now
(`download_dof_assets` + `iterador_de_assets`), which made the dataset a third
copy of ~1.2 million records that could silently fall behind the release, and
made every consumer pass a file path around. Issue #166 removed it: the
titles are a cheap projection of a stream we already know how to produce.

The `codOrgaUno` -> `nombreCodOrgaUno` map (its human-readable name, e.g.
"PODER EJECUTIVO") comes out of the same pass, into a dict the caller owns —
see `organigrama` below, or the `organigrama=` parameter.

A note whose day did not come from SIDOF carries a `fuente` key naming where
it did come from (see `dofweb.py`); notes without one are SIDOF's.

`notas_de_tgz` (also reachable as `dofjson.notas_de_tgz`) is the general
counterpart of the titles-only extraction above: it yields every note whole,
every field the asset carries, in publication order (by day, then by codNota
within the day) — the building block for anything that needs more than just
codNota/titulo/fecha/codOrgaUno out of the archive, such as
`nota_del_dia_en_cache` below, which `dofjson.api.get_notas` uses to answer a
cached date without a SIDOF/dofweb request at all.
"""

import datetime as dt
import io
import json
import re
import tarfile
from pathlib import Path

import platformdirs
import requests

RELEASES_API = "https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/notas-archivo"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)",
    "Accept": "application/vnd.github+json",
}
_LISTAS_NOTAS = ("NotasMatutinas", "NotasVespertinas", "NotasExtraordinarias")

#: Source a note is assumed to come from when its day carries no `fuente`.
#: Carrying "sidof" on all ~1.2 million rows would cost more than it says, so
#: only the exceptions — the days recovered from the DOF website — are marked.
FUENTE_PREDETERMINADA = "sidof"

#: A day file's name inside a notas-YYYY[-MM].tgz, e.g. "1980/02011980-notas.json"
#: -- DD, MM, YYYY, in that order (see .github/workflows/notas-archivo.yml).
_NOMBRE_DIA_RE = re.compile(r"(\d{2})(\d{2})(\d{4})-notas\.json$")


def directorio_cache_predeterminado() -> Path:
    """The directory notas-archivo assets are cached in when a caller does
    not name one: the OS-appropriate per-user cache directory (e.g.
    ``~/.cache/dofjson`` on Linux, ``~/Library/Caches/dofjson`` on macOS,
    ``%LOCALAPPDATA%\\dofjson\\Cache`` on Windows), via `platformdirs` —
    the right place for data a program can always re-download, as opposed to
    a user's own documents.
    """
    return Path(platformdirs.user_cache_dir("dofjson"))


#: Where download_dof_assets()/api.get_notas()/api.get_nota() look for a
#: locally cached notas-archivo asset when a caller does not pass a
#: `cache_dir` of their own -- so every one of them agrees on "the assets
#: directory" without threading it through every call. Starts out at
#: directorio_cache_predeterminado(); set it once (e.g.
#: ``dofjson.titulos.CACHE_DIR = Path("/mnt/datos/dofjson")``) to point the
#: whole package somewhere else. Pass an explicit ``cache_dir=None`` to a
#: single call instead, to skip the cache for just that call.
CACHE_DIR = directorio_cache_predeterminado()

#: Sentinel default for a `cache_dir` parameter, so a function can tell "the
#: caller passed nothing" (use CACHE_DIR, read fresh at call time) apart from
#: an explicit ``cache_dir=None`` ("skip the cache for this call"). Shared
#: across dofjson.api/dofjson.archivo/dofjson.cli so all three resolve a
#: missing `cache_dir` the same way.
SIN_CACHE_DIR = object()


def resuelve_cache_dir(cache_dir) -> Path | None:
    """A `cache_dir` argument as an actual directory (or None, "no cache"):
    `SIN_CACHE_DIR` -> the package-wide `CACHE_DIR`, read now rather than
    frozen at import time, so reassigning it still takes effect; None ->
    None; anything else -> that path."""
    if cache_dir is SIN_CACHE_DIR:
        return Path(CACHE_DIR)
    if cache_dir is None:
        return None
    return Path(cache_dir)


def listar_assets(timeout: int = 30) -> list[dict]:
    """`.tgz` assets (name + download URL) of the notas-archivo release."""
    response = requests.get(RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return [
        {"name": asset["name"], "url": asset["browser_download_url"]}
        for asset in response.json()["assets"]
        if asset["name"].endswith(".tgz")
    ]


def download_dof_assets(
    cache_dir: Path | None = None, timeout: int = 60, log=print, *, refrescar: bool = False
) -> list[Path]:
    """Download every notas-archivo `.tgz` asset into `cache_dir`, one per year/month.

    Assets already present in `cache_dir` (matched by file name) are kept
    as-is and not re-downloaded. Returns the local path of every asset, in
    the same order as `listar_assets()`.

    `cache_dir` defaults to the package-wide `CACHE_DIR` (itself
    `directorio_cache_predeterminado()` unless changed) — a directory for
    program data the user never had to name — so a caller who just wants
    the archive on disk somewhere reusable does not have to pick a path.

    `refrescar=True` re-downloads over what is already there: these assets
    are matched by name and never revalidated, so re-publishing the release
    under the same names is otherwise invisible to a populated cache.
    """
    cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    assets = listar_assets()
    paths = []
    for i, asset in enumerate(assets, 1):
        path = cache_dir / asset["name"]
        if path.exists() and not refrescar:
            log(f"[{i}/{len(assets)}] {asset['name']}: already cached")
        else:
            response = requests.get(asset["url"], headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            path.write_bytes(response.content)
            log(f"[{i}/{len(assets)}] {asset['name']}: downloaded")
        paths.append(path)
    return paths


def _fecha_de_miembro(nombre: str) -> dt.date | None:
    """The date a day file's name (see `_NOMBRE_DIA_RE`) stands for, or None
    for a member that is not one (e.g. a directory entry) — used only to put
    days in chronological order, since `DDMMYYYY` sorts as text by day
    first, not by year."""
    coincidencia = _NOMBRE_DIA_RE.search(nombre)
    if not coincidencia:
        return None
    dia, mes, anio = (int(x) for x in coincidencia.groups())
    return dt.date(anio, mes, dia)


def notas_de_tgz(contenido: bytes, organigrama: dict | None = None):
    """Yield every note inside a notas-YYYY[-MM].tgz whole — every field the
    asset carries for it, not a fixed subset — in publication order: by day,
    then by codNota within each day.

    Reads the tarball straight out of `contenido` in memory: nothing is
    written to disk. This is the general building block `_titulos_de_tgz`
    (the codNota+titulo+fecha+codOrgaUno projection `legal_provisions_titles`
    yields) and `nota_del_dia_en_cache` (a single cached day, for
    `dofjson.api.get_notas`) are both built on.

    A note whose day did not come from SIDOF is tagged with the same
    `fuente` its day file carries (see `dofjson.dofweb`); a plain SIDOF note
    is left as-is, exactly as its own record already reads.

    If `organigrama` is given, it is updated in place with every
    `codOrgaUno` -> `nombreCodOrgaUno` pairing seen (first name wins), so a
    caller can accumulate the mapping across every asset without keeping it
    on each yielded record.
    """
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        miembros = [
            m for m in tar.getmembers() if m.isfile() and m.name.endswith(".json")
        ]
        miembros.sort(key=lambda m: _fecha_de_miembro(m.name) or dt.date.min)
        for member in miembros:
            dia = json.load(tar.extractfile(member))
            # Days recovered from the DOF website (see dofweb.py) say so; days
            # that predate the marker, or came from SIDOF, do not.
            fuente = dia.get("fuente")
            notas = [nota for lista in _LISTAS_NOTAS for nota in dia.get(lista, [])]
            notas.sort(key=lambda n: n["codNota"])
            for nota in notas:
                cod_orga_uno = nota.get("codOrgaUno")
                if organigrama is not None and cod_orga_uno is not None:
                    nombre = nota.get("nombreCodOrgaUno")
                    if nombre:
                        organigrama.setdefault(cod_orga_uno, nombre)
                registro = dict(nota)
                if fuente and fuente != FUENTE_PREDETERMINADA:
                    registro.setdefault("fuente", fuente)
                yield registro


def _proyectar_titulo(nota: dict) -> dict | None:
    """The codNota+titulo+fecha+codOrgaUno(+fuente) projection of a note from
    `notas_de_tgz()`/`iterador_de_assets()`, or None for a title-less note --
    dropped from the titles dataset, same idea as
    dofjson.notas.quita_notas_sin_titulo() applied to a live day's index."""
    if not nota.get("titulo"):
        return None
    titulo = {
        "codNota": nota["codNota"],
        "titulo": nota["titulo"],
        "fecha": nota.get("fecha"),
        "codOrgaUno": nota.get("codOrgaUno"),
    }
    if nota.get("fuente"):
        titulo["fuente"] = nota["fuente"]
    return titulo


def _titulos_de_tgz(contenido: bytes, organigrama: dict | None = None):
    """Yield {"codNota", "titulo", "fecha", "codOrgaUno"} for every titled note
    inside a notas-YYYY[-MM].tgz — the codNota+titulo+fecha+codOrgaUno
    projection of `notas_de_tgz` that `legal_provisions_titles` yields.
    """
    for nota in notas_de_tgz(contenido, organigrama):
        titulo = _proyectar_titulo(nota)
        if titulo is not None:
            yield titulo


def iterador_de_assets(
    cache_dir=SIN_CACHE_DIR, timeout: int = 60, log=print,
    organigrama: dict | None = None,
):
    """Yield every note in the notas-archivo release, one asset after another
    (`listar_assets()`'s own order), each asset's own notes already in
    `notas_de_tgz`'s order (by day, then codNota within the day) — the whole
    archive as a single stream, never holding more than one asset's notes in
    memory at a time.

    `cache_dir` follows the same convention as `api.get_notas` and
    `archivo.download_archivo` (issue #166): not given at all -> the
    package-wide `CACHE_DIR`, so a caller who already populated it (e.g.
    `nota2md download gazette-metadata`) reads it back without naming a path;
    a directory -> that one; an explicit ``cache_dir=None`` -> every asset
    downloaded straight into memory, nothing touching disk.

    `organigrama`, if given, accumulates codOrgaUno -> nombreCodOrgaUno across
    every asset — see `notas_de_tgz`.

    This is the building block `legal_provisions_titles` streams through
    `_proyectar_titulo()`; any other consumer that wants the whole archive,
    notes whole, can iterate this directly instead of re-deriving the
    download-then-extract loop itself.
    """
    cache_dir = resuelve_cache_dir(cache_dir)
    if cache_dir is not None:
        asset_paths = download_dof_assets(cache_dir, timeout, log)
        for i, path in enumerate(asset_paths, 1):
            n = 0
            for nota in notas_de_tgz(path.read_bytes(), organigrama):
                n += 1
                yield nota
            log(f"[{i}/{len(asset_paths)}] {path.name}: {n} notas")
    else:
        assets = listar_assets()
        for i, asset in enumerate(assets, 1):
            response = requests.get(asset["url"], headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            n = 0
            for nota in notas_de_tgz(response.content, organigrama):
                n += 1
                yield nota
            log(f"[{i}/{len(assets)}] {asset['name']}: {n} notas")


def legal_provisions_titles(
    cache_dir=SIN_CACHE_DIR,
    timeout: int = 60,
    log=print,
    organigrama: dict | None = None,
):
    """Yield the codNota+titulo+fecha+codOrgaUno(+fuente) record of every
    titled note ever published — `iterador_de_assets` streamed through
    `_proyectar_titulo`, title-less notes dropped.

    Nothing is written: this is the titles dataset as a stream (issue #166),
    read from the notas-archivo cache the rest of the monorepo already shares.
    Populate that cache once (``nota2md download gazette-metadata``, or
    `download_dof_assets`) and every pass afterwards is local.

    `cache_dir` resolves as everywhere else: omitted -> `CACHE_DIR`, a
    directory -> that one, an explicit None -> downloaded into memory,
    nothing on disk.

    `organigrama`, if given, is filled in place with codOrgaUno ->
    nombreCodOrgaUno as the same pass goes by (see `notas_de_tgz`) — the map
    is complete only once the stream has been consumed to the end, which is
    what `organigrama()` below does.

    The stream is not re-iterable: each pass re-reads (and re-decompresses)
    every asset. A consumer that needs more than one pass either calls this
    again or materializes what it needs.
    """
    for nota in iterador_de_assets(cache_dir, timeout, log, organigrama):
        titulo = _proyectar_titulo(nota)
        if titulo is not None:
            yield titulo


def organigrama(cache_dir=SIN_CACHE_DIR, timeout: int = 60, log=print) -> dict:
    """The codOrgaUno -> nombreCodOrgaUno map of the whole archive (e.g.
    ``{"PEJ": "PODER EJECUTIVO"}``), built by consuming the archive once.

    It used to be written out as `organigrama.json` alongside the titles
    dataset; it is small enough (a few hundred codes) to hand back as a dict,
    and it is derived from the same pass the titles are. A caller that wants
    both in one pass passes its own dict as `legal_provisions_titles`'
    `organigrama=` instead of paying for a second one.
    """
    acumulado: dict = {}
    for _ in iterador_de_assets(cache_dir, timeout, log, acumulado):
        pass
    return acumulado


def _asset_para_fecha(fecha: dt.date, hoy: dt.date | None = None) -> str:
    """The name of the notas-archivo asset that would carry `fecha`, per how
    `.github/workflows/notas-archivo.yml` packages them: `notas-{año}.tgz`
    for a year already closed, `notas-{año}-{mes}.tgz` for a month of the
    year still in progress. Whether that asset has actually been downloaded
    into a given `cache_dir` is for the caller to check."""
    hoy = hoy if hoy is not None else dt.date.today()
    if fecha.year < hoy.year:
        return f"notas-{fecha.year}.tgz"
    return f"notas-{fecha.year}-{fecha.month:02d}.tgz"


def nota_del_dia_en_cache(fecha: dt.date, cache_dir: Path, hoy: dt.date | None = None) -> dict | None:
    """A day's notes index for `fecha` — the same shape `dofjson.api.get_notas()`
    returns, `fuente` included — read straight out of a notas-archivo asset
    already sitting in `cache_dir` (see `download_dof_assets`), or None when
    that asset (or that day inside it) is not there.

    A miss here is not an error, just "not cached": the caller is expected to
    fall back to fetching the day from SIDOF/dofweb, as `dofjson.api.get_notas`
    does when it is given a `cache_dir` and this comes back empty-handed.

    Nothing is written to disk and no network request is made — only the one
    day's member is read out of the (already-local) `.tgz`, not the whole
    asset's notes.
    """
    asset = Path(cache_dir) / _asset_para_fecha(fecha, hoy)
    if not asset.exists():
        return None
    miembro = f"{fecha.year}/{fecha:%d%m%Y}-notas.json"
    with tarfile.open(asset, mode="r:gz") as tar:
        try:
            info = tar.getmember(miembro)
        except KeyError:
            return None
        return json.load(tar.extractfile(info))
