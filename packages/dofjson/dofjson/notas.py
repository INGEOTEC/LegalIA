"""Pure helpers for working with a day's notes index or an edition PDF once
it has already been fetched — none of these make a network request, and
none of them care whether the data came from SIDOF (dofjson.sidof) or the
DOF's own website (dofjson.dofweb): dofjson.api.get_notas() runs a
website-recovered day through quita_notas_sin_titulo() exactly the same way
it does a SIDOF one.

Kept out of dofjson.sidof on purpose, so that module is left holding only
what actually talks to SIDOF — see its own module docstring.
"""

import re

EDICION_LISTAS = {
    "MAT": "NotasMatutinas",
    "VES": "NotasVespertinas",
    "EXT": "NotasExtraordinarias",
}


def infer_paginas(nota: dict, notas_del_dia: dict) -> list[int]:
    """Infer which page(s) a note occupies, using the fact that notes are
    published one after another: if the next note (in publication order)
    starts on the same page, this note is confined to a single page; if it
    starts on a later page, this note is assumed to span through that page
    too.
    """
    lista = notas_del_dia[EDICION_LISTAS[nota["codEdicion"]]]
    ordenada = sorted(lista, key=lambda n: n["codNota"])
    idx = next(i for i, n in enumerate(ordenada) if n["codNota"] == nota["codNota"])

    pagina_inicio = nota["pagina"]
    if len(ordenada) == idx + 1:
        return [pagina_inicio]

    pagina_sig = ordenada[idx + 1]["pagina"]
    if pagina_inicio == pagina_sig:
        return [pagina_inicio]
    return list(range(pagina_inicio, pagina_sig + 1))


def quita_notas_sin_titulo(notas_del_dia: dict) -> dict:
    """Drop notes with no `titulo` from a get_notas()-shaped response — SIDOF's
    or dofweb's, it makes no difference here — for building a clean per-day
    note index. Most are stub duplicates of an adjacent, same-page note
    (existeHtml "S" but existeDoc "N" — see infer_paginas()); the rest are
    genuine image-only notes (existeHtml "N") with no digital text at all.
    Do NOT use this on the notas_del_dia passed into infer_paginas()/
    download_nota(): those rely on stub entries being present to compute
    page spans."""
    filtrado = dict(notas_del_dia)
    for clave in EDICION_LISTAS.values():
        if clave in filtrado:
            filtrado[clave] = [n for n in filtrado[clave] if n.get("titulo")]
    return filtrado


_PAGINA_HEADER_WINDOW = 120


def _detectar_offset_paginacion(reader, paginas_conocidas: set[int]) -> int | None:
    """Best-effort: work out the (printed page number - physical index)
    offset of an edition PDF, by looking for one of the day's known printed
    `pagina` numbers near the top of each physical page's extracted text.

    Modern editions restart their own printed numbering at 1 on the
    edition's cover, so `pagina - 1` is already a valid physical index
    (offset 1). But old digitized volumes often carry a running page count
    from a bound "tomo" spanning many editions (issue #95): their first
    physical page prints no visible number at all, and later pages resume a
    much larger count. Matching the day's actual page numbers against what
    each physical page prints works for both, instead of assuming offset 1.
    """
    votos: dict[int, int] = {}
    for indice, page in enumerate(reader.pages):
        texto = (page.extract_text() or "")[:_PAGINA_HEADER_WINDOW]
        for numero in re.findall(r"\d{1,5}", texto):
            numero = int(numero)
            if numero in paginas_conocidas:
                offset = numero - indice
                votos[offset] = votos.get(offset, 0) + 1
    if not votos:
        return None
    return max(votos, key=votos.get)
