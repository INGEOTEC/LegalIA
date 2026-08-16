"""The dofjson package itself is the one entry point for reading and
downloading the DOF, so no other function — in this package or in another
one (nota2md, leyesmx...) — should need to import dofjson.client or
dofjson.dofweb directly.

get_nota() and get_notas() are re-exported from dofjson.api, which does the
real unification work: SIDOF (dofjson.client) first, falling back to the
DOF's own website (dofjson.dofweb) when SIDOF has nothing for that note/day.
Every other function below has no dofweb equivalent to fall back to at all
(the website carries no per-note images, PDFs, page numbers, economic
indicators, or edition metadata) — those are re-exported here as thin,
direct passthroughs to dofjson.client, so a caller reaches all of it through
one name (`dofjson`) either way, without needing to know which submodule
actually implements which piece."""

from dofjson.api import get_nota, get_notas
from dofjson.client import (
    download_imagen,
    download_nota,
    download_nota_imagen_o_pdf,
    download_nota_imagenes,
    download_nota_pdf,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
    infer_paginas,
    quita_notas_sin_titulo,
)
from dofjson.dofweb import FUENTE as FUENTE_WEB

__version__ = "0.5.0"

__all__ = [
    "get_nota",
    "get_notas",
    "get_diario",
    "get_indicadores",
    "get_imagenes",
    "download_pdf",
    "download_imagen",
    "download_nota",
    "download_nota_imagenes",
    "download_nota_pdf",
    "download_nota_imagen_o_pdf",
    "infer_paginas",
    "quita_notas_sin_titulo",
    "FUENTE_WEB",
]
