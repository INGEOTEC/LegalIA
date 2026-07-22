"""Build a compact codNota+titulo+fecha+codOrgaUno dataset from the notas-archivo release.

The `notas-archivo` GitHub release (see `archivo.py`) publishes one
`notas-YYYY.tgz` per year (1917-2025) and one `notas-YYYY-MM.tgz` per month
of the current year, each holding the per-day notes-index JSON files. This
module downloads every asset straight into memory, extracts its daily JSONs
without ever writing them to disk, and keeps only `codNota`, `titulo`
(Spanish for "title"), `fecha` (Spanish for "date") and `codOrgaUno` (the
note's top-level organism/branch code) from each note — `codNota` to fetch
that note's full content later, `titulo` for exploratory analysis of the
titles themselves, `fecha` to place each title in time (e.g. grouping by
year), `codOrgaUno` to group notes by issuing branch without carrying its
full name on every row. The result is a single small JSONL file, light
enough to ship to a Colab GPU runtime for experiments.

Alongside it, `download_titulos` also writes a small JSON map from
`codOrgaUno` to `nombreCodOrgaUno` (its human-readable name, e.g. "PODER
EJECUTIVO") — the pairing lives once per code, not once per note.
"""

import gzip
import io
import json
import tarfile
from pathlib import Path

import requests

RELEASES_API = "https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/notas-archivo"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)",
    "Accept": "application/vnd.github+json",
}
_LISTAS_NOTAS = ("NotasMatutinas", "NotasVespertinas", "NotasExtraordinarias")


def listar_assets(timeout: int = 30) -> list[dict]:
    """`.tgz` assets (name + download URL) of the notas-archivo release."""
    response = requests.get(RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return [
        {"name": asset["name"], "url": asset["browser_download_url"]}
        for asset in response.json()["assets"]
        if asset["name"].endswith(".tgz")
    ]


def _titulos_de_tgz(contenido: bytes, organigrama: dict | None = None):
    """Yield {"codNota", "titulo", "fecha", "codOrgaUno"} for every titled note
    inside a notas-YYYY[-MM].tgz.

    Reads the tarball straight out of `contenido` in memory: nothing is
    written to disk.

    If `organigrama` is given, it is updated in place with every
    `codOrgaUno` -> `nombreCodOrgaUno` pairing seen (first name wins), so a
    caller can accumulate the mapping across every asset without keeping it
    on each yielded record.
    """
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            dia = json.load(tar.extractfile(member))
            for lista in _LISTAS_NOTAS:
                for nota in dia.get(lista, []):
                    if nota.get("titulo"):
                        cod_orga_uno = nota.get("codOrgaUno")
                        if organigrama is not None and cod_orga_uno is not None:
                            nombre = nota.get("nombreCodOrgaUno")
                            if nombre:
                                organigrama.setdefault(cod_orga_uno, nombre)
                        yield {
                            "codNota": nota["codNota"],
                            "titulo": nota["titulo"],
                            "fecha": nota.get("fecha"),
                            "codOrgaUno": cod_orga_uno,
                        }


def download_titulos(
    dest: Path, organigrama_dest: Path | None = None, timeout: int = 60, log=print
) -> Path:
    """Build a codNota+titulo+fecha+codOrgaUno dataset (gzipped JSONL) out of
    every published note, plus a small codOrgaUno -> nombreCodOrgaUno map.

    Downloads each notas-archivo asset into memory one at a time, extracts
    it, keeps only codNota/titulo/fecha/codOrgaUno from every note, and
    appends them to `dest` as gzip-compressed JSONL. Nothing downloaded
    touches disk, so the whole run leaves behind only the two result files
    (~1.2 million notes fit in a few tens of MB gzipped) — small enough to
    move around or commit to a Colab notebook for experiments.

    `organigrama_dest` (default: `organigrama.json` next to `dest`) gets the
    codOrgaUno -> nombreCodOrgaUno map, built from the same notes as they are
    streamed, and written once at the end.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    organigrama_dest = Path(organigrama_dest) if organigrama_dest else dest.with_name("organigrama.json")
    organigrama_dest.parent.mkdir(parents=True, exist_ok=True)

    assets = listar_assets()
    organigrama: dict = {}

    total = 0
    with gzip.open(dest, "wt", encoding="utf-8") as out:
        for i, asset in enumerate(assets, 1):
            response = requests.get(asset["url"], headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            n = 0
            for titulo in _titulos_de_tgz(response.content, organigrama):
                out.write(json.dumps(titulo, ensure_ascii=False) + "\n")
                n += 1
            total += n
            log(f"[{i}/{len(assets)}] {asset['name']}: {n} notas")

    with open(organigrama_dest, "w", encoding="utf-8") as f:
        json.dump(organigrama, f, ensure_ascii=False, indent=2, sort_keys=True)

    log(f"\nTotal: {total} notas -> {dest}")
    log(f"Organigrama: {len(organigrama)} códigos -> {organigrama_dest}")
    return dest
