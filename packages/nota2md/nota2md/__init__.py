"""Build the Markdown of a single DOF note (`legal_provisions`), reconstruct
a law's current text from nothing but its DOF notes (`reconstruct_legal_provisions`),
and read a law's reform history back from the historial-legislativo release
(`download_legal_provisions_provenance_ids`) — the package's three entry
points, re-exported here so each can be imported straight off `nota2md`
(``from nota2md import legal_provisions``) instead of its own submodule.
"""

from nota2md.builder import legal_provisions
from nota2md.leyes import reconstruct_legal_provisions
from nota2md.utils import download_legal_provisions_provenance_ids

__version__ = "0.3.0"

__all__ = [
    "legal_provisions",
    "reconstruct_legal_provisions",
    "download_legal_provisions_provenance_ids",
]
