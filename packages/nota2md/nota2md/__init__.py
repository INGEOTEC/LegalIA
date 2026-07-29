"""Build the Markdown of a single DOF note (`legal_provisions`), reconstruct
a law's current text from nothing but its DOF notes (`normative_reconstruction`),
and read a law's reform history back from the historial-legislativo release
(`download_normative_history`) — the package's three entry points, re-exported
here so each can be imported straight off `nota2md` (``from nota2md import
legal_provisions``) instead of its own submodule.
"""

from nota2md.builder import legal_provisions
from nota2md.leyes import normative_reconstruction
from nota2md.utils import download_normative_history

__version__ = "0.3.0"

__all__ = [
    "legal_provisions",
    "normative_reconstruction",
    "download_normative_history",
]
