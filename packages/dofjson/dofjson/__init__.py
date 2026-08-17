"""The dofjson package itself is the one entry point for reading and
downloading the DOF, so no other function — in this package or in another
one (nota2md, leyesmx...) — should need to import dofjson.sidof or
dofjson.dofweb directly.

get_nota(), get_notas(), download_nota(), download_nota_imagenes(),
download_nota_pdf() and download_nota_imagen_o_pdf() are all re-exported
from dofjson.api: SIDOF (dofjson.sidof) first, falling back to the DOF's
own website (dofjson.dofweb) when SIDOF has nothing for that note/day, or
raising a clear error for a download that needs SIDOF-only fields (codDiario,
pagina) a dofweb-recovered note never carries. infer_paginas() and
quita_notas_sin_titulo() (dofjson.notas) are pure helpers for a notas-shaped
dict already in hand, from either source. Every other function below has no
dofweb equivalent to fall back to at all (the website carries no per-note
images, PDFs, page numbers, economic indicators, or edition metadata) — those
are re-exported here as thin, direct passthroughs to dofjson.sidof, so a
caller reaches all of it through one name (`dofjson`) either way, without
needing to know which submodule actually implements which piece."""

from dofjson.api import (
    download_nota,
    download_nota_imagen_o_pdf,
    download_nota_imagenes,
    download_nota_pdf,
    get_nota,
    get_notas,
)
from dofjson.dofweb import FUENTE as FUENTE_WEB
from dofjson.notas import infer_paginas, quita_notas_sin_titulo
from dofjson.sidof import (
    download_imagen,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
)

__version__ = "0.5.1"

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
