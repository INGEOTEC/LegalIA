"""
Fetch or convert the markdown of the legal-provisions provenance
"""

from pathlib import Path
import io
import tarfile
import json
from tqdm import tqdm
from nota2md import (
    download_legal_provisions_provenance_ids,
    legal_provisions,
    download_legal_provisions_titles
)
from dofjson.titulos import download_dof_assets, _LISTAS_NOTAS, FUENTE_PREDETERMINADA
from dofjson.client import download_nota_imagen_o_pdf

PATH = 'drive/MyDrive/DATA/legal_provisions'
PATH = 'legal_provisions'

def _codNota_metadatos_tgz(contenido: bytes, organigrama: dict | None = None):
    """Yield {"codNota", "titulo", "fecha", "codOrgaUno"} for every titled note
    inside a notas-YYYY[-MM].tgz.
    """
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            dia = json.load(tar.extractfile(member))
            # Days recovered from the DOF website (see dofweb.py) say so; days
            # that predate the marker, or came from SIDOF, do not.
            fuente = dia.get("fuente")
            for lista in _LISTAS_NOTAS:
                for nota in dia.get(lista, []):
                    if nota.get("titulo"):
                        cod_orga_uno = nota.get("codOrgaUno")
                        if organigrama is not None and cod_orga_uno is not None:
                            nombre = nota.get("nombreCodOrgaUno")
                            if nombre:
                                organigrama.setdefault(cod_orga_uno, nombre)
                        # titulo = {
                        #     "codNota": nota["codNota"],
                        #     "existeImagen": nota.get("existeImagen"),
                        #     "existePdf": nota.get("existePdf"),
                        #     "fecha": nota.get("fecha"),
                        #     "existeHtml": nota.get("existeHtml")
                        # }
                        titulo = nota
                        if fuente and fuente != FUENTE_PREDETERMINADA:
                            titulo["fuente"] = fuente
                        yield titulo

colecciones = ["leyes", "reglamentos", "normas", "tratados"]
assets_outdir = Path(f'{PATH}/assets')
asset_paths = download_dof_assets(assets_outdir, 50,
                                  lambda x: None)
provisions = {}
for path in tqdm(asset_paths):
    for data in _codNota_metadatos_tgz(path.read_bytes()):
        provisions[data['codNota']] = data

faltantes = []
for coleccion in colecciones:
    ids = download_legal_provisions_provenance_ids(coleccion)
    for ele in ids:
        for id in ele['historial']:
            if id is None or provisions[id]['existeHtml'] == 'S':
                continue
            flag = 'auto' if provisions[id]['existeImagen'] == 'S' else 'pdf'
            faltantes.append((id, flag))


for codNota, flag in tqdm(faltantes[11+371:]):
    download_nota_imagen_o_pdf(codNota,
                               outdir=Path(PATH))


# pdfs = [x for x in faltantes if x[1] == 'pdf']
# for codNota, flag in tqdm(pdfs):
#     output_file = Path(f'{PATH}/nota-{codNota}.md')
#     if output_file.is_file():
#         continue
#     legal_provisions(codNota,
#                      outdir=Path(PATH),
#                      source=flag)