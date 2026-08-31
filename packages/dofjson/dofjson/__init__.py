"""dofjson: the one entry point for SIDOF/dofweb functionality (issue #104)
and for the notas-archivo release (this module's own `notas_de_tgz`, issue
#103) -- see tests/test_dofjson.py for the surface this locks in."""

__version__ = "0.6.0"

from dofjson import dofweb
from dofjson.api import (
    download_edicion_pdf,
    download_nota,
    download_nota_imagen_o_pdf,
    download_nota_imagenes,
    download_nota_pdf,
    get_nota,
    get_notas,
)
from dofjson.notas import infer_paginas, quita_notas_sin_titulo
from dofjson.sidof import (
    download_imagen,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
)
from dofjson.titulos import (
    download_dof_assets,
    iterador_de_assets,
    legal_provisions_titles,
    notas_de_tgz,
    organigrama,
)

#: dofweb's own source marker, re-exported so a caller checking a result's
#: `fuente` never has to import dofjson.dofweb itself just for this constant.
FUENTE_WEB = dofweb.FUENTE

__all__ = [
    "get_nota",
    "get_notas",
    "FUENTE_WEB",
    "download_nota",
    "download_nota_imagenes",
    "download_nota_pdf",
    "download_nota_imagen_o_pdf",
    "download_edicion_pdf",
    "get_diario",
    "get_indicadores",
    "get_imagenes",
    "download_pdf",
    "download_imagen",
    "infer_paginas",
    "quita_notas_sin_titulo",
    "download_dof_assets",
    "iterador_de_assets",
    "legal_provisions_titles",
    "organigrama",
    "notas_de_tgz"
]
