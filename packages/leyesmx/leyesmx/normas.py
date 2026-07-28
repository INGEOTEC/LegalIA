"""Build each Norma Oficial Mexicana's history straight from the DOF.

NOMs are not legislation and LeyesBiblio does not carry them — not one "NOM-"
appears across its pages — so the Diputados spine the laws and regulations rest
on does not exist here. It is not needed: for laws the DOF never says which law
a decree amends, which is why a curated source is required, but a NOM's DOF
title *contains the NOM's own code*. The link is intrinsic, and the whole
history can be read off the titles dataset alone.

What a NOM's history looks like in the gazette:

    PROYECTO de Norma Oficial Mexicana PROY-NOM-001-SCFI-2017, …
    RESPUESTA a los comentarios recibidos respecto del Proyecto …
    NORMA Oficial Mexicana NOM-001-SCFI-2018, Aparatos electrónicos…
    Modificación al Transitorio Primero de la Norma Oficial Mexicana …

A draft is keyed to the NOM it drafts, so `PROY-` is stripped. A note that
mentions several codes belongs to each of them, which is common and correct:
the note issuing a revision usually also cancels the edition it replaces.

Codes are not decomposed. Sixty years of the gazette have left 253 distinct
code shapes — `NOM-001-SCFI-1993`, `NOM-150-1979`, `NOM-C-247-1978`,
`NOM-EM-002-SSA2-1993`, `NOM-015-SCT-2-1993` — and reading a year as a
dependency, which is what parsing the parts invites, mislabels hundreds of
them. The normalized code string is the identifier.
"""

import collections
import re

#: A code as cited: `NOM` followed by hyphen-separated segments, optionally
#: prefixed `PROY-` for a draft.
CODIGO = re.compile(r"\b(?:PROY-)?NOM(?:-[A-Z0-9]+)+", re.I)


def codigos_del_titulo(titulo: str) -> set[str]:
    """Every NOM code a DOF title cites, normalized.

    Upper-cased, `PROY-` dropped so a draft joins the NOM it drafts, and any
    trailing hyphen trimmed (titles run codes into the prose that follows).
    """
    return {
        c.upper().removeprefix("PROY-").rstrip("-")
        for c in CODIGO.findall(titulo)
    }


def agrupa(notas) -> dict[str, list[dict]]:
    """Group dofjson title records by the NOM code(s) each one cites."""
    grupos: dict[str, list[dict]] = collections.defaultdict(list)
    for nota in notas:
        for codigo in codigos_del_titulo(nota.get("titulo") or ""):
            grupos[codigo].append(nota)
    return dict(grupos)


def resuelve_citas_parciales(grupos: dict) -> tuple[dict, dict]:
    """Fold codes cited short into the full code they stand for.

    Titles often cite a NOM by part of its code — `NOM-186-SSA1` for
    `NOM-186-SSA1-2000`. Treated as its own key such a citation would look like
    an instrument while being none, and its notes would go missing from the
    instrument they belong to.

    A short citation is folded in when exactly one code extends it. When
    several do it cannot be resolved — `NOM-021` extends to an ASEA, a SAG and
    an SCT4 norm — and it is kept aside rather than guessed at.

    Returns `(instrumentos, ambiguas)`.
    """
    codigos = set(grupos)
    instrumentos = {c: list(v) for c, v in grupos.items()}
    ambiguas = {}

    for corto in sorted(codigos, key=len, reverse=True):
        extensiones = [c for c in codigos if c != corto and c.startswith(corto + "-")]
        if not extensiones:
            continue
        if len(extensiones) == 1 and extensiones[0] in instrumentos:
            destino = instrumentos[extensiones[0]]
            vistos = {n["codNota"] for n in destino}
            destino.extend(n for n in instrumentos[corto]
                           if n["codNota"] not in vistos)
        else:
            ambiguas[corto] = instrumentos[corto]
        del instrumentos[corto]

    return instrumentos, ambiguas


def _clave_fecha(nota: dict) -> tuple:
    f = nota.get("fecha") or "01-01-1900"
    return (f[-4:], f[3:5], f[:2], nota["codNota"])


def historia(notas_del_codigo: list[dict]) -> list[int]:
    """The codNota of a NOM's notes, oldest first.

    Only the codNota is stored, as everywhere in this package: the title, the
    date and the issuing branch come back by joining against the titles
    dataset, and a second copy here would only drift.
    """
    return [n["codNota"] for n in sorted(notas_del_codigo, key=_clave_fecha)]


#: The definitive publication of a NOM opens this way; a draft, a response to
#: comments or a notice does not. Its title is what says what the norm covers.
_ES_LA_NORMA = re.compile(r"^\s*NORMA\s+Oficial\s+Mexicana", re.I)
_LARGO_TITULO = 140


def catalogo(instrumentos: dict) -> list[dict]:
    """One entry per NOM: code, span, note count and a title that describes it.

    The label is taken from the note that *is* the norm rather than from the
    most recent one, which is as often a notice of public consultation and says
    nothing about the subject.
    """
    filas = []
    for codigo, notas in instrumentos.items():
        ordenadas = sorted(notas, key=_clave_fecha)
        etiqueta = next(
            (n for n in reversed(ordenadas) if _ES_LA_NORMA.match(n.get("titulo") or "")),
            ordenadas[-1],
        )
        filas.append({
            "codigo": codigo,
            "notas": len(ordenadas),
            "desde": ordenadas[0].get("fecha"),
            "hasta": ordenadas[-1].get("fecha"),
            "titulo": (etiqueta.get("titulo") or "").strip()[:_LARGO_TITULO],
        })
    filas.sort(key=lambda f: f["codigo"])
    return filas
