import datetime as dt
from pathlib import Path

import requests
import urllib3

DOF_BASE_URL = "https://www.dof.gob.mx/abrirPDF.php"

# The dof.gob.mx server doesn't send the full certificate chain, so TLS
# verification fails even with plain curl/openssl. Disabled only for this
# host, with the corresponding warning silenced.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def build_url(date: dt.date, edition: str) -> tuple[str, str]:
    filename = f"{date:%d%m%Y}-{edition}.pdf"
    url = f"{DOF_BASE_URL}?archivo={filename}&anio={date.year}&repo=repositorio/"
    return url, filename


def download_pdf(url: str, dest: Path) -> None:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DOF-Downloader/1.0)"}
    response = requests.get(url, headers=headers, timeout=30, verify=False)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(
            f"Response is not a valid PDF (maybe no edition was published that day?): {url}"
        )
    dest.write_bytes(response.content)
