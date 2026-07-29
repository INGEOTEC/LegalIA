"""Download one legislative-history collection from the `historial-legislativo`
release.

`leyesmx --ley todas|reglamentos|normas|tratados` writes each collection as an
index (`leyes.json`, `reglamentos.json`, `catalogo.json`…) plus, per
instrument, either its own small file (laws, regulations) or an entry in a
shared lookup (normas, tratados) — the split `scripts/empaqueta_historial.py`
packs into the four tarballs the release publishes (see `dofjson.titulos` for
the equivalent split for titles). A consumer rarely wants that split: this
module downloads a tarball straight into memory and re-joins each
instrument's index entry with its own history into a single dict, so
`download_normative_history("leyes")` returns the CPEUM as one dict carrying
its name, its reform count, *and* the list of `codNota` that are its reforms —
everything `leyes.json` and `cpeum.json` separately hold about it, together.

Nothing here is written to disk: each collection is small enough (a few MB at
most) that returning it as a plain list of dicts, ready for a notebook to
filter or join, costs nothing extra.
"""

import io
import json
import tarfile

import requests

RELEASES_API = "https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/historial-legislativo"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)",
    "Accept": "application/vnd.github+json",
}

COLECCIONES = ("leyes", "reglamentos", "normas", "tratados")

_ASSETS = {
    "leyes": "leyes.tgz",
    "reglamentos": "reglamentos.tgz",
    "normas": "normas.tgz",
    "tratados": "tratados.tgz",
}
#: The file each collection's catalogue (one entry per instrument) lives in
#: inside its tarball.
_INDICES = {
    "leyes": "leyes.json",
    "reglamentos": "reglamentos.json",
    "normas": "catalogo.json",
    "tratados": "catalogo.json",
}


def listar_assets(timeout: int = 30) -> dict[str, str]:
    """`.tgz` assets of the historial-legislativo release, name -> download URL."""
    response = requests.get(RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return {
        asset["name"]: asset["browser_download_url"]
        for asset in response.json()["assets"]
        if asset["name"].endswith(".tgz")
    }


def _miembros(contenido: bytes) -> dict[str, bytes]:
    """Every file member of a tarball, name -> raw bytes, read out of memory."""
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}


def _con_historial_por_archivo(catalogo: list[dict], miembros: dict, campo: str) -> list[dict]:
    """Laws and regulations: each entry's historial is its own `<campo>.json`
    member — `leyes.json`'s `abrev` names `cpeum.json`, and so on."""
    return [
        {**entrada, "historial": json.loads(miembros[f"{entrada[campo]}.json"])}
        for entrada in catalogo
    ]


def _con_historial_por_mapa(
    catalogo: list[dict], miembros: dict, campo: str, mapa_nombre: str
) -> list[dict]:
    """Normas: one shared lookup (`noms.json`) holds every code's historial,
    rather than a file per NOM — 4,674 of them, each a handful of numbers."""
    mapa = json.loads(miembros[mapa_nombre])
    return [{**entrada, "historial": mapa[entrada[campo]]} for entrada in catalogo]


def _con_historial_paralelo(catalogo: list[dict], miembros: dict, lista_nombre: str) -> list[dict]:
    """Tratados: a treaty has no code to key a lookup by, so `tratados.json`
    is instead a plain list in the same order as `catalogo.json`."""
    historial = json.loads(miembros[lista_nombre])
    return [{**entrada, "historial": h} for entrada, h in zip(catalogo, historial)]


def _une_con_historial(coleccion: str, catalogo: list[dict], miembros: dict) -> list[dict]:
    if coleccion in ("leyes", "reglamentos"):
        return _con_historial_por_archivo(catalogo, miembros, "abrev")
    if coleccion == "normas":
        return _con_historial_por_mapa(catalogo, miembros, "codigo", "noms.json")
    return _con_historial_paralelo(catalogo, miembros, "tratados.json")


def download_normative_history(coleccion: str, timeout: int = 60) -> list[dict]:
    """Every instrument of `coleccion`, each as one dict merging its catalogue
    entry with its own `historial` — the `codNota` of its reforms or decrees,
    oldest first.

    `coleccion` is one of "leyes", "reglamentos", "normas" or "tratados".
    Downloads that collection's tarball into memory; nothing touches disk.
    """
    if coleccion not in COLECCIONES:
        raise ValueError(
            f"colección desconocida: {coleccion!r}; opciones: {', '.join(COLECCIONES)}"
        )

    urls = listar_assets(timeout)
    asset = _ASSETS[coleccion]
    if asset not in urls:
        raise KeyError(f"el release no publica el asset {asset!r}")

    response = requests.get(urls[asset], headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    miembros = _miembros(response.content)

    catalogo = json.loads(miembros[_INDICES[coleccion]])
    return _une_con_historial(coleccion, catalogo, miembros)
