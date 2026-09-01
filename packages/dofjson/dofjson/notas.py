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


def notas_de_la_edicion(nota: dict, notas) -> list[dict]:
    """The day's notes of `nota`'s own edition, in publication order — the
    one place `EDICION_LISTAS` is looked up (issue #180).

    A page number restarts with each edition, so everything that reasons
    about where a note ends on a shared page — infer_paginas(), the
    `paginas_conocidas` of dofjson.api.download_nota_pdf(),
    nota2md.builder.titulo_siguiente() — needs exactly this list and nothing
    wider. Expressed over notas_del_dia(), which already orders by `codNota`
    inside an edition and stamps each note with the bucket it came from, so
    a dofweb-recovered note with no `codEdicion` of its own still lands in
    the right edition.

    `notas` is either day shape: get_notas()'s per-edition dict, or the flat
    list fetch_daily_legal_provisions() returns. Normalising here is what
    lets a public parameter take both without every caller re-deciding
    (issue #180, question 3).
    """
    edicion = nota.get("edicion") or nota["codEdicion"]
    planas = notas_del_dia(notas) if isinstance(notas, dict) else notas
    return [n for n in planas if n["edicion"] == edicion]


def infer_paginas(nota: dict, notas_del_dia: dict) -> list[int]:
    """Infer which page(s) a note occupies, using the fact that notes are
    published one after another: if the next note (in publication order)
    starts on the same page, this note is confined to a single page; if it
    starts on a later page, this note is assumed to span through that page
    too.
    """
    ordenada = notas_de_la_edicion(nota, notas_del_dia)
    idx = next(i for i, n in enumerate(ordenada) if n["codNota"] == nota["codNota"])

    pagina_inicio = nota["pagina"]
    if len(ordenada) == idx + 1:
        return [pagina_inicio]

    pagina_sig = ordenada[idx + 1]["pagina"]
    if pagina_inicio == pagina_sig:
        return [pagina_inicio]
    return list(range(pagina_inicio, pagina_sig + 1))


def notas_del_dia(notas: dict) -> list[dict]:
    """Every note in a get_notas()-shaped response as one flat list, each note
    carrying `edicion` ("MAT"/"VES"/"EXT") and the day's `fuente`.

    Ordered edition-first (MAT, VES, EXT — the day's publication order), then
    by `codNota` within an edition, so a reader goes through the day the way
    the gazette itself is published.

    `edicion` is taken from the bucket the note was sitting in, not from its
    own `codEdicion`: a dofweb-recovered note does not always carry
    `codEdicion`, and where both exist they agree. The day-level `fuente` is
    copied onto each note, so a note taken out of the day's context still says
    whether it came from SIDOF or dof.gob.mx — the same convention get_nota()
    already follows per note.

    Each entry is a shallow copy; the response passed in is left untouched.
    Keys that are not one of the three edition lists (`fuente`, and anything
    SIDOF adds later) are skipped rather than iterated, and an empty day — a
    weekend, a holiday, a day SIDOF lost and dofweb confirmed empty — gives
    back an empty list.
    """
    fuente = notas.get("fuente")
    planas = []
    for edicion, clave in EDICION_LISTAS.items():
        lista = notas.get(clave)
        if not isinstance(lista, list):
            continue
        for nota in sorted(lista, key=lambda n: n["codNota"]):
            plana = dict(nota)
            plana["edicion"] = edicion
            if fuente is not None:
                plana["fuente"] = fuente
            planas.append(plana)
    return planas


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
