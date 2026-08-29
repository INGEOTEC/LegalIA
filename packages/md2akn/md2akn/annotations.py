"""Reform annotations as structured metadata (issue #161).

The SCJN's consolidated texts carry, interleaved with the law, the notes that
say which reform touched which part::

    **(REFORMADO PRIMER PÁRRAFO, D.O.F. 10 DE JUNIO DE 2011)**

This module turns those from noise into `Annotation`s attached to the node
they describe. In Akoma Ntoso terms they are the **passive** side of a
modification — `lifecycle` / `temporalData` / `passiveModifications`, reviewed
in issue #91. No such XML is emitted here; what is produced is the same
information at a granularity fine enough for someone to emit it later. The
**active** side — a DOF decree saying what *it* changed — is issue #163, and
the two share an eId scheme precisely so they can be crossed against each
other.

## What counts as an annotation

Only a parenthesised block whose head is one of the known actions. The corpus
puts plenty of other things in the same shape — `(NOTA: EL 22 DE JUNIO DE
2023, EL PLENO DE LA SUPREMA CORTE…)` (1,062 of them), `(ARANCEL)` (97),
`(VÉASE TABLA ANEXA)` (57) — and none of those is a reform. Admitting them
would make #162's "annotations we failed to parse" count meaningless, which
is the number that says whether this module is finished.

## Dates

Written out in Spanish (`10 DE JUNIO DE 2011`, `1o. DE ENERO DE 1995`), so
they are read with a month table of this module's own. Not `locale`: it
depends on what the machine has installed, which makes the result
irreproducible in CI, and not a date-parsing dependency, for one table of
twelve words.
"""

from __future__ import annotations

import datetime as dt
import re

from md2akn.model import Annotation

#: The reform verbs, as they appear. Gender agrees with what was reformed —
#: `REFORMADO` an artículo, `REFORMADA` una fracción — and the accents are
#: inconsistent in the corpus itself, so both are admitted and the result is
#: normalized to the masculine, unaccented singular.
_VERBOS = "REFORMAD|ADICIONAD|DEROGAD|REUBICAD|ACTUALIZAD|RECORRID"

#: A "fe de erratas" — a correction published against an earlier text. 511
#: occurrences, so it is a real action and not a curiosity; it keeps its own
#: name rather than being folded into REFORMADO, which it is not.
_FE_DE_ERRATAS = r"F\.\s*DE\s*E\."

#: The head of an annotation: one or more actions, possibly joined ("REFORMADO
#: Y REUBICADO", 223). Used both to recognize a block as an annotation at all
#: and to split the action from its scope.
ACCION = re.compile(
    rf"^(?P<acciones>(?:{_VERBOS})[OA]S?(?:\s+Y\s+(?:{_VERBOS})[OA]S?)*|{_FE_DE_ERRATAS})"
    r"(?P<resto>.*)$",
    re.UNICODE,
)

#: The gazette date an annotation cites, and everything before it (the scope).
#: Every dot and comma here is optional because the corpus writes all of them:
#: `D.O.F 8 DE NOVIEMBRE DE 2019`, `D.O.F., 30 DE DICIEMBRE DE 2002`,
#: `D.O.F. DE 26 DE DICIEMBRE DE 1990`. `P.O.` is a state gazette, not the
#: DOF — 1 occurrence, in a law that cites a local publication; the date is
#: still a date, so it is read rather than dropped.
_FECHA_DOF = re.compile(
    r"[,;]?\s*(?:D\.\s*O\.\s*F|P\.\s*O)\.?,?\s*(?:DE\s+)?(?P<fecha>[^,)]+?)\s*$",
    re.UNICODE,
)

#: An annotation that names no gazette at all — `(REFORMADO, 20 DE MAYO DE
#: 2021)`. Tried only after `_FECHA_DOF` fails, and it still has to parse as
#: a full written-out date, so it cannot swallow a scope by accident.
_FECHA_SUELTA = re.compile(r"[,;]\s*(?P<fecha>[^,;)]+?)\s*$", re.UNICODE)

#: When an annotation cites another instrument instead of a date:
#: "(DEROGADO POR ARTICULO SEGUNDO TRANSITORIO DE LA LEY DEL SERVICIO POSTAL
#: MEXICANO, ...)".
_POR_NORMA = re.compile(r"^\s*POR\s+(?P<fuente>.+)$", re.UNICODE | re.DOTALL)

_MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}
_MES_ALT = {"SETIEMBRE": 9}

#: `10 DE JUNIO DE 2011`, and the ordinal first-of-the-month the corpus writes
#: as `1o.`/`1°`/`1º`. Both `DE`s are optional because the corpus drops either
#: one — `30 DICIEMBRE DE 2002`, `30 DE DICIEMBRE 2002`. Nothing else about
#: the shape is optional, so a scope cannot be mistaken for a date.
_FECHA_EN_PALABRAS = re.compile(
    r"^\s*(?P<dia>\d{1,2})\s*[oº°]?\.?\s+(?:DE\s+)?(?P<mes>[A-ZÁÉÍÓÚ]+)\s+(?:DE\s+)?"
    r"(?P<anio>\d{4})\s*\.?\s*$",
    re.UNICODE | re.IGNORECASE,
)

_ACENTOS = str.maketrans("ÁÉÍÓÚÜ", "AEIOUU")


def _normaliza(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.translate(_ACENTOS).upper()).strip()


def normaliza_accion(accion: str) -> str:
    """`REFORMADA` / `REFORMADO` / `REFORMADAS` → `REFORMADO`.

    Gender and number agree with whatever was reformed, which says nothing
    about the reform; folding them to one spelling is what makes the field
    groupable. `F. DE E.` has no gender and is left alone.
    """
    accion = _normaliza(accion)
    if accion.startswith("F. DE E") or accion.startswith("F.DE E"):
        return "F. DE E."
    return re.sub(r"\b(\w+?)[OA]S?\b", r"\1O", accion)


def parse_fecha(texto: str) -> dt.date | None:
    """A D.O.F. date written in Spanish, or None when it cannot be read."""
    m = _FECHA_EN_PALABRAS.match(texto)
    if not m:
        return None
    mes = _normaliza(m.group("mes"))
    numero = _MESES.get(mes) or _MES_ALT.get(mes)
    if numero is None:
        return None
    try:
        return dt.date(int(m.group("anio")), numero, int(m.group("dia")))
    except ValueError:
        # A day the month does not have. The text said what it said; the
        # annotation is kept with `date=None` rather than shifted to a date
        # nobody wrote.
        return None


def es_anotacion(cuerpo: str) -> bool:
    """Whether the inside of a parenthesis is a reform annotation at all —
    which is to say, whether it opens with one of the known actions."""
    return ACCION.match(_normaliza(cuerpo)) is not None


def parse_annotation(raw: str, cuerpo: str | None = None) -> Annotation:
    """One annotation, from its own raw text.

    `raw` is kept whole and always: an annotation whose parts cannot be made
    out is recorded with `action=None` rather than dropped, so that #162's
    sweep can count what is still not understood. A discarded annotation
    would make that count a lie.
    """
    if cuerpo is None:
        m = re.search(r"\((?P<cuerpo>[^)]*)\)", raw, re.DOTALL)
        cuerpo = m.group("cuerpo") if m else raw
    texto = _normaliza(cuerpo)

    m = ACCION.match(texto)
    if not m:
        return Annotation(raw=raw)

    accion = normaliza_accion(m.group("acciones"))
    resto = m.group("resto").strip()

    fecha = None
    fuente = None
    m_fecha = _FECHA_DOF.search(resto)
    if m_fecha is None:
        # No gazette named. Only accepted if the tail parses as a whole date.
        candidato = _FECHA_SUELTA.search(resto)
        if candidato is not None and parse_fecha(candidato.group("fecha")):
            m_fecha = candidato
    if m_fecha:
        fecha = parse_fecha(m_fecha.group("fecha"))
        resto = resto[: m_fecha.start()]

    # A "POR <norma>" clause is the instrument that did the repealing, not a
    # description of what was repealed, so it belongs in `source` — whether
    # or not the annotation also cites a date, which many of these do.
    m_norma = _POR_NORMA.match(resto)
    if m_norma:
        fuente = m_norma.group("fuente").strip(" ,.;")
        resto = ""

    alcance = resto.strip(" ,.;") or None
    return Annotation(raw=raw, action=accion, scope=alcance, date=fecha, source=fuente)
