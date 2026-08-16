"""Unified entry point for reading a note or a day's index — and the one
place that is allowed to know dofjson.sidof (SIDOF) and dofjson.dofweb
(the DOF's own website) both exist. Every other function, in this package
(dofjson.archivo, dofjson.cli) or another one (nota2md, leyesmx...), calls
just this instead of juggling sidof/dofweb itself, which is the bug this
module exists to close: a caller that only ever calls ``sidof`` and never
considers that the day/note could be sitting in ``dofweb`` instead.

get_notas() also carries the day-level policy dofjson.archivo needs for its
whole-archive download (``respaldo``: whether an empty SIDOF answer is even
worth double-checking) and tags its result with `fuente` — FUENTE_SIDOF or
FUENTE_WEB — so a caller like archivo.procesar_dia() can tell which source
answered (and, from the shape of what comes back, whether it actually had
anything) without reaching for dofweb-specific knowledge of its own. Every
other name below (RESPALDO_OPCIONES, tiene_notas, consultar_respaldo,
cuenta_notas, PaginaDeOtroDia) is re-exported for exactly that: so archivo
and cli need nothing from dofjson.sidof/dofjson.dofweb directly either.
"""

import datetime as dt

import requests

from dofjson import dofweb, sidof
from dofjson.dofweb import PaginaDeOtroDia
from dofjson.notas import EDICION_LISTAS, quita_notas_sin_titulo

FUENTE_SIDOF = "sidof"
FUENTE_WEB = dofweb.FUENTE

#: How eagerly get_notas() double-checks an empty SIDOF answer against the
#: DOF website. "todos" (get_notas()'s own default) always checks. "habiles"
#: only checks Mon-Fri — dofjson.archivo's own default for its ~40,000-day
#: range, where a check is a real extra request and every confirmed loss so
#: far falls on a weekday anyway. "nunca" trusts SIDOF alone.
RESPALDO_OPCIONES = ("habiles", "todos", "nunca")

#: How many notes a get_notas()-shaped response carries, across every
#: edition — re-exported as-is; dofweb.py's own docstring covers it.
cuenta_notas = dofweb.cuenta_notas


def tiene_notas(notas: dict) -> bool:
    """Whether a get_notas()-shaped response carries any note at all."""
    return any(notas.get(clave) for clave in EDICION_LISTAS.values())


def _validar_respaldo(respaldo: str) -> None:
    if respaldo not in RESPALDO_OPCIONES:
        raise ValueError(f"respaldo debe ser uno de {RESPALDO_OPCIONES}, no {respaldo!r}")


def consultar_respaldo(fecha: dt.date, respaldo: str) -> bool:
    """Whether an empty SIDOF answer for this date is worth double-checking
    against the DOF website, per `respaldo` (see RESPALDO_OPCIONES)."""
    _validar_respaldo(respaldo)
    if respaldo == "nunca":
        return False
    if respaldo == "todos":
        return True
    return fecha.weekday() < 5


def get_nota(cod_nota: int) -> dict:
    """A note by its codNota, from SIDOF or — when SIDOF has no record of it
    at all — the DOF website (see dofjson.dofweb).

    Tagged with `fuente` (FUENTE_SIDOF or FUENTE_WEB), naming which source
    the returned record actually is — same convention as get_notas().

    SIDOF answers ``{"Nota": []}``, not an error, for a codNota it lacks —
    that empty answer is what sends the lookup to the website. Raises
    ValueError if neither source has the note.
    """
    nota = sidof.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        nota["fuente"] = FUENTE_SIDOF
        return nota

    nota = dofweb.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    raise ValueError(f"nota {cod_nota} does not exist in SIDOF nor in {FUENTE_WEB}")


def get_notas(date: dt.date, *, respaldo: str = "todos") -> dict:
    """A day's notes index, always tagged with `fuente` (FUENTE_SIDOF or
    FUENTE_WEB) naming which source the returned data actually is — not
    just which one has notes: a day with no edition (a weekend, a holiday,
    or one dofweb also reports nothing for) still comes back tagged
    FUENTE_SIDOF, since that is whose (empty) answer is being returned.

    `respaldo` controls when an empty SIDOF answer is worth double-checking
    against the DOF website (see RESPALDO_OPCIONES, and the days SIDOF
    loses, in dofjson's README) — "todos" (the default here) always checks;
    dofjson.archivo passes "habiles"/"nunca" for its own batch download.

    SIDOF's own 404 for a date outside its coverage is treated exactly like
    its ordinary 200-with-nothing answer — both mean "the fallback is worth
    a look" — instead of raising.

    Title-less stub entries (dofjson.notas.quita_notas_sin_titulo) are
    dropped either way, so what is left is real, browsable notes.
    """
    _validar_respaldo(respaldo)

    try:
        notas = quita_notas_sin_titulo(sidof.get_notas(date))
    except requests.exceptions.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        notas = {clave: [] for clave in EDICION_LISTAS.values()}

    if tiene_notas(notas):
        notas["fuente"] = FUENTE_SIDOF
        return notas

    if consultar_respaldo(date, respaldo):
        alterno = dofweb.get_notas(date)
        if dofweb.hay_publicacion(alterno):
            return quita_notas_sin_titulo(alterno)

    notas["fuente"] = FUENTE_SIDOF
    return notas
