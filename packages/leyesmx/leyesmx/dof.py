"""Match each reform decree to the DOF note that published it.

Diputados says *which* decrees reformed a law and *when*; `dofjson`'s titles
dataset says which `codNota` carries each note. Joining the two on the
publication date yields, per law, the list of DOF notes that changed it —
which is what makes a law's evolution traceable back to primary sources.

The join is by date plus title similarity, because a single day carries
dozens of notes (and occasionally two reforms to the same law). Diputados
appends an editorial summary to the decree's title, so the DOF title is
typically a *prefix* of it; similarity is measured over that prefix only.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

_NO_ALNUM = re.compile(r"[^a-z0-9 ]")
_ESPACIOS = re.compile(r"\s+")


def normaliza(texto: str) -> str:
    """Lower-cased, accent- and punctuation-free form used for comparison."""
    sin_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return _ESPACIOS.sub(" ", _NO_ALNUM.sub(" ", sin_acento.lower())).strip()


def similitud(decreto: str, titulo_dof: str) -> float:
    """How well a DOF title matches a Diputados decree, in [0, 1].

    1.0 when the whole DOF title appears verbatim inside the Diputados text,
    which is the common case once its editorial summary is accounted for.
    Containment is checked over the entire title, not a prefix: two reforms
    published the same day can differ only in a trailing parenthetical, as on
    17-05-2021 (article 43, "Michoacán de Ocampo" vs "Veracruz").
    """
    a, b = normaliza(decreto), normaliza(titulo_dof)
    if not b:
        return 0.0
    if b in a:
        return 1.0
    return SequenceMatcher(None, a[: len(b)], b).ratio()


_PALABRA_CORTA = 3


def similitud_nombre(nombre: str, titulo_dof: str) -> float:
    """How much of an instrument's *name* the DOF title mentions, in [0, 1].

    For regulations there is no decree title to compare against: LeyesBiblio
    gives only the regulation's name, so `similitud()` — which expects the DOF
    title to be a prefix of a longer Diputados text — has it backwards and
    scores unrelated notes as well as real ones. Here the question is the other
    way round: does this DOF title name this regulation?

    1.0 when the whole name appears in the title; otherwise the fraction of the
    name's words that do, ignoring words too short to carry meaning.
    """
    a, b = normaliza(nombre), normaliza(titulo_dof)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    palabras = [p for p in a.split() if len(p) > _PALABRA_CORTA]
    if not palabras:
        return 0.0
    return sum(p in b for p in palabras) / len(palabras)


@dataclass
class ReformaEnlazada:
    """A reform decree together with the DOF note that published it."""

    ley: str
    no: int | None
    fecha: str
    codNota: int | None
    titulo_dof: str
    decreto_dip: str
    confianza: float

    @property
    def enlazada(self) -> bool:
        return self.codNota is not None


def notas_por_fecha(notas, fechas) -> dict:
    """Group an iterable of dofjson records by `fecha`, keeping only `fechas`."""
    fechas = set(fechas)
    porf: dict[str, list] = {}
    for nota in notas:
        if nota.get("fecha") in fechas:
            porf.setdefault(nota["fecha"], []).append(nota)
    return porf


def enlaza(reformas, notas) -> list[ReformaEnlazada]:
    """Pair every reform with the best-matching DOF note published that day.

    `notas` is any iterable of dofjson title records (see
    `dofjson.titulos.download_titulos`). Grouping it costs one full pass, so
    when linking many laws against the same dataset, group once with
    `notas_por_fecha()` and call `enlaza_agrupadas()` per law instead.
    """
    reformas = list(reformas)
    return enlaza_agrupadas(
        reformas, notas_por_fecha(notas, (r.fecha for r in reformas))
    )


def puntua_entrada(reforma, titulo_dof: str, por_nombre: bool = False) -> float:
    """Score a candidate DOF note for one entry.

    Which metric applies depends on what LeyesBiblio gives for the entry. A
    numbered reform of a law comes with the decree's own title, which
    `similitud()` compares directly. An original publication comes with no
    title at all — only the instrument's name — and so does every entry of a
    regulation, and for those the question is whether the DOF title *names*
    the instrument (`similitud_nombre()`).

    Using `similitud()` where only a name is available is not merely weaker,
    it is wrong: it scored the Ley Federal del Trabajo's 1970 publication
    against a Mexico City traffic-regulation decree, and the Código Fiscal's
    against the 1982 budget.
    """
    if por_nombre or reforma.no is None:
        return similitud_nombre(reforma.decreto, titulo_dof)
    return similitud(reforma.decreto, titulo_dof)


def enlaza_agrupadas(reformas, porf: dict, por_nombre: bool = False,
                     minimo_nombre: float = 0.6) -> list[ReformaEnlazada]:
    """As `enlaza()`, over notes already grouped by date.

    A reform whose date has no note in the dataset comes back with
    `codNota=None` rather than being dropped: a missing note is a fact about
    the source worth surfacing, not an error.

    Within one law each note is claimed by at most one reform, since several
    reforms to the same law can share a publication date and each has its own
    note; pairs are assigned best-score-first so the clearest match wins.
    Across laws there is no such exclusivity, and there must not be: one
    decree routinely amends several laws at once, so the same codNota
    legitimately appears in more than one law's list.

    `por_nombre` says that only the instrument's name is available for every
    entry, as it is for regulations (see `puntua_entrada`). `minimo_nombre` is
    the score below which a name-matched entry is left unlinked: a busy day
    carries a hundred notes, and half a name's words matching is as likely to
    be coincidence as not — an unlinked entry says less than a wrong one.
    """
    reformas = list(reformas)
    pares = [
        (puntua_entrada(r, n.get("titulo", ""), por_nombre), i, n,
         por_nombre or r.no is None)
        for i, r in enumerate(reformas)
        for n in porf.get(r.fecha, [])
    ]
    pares.sort(key=lambda p: -p[0])

    asignada: dict[int, dict] = {}
    puntaje: dict[int, float] = {}
    tomadas: set[int] = set()
    for s, i, n, por_nombre_i in pares:
        if i in asignada or n["codNota"] in tomadas or s <= 0:
            continue
        if por_nombre_i and s < minimo_nombre:
            continue
        asignada[i], puntaje[i] = n, s
        tomadas.add(n["codNota"])

    enlazadas = []
    for i, r in enumerate(reformas):
        n = asignada.get(i)
        enlazadas.append(
            ReformaEnlazada(
                ley=r.ley, no=r.no, fecha=r.fecha,
                codNota=n["codNota"] if n else None,
                titulo_dof=n["titulo"] if n else "",
                decreto_dip=r.decreto, confianza=round(puntaje.get(i, 0.0), 3),
            )
        )
    return enlazadas
