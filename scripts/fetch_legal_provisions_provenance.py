"""
Fetch or convert the markdown of the legal-provisions provenance
"""

from pathlib import Path
from tqdm import tqdm
from nota2md import (
    download_legal_provisions_provenance_ids,
    legal_provisions,
)
from dofjson import download_nota_imagen_o_pdf, iterador_de_assets

PATH = 'drive/MyDrive/DATA/legal_provisions'
PATH = 'legal_provisions'

colecciones = ["leyes", "reglamentos", "normas", "tratados"]
assets_outdir = Path(f'{PATH}/assets')
# Every note whole -- this needs existeHtml/existeImagen/existePdf, which the
# titles projection (dofjson.legal_provisions_titles) drops.
provisions = {}
for data in tqdm(iterador_de_assets(assets_outdir, 50, lambda x: None)):
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