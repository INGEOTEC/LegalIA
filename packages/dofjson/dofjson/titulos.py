"""Build a compact codNota+titulo+fecha+codOrgaUno dataset from the notas-archivo release.

The `notas-archivo` GitHub release (see `archivo.py`) publishes one
`notas-YYYY.tgz` per year (1917-2025) and one `notas-YYYY-MM.tgz` per month
of the current year, each holding the per-day notes-index JSON files. This
module downloads every asset straight into memory by default (or reuses a
`cache_dir` on disk, via `download_dof_assets`, if one is given), extracts
its daily JSONs, and keeps only `codNota`, `titulo`
(Spanish for "title"), `fecha` (Spanish for "date") and `codOrgaUno` (the
note's top-level organism/branch code) from each note — `codNota` to fetch
that note's full content later, `titulo` for exploratory analysis of the
titles themselves, `fecha` to place each title in time (e.g. grouping by
year), `codOrgaUno` to group notes by issuing branch without carrying its
full name on every row. The result is a single small JSONL file, light
enough to ship to a Colab GPU runtime for experiments.

Alongside it, `download_legal_provisions_titles` also writes a small JSON map from
`codOrgaUno` to `nombreCodOrgaUno` (its human-readable name, e.g. "PODER
EJECUTIVO") — the pairing lives once per code, not once per note.

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
import gzip
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


def lee_titulos(origen: Path):
    """Yield the records of a dataset `download_legal_provisions_titles` wrote.

    The counterpart of writing it, kept here so a consumer does not have to
    know the file is gzipped JSONL — or reach for a text-mining library to find
    that out. `microtc.utils.tweet_iterator` reads the same format, but pulling
    it in costs `numpy` as well, and it does not declare that dependency: an
    install without it fails on `import microtc`, not at the call.
    """
    with gzip.open(Path(origen), "rt", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea:
                yield json.loads(linea)


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
    (the codNota+titulo+fecha+codOrgaUno projection `download_legal_provisions_titles`
    writes) and `nota_del_dia_en_cache` (a single cached day, for
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
    projection of `notas_de_tgz` that `download_legal_provisions_titles` writes out.
    """
    for nota in notas_de_tgz(contenido, organigrama):
        titulo = _proyectar_titulo(nota)
        if titulo is not None:
            yield titulo


def iterador_de_assets(
    cache_dir: Path | None = None, timeout: int = 60, log=print,
    organigrama: dict | None = None,
):
    """Yield every note in the notas-archivo release, one asset after another
    (`listar_assets()`'s own order), each asset's own notes already in
    `notas_de_tgz`'s order (by day, then codNota within the day) — the whole
    archive as a single stream, never holding more than one asset's notes in
    memory at a time.

    `cache_dir` works exactly like `download_legal_provisions_titles`'s own:
    left as None (the default), every asset is downloaded straight into
    memory and nothing touches disk; give a directory instead to read/reuse
    assets there (see `download_dof_assets`), so a later run only fetches
    what is not already cached.

    `organigrama`, if given, accumulates codOrgaUno -> nombreCodOrgaUno across
    every asset — see `notas_de_tgz`.

    This is the building block `download_legal_provisions_titles` streams
    through `_proyectar_titulo()` to build its compact dataset; any other
    consumer that wants the whole archive, notes whole, can iterate this
    directly instead of re-deriving the download-then-extract loop itself.
    """
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


def download_legal_provisions_titles(
    dest: Path,
    organigrama_dest: Path | None = None,
    cache_dir: Path | None = None,
    timeout: int = 60,
    log=print,
) -> Path:
    """Build a codNota+titulo+fecha+codOrgaUno dataset (gzipped JSONL) out of
    every published note, plus a small codOrgaUno -> nombreCodOrgaUno map.

    Streams every note in the release (`iterador_de_assets`), keeps only
    codNota/titulo/fecha/codOrgaUno from every titled one (`_proyectar_titulo`),
    and appends them to `dest` as gzip-compressed JSONL. With `cache_dir` left
    unset, nothing downloaded touches disk, so the whole run leaves behind
    only the two result files (~1.2 million notes fit in a few tens of MB
    gzipped) — small enough to move around or commit to a Colab notebook for
    experiments.

    `organigrama_dest` (default: `organigrama.json` next to `dest`) gets the
    codOrgaUno -> nombreCodOrgaUno map, built from the same notes as they are
    streamed, and written once at the end.

    `cache_dir`, if given, is passed to `download_dof_assets` so assets are
    fetched (or reused) from disk instead of downloaded straight into memory
    — a rebuild then only fetches assets not already cached there.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    organigrama_dest = Path(organigrama_dest) if organigrama_dest else dest.with_name("organigrama.json")
    organigrama_dest.parent.mkdir(parents=True, exist_ok=True)

    organigrama: dict = {}
    total = 0
    with gzip.open(dest, "wt", encoding="utf-8") as out:
        for nota in iterador_de_assets(cache_dir, timeout, log, organigrama):
            titulo = _proyectar_titulo(nota)
            if titulo is None:
                continue
            out.write(json.dumps(titulo, ensure_ascii=False) + "\n")
            total += 1

    with open(organigrama_dest, "w", encoding="utf-8") as f:
        json.dump(organigrama, f, ensure_ascii=False, indent=2, sort_keys=True)

    log(f"\nTotal: {total} notas -> {dest}")
    log(f"Organigrama: {len(organigrama)} códigos -> {organigrama_dest}")
    return dest


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
