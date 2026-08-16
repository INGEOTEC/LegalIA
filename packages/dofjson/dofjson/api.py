"""Unified entry point for reading a note or a day's index.

``client`` (SIDOF) is the primary source; ``dofweb`` (the DOF's own website)
covers the days and notes SIDOF's dataset is missing outright (see dofweb's
module docstring). Falling back from one to the other is correct behaviour,
but leaving every caller to juggle both modules on its own invites the bug
this module exists to close: a caller that only ever calls ``client`` and
never considers that the day/note could be sitting in ``dofweb`` instead.

``get_nota()`` and ``get_notas()`` do that fallback once, here, so every
other function — in this package or in another one (nota2md, leyesmx...) —
can call just these two instead of importing ``client`` and ``dofweb``
separately.
"""

import datetime as dt

from dofjson import client, dofweb

_LISTAS_NOTAS = ("NotasMatutinas", "NotasVespertinas", "NotasExtraordinarias")


def get_nota(cod_nota: int) -> dict:
    """A note by its codNota, from SIDOF or — when SIDOF has no record of it
    at all — the DOF website (see dofjson.dofweb).

    SIDOF answers ``{"Nota": []}``, not an error, for a codNota it lacks —
    that empty answer is what sends the lookup to the website. Raises
    ValueError if neither source has the note.
    """
    nota = client.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    nota = dofweb.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    raise ValueError(f"nota {cod_nota} does not exist in SIDOF nor in {dofweb.FUENTE}")


def get_notas(date: dt.date) -> dict:
    """A day's notes index, from SIDOF or — when SIDOF reports nothing for
    that day — the DOF website (see dofjson.dofweb and the days SIDOF loses).

    Title-less stub entries (dofjson.client.quita_notas_sin_titulo) are
    dropped either way, so what is left is real, browsable notes. SIDOF
    reports both a day with no edition and a day it has lost outright the
    same way — every list empty — so an empty answer is always checked
    against the website before being taken to mean nothing was published.
    """
    notas = client.quita_notas_sin_titulo(client.get_notas(date))
    if any(notas.get(clave) for clave in _LISTAS_NOTAS):
        return notas

    alterno = dofweb.get_notas(date)
    if dofweb.hay_publicacion(alterno):
        return client.quita_notas_sin_titulo(alterno)
    return notas
