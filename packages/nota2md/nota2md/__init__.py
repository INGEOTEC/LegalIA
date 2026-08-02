"""Build the Markdown of a single DOF note (`legal_provisions`), reconstruct
a law's current text from nothing but its DOF notes (`reconstruct_legal_provisions`),
read a law's reform history back from the historial-legislativo release
(`download_legal_provisions_provenance_ids`), and fetch a day's browsable
legal-provisions index (`fetch_daily_legal_provisions`) — the package's four
entry points, re-exported here so each can be imported straight off `nota2md`
(``from nota2md import legal_provisions``) instead of its own submodule.
"""

from nota2md.builder import fetch_daily_legal_provisions, legal_provisions
from nota2md.leyes import reconstruct_legal_provisions
from nota2md.utils import download_legal_provisions_provenance_ids

__version__ = "0.3.0"

__all__ = [
    "legal_provisions",
    "reconstruct_legal_provisions",
    "download_legal_provisions_provenance_ids",
    "fetch_daily_legal_provisions",
]
