"""Build the Markdown of a single legal provision (`legal_provisions`),
reconstruct a law's current text from nothing but its DOF notes
(`reconstruct_legal_provisions`), read a law's reform history back from the
historial-legislativo release (`download_legal_provisions_provenance_ids`),
fetch a whole day's legal provisions as one flat list, each one naming the
edition it was published in (`fetch_daily_legal_provisions`, re-exported from
`dofjson.api` — issue #180),
get a note's record with its text already in Markdown (`get_document`),
stream every legal provision ever published, as titles
(`legal_provisions_titles`), and read the SCJN-based corpus of
consolidated law texts back from the scjn-leyes release
(`download_scjn_leyes_corpus`, `download_scjn_leyes_index`) — the package's
eight entry points, re-exported here so each can be imported straight off
`nota2md` (``from nota2md import legal_provisions``) instead of its own
submodule.

Note that `legal_provisions` answers from the SCJN corpus by default (the
consolidated text of the whole law as it read right after that reform) and
only goes to the DOF when the corpus does not cover the codNota — pass
``source="dof"`` for the original source. See `nota2md.builder`.
"""

from dofjson.titulos import legal_provisions_titles
from nota2md.builder import (
    fetch_daily_legal_provisions,  # re-exported from dofjson.api (issue #180)
    get_document,
    legal_provisions,
)
from nota2md.leyes import reconstruct_legal_provisions
from nota2md.scjn import download_scjn_leyes_corpus, download_scjn_leyes_index
from nota2md.utils import download_legal_provisions_provenance_ids

__version__ = "0.6.0"

__all__ = [
    "legal_provisions",
    "reconstruct_legal_provisions",
    "download_legal_provisions_provenance_ids",
    "fetch_daily_legal_provisions",
    "get_document",
    "legal_provisions_titles",
    "download_scjn_leyes_corpus",
    "download_scjn_leyes_index",
]
