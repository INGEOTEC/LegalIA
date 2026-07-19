"""Build a compact codNota+titulo dataset from the notas-archivo release.

The `notas-archivo` GitHub release (see `archivo.py`) publishes one
`notas-YYYY.tgz` per year (1917-2025) and one `notas-YYYY-MM.tgz` per month
of the current year, each holding the per-day notes-index JSON files. This
module downloads every asset straight into memory, extracts its daily JSONs
without ever writing them to disk, and keeps only `codNota` and `titulo`
from each note — the two fields needed to build a text-classification
dataset. The result is a single small JSONL file, light enough to ship to a
Colab GPU runtime for experiments.
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


def _titulos_de_tgz(contenido: bytes):
    """Yield {"codNota", "titulo"} for every titled note inside a notas-YYYY[-MM].tgz.

    Reads the tarball straight out of `contenido` in memory: nothing is
    written to disk.
    """
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            dia = json.load(tar.extractfile(member))
            for lista in _LISTAS_NOTAS:
                for nota in dia.get(lista, []):
                    if nota.get("titulo"):
                        yield {"codNota": nota["codNota"], "titulo": nota["titulo"]}


def download_titulos(dest: Path, timeout: int = 60, log=print) -> Path:
    """Build a codNota+titulo dataset (gzipped JSONL) out of every published note.

    Downloads each notas-archivo asset into memory one at a time, extracts
    it, keeps only codNota/titulo from every note, and appends them to
    `dest` as gzip-compressed JSONL. Nothing downloaded touches disk, so the
    whole run leaves behind only the resulting file (~1.2 million notes fit
    in a few tens of MB gzipped) — small enough to move around or commit to
    a Colab notebook for experiments.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    assets = listar_assets()

    total = 0
    with gzip.open(dest, "wt", encoding="utf-8") as out:
        for i, asset in enumerate(assets, 1):
            response = requests.get(asset["url"], headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            n = 0
            for titulo in _titulos_de_tgz(response.content):
                out.write(json.dumps(titulo, ensure_ascii=False) + "\n")
                n += 1
            total += n
            log(f"[{i}/{len(assets)}] {asset['name']}: {n} notas")

    log(f"\nTotal: {total} notas -> {dest}")
    return dest
