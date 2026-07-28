"""Build each international treaty's history from the DOF.

Neither LeyesBiblio nor the SRE's registry can serve as the spine here.
LeyesBiblio does not carry treaties, and `cja.sre.gob.mx/tratadosmexico`, the
official register, answers a Radware bot-management challenge rather than data.
So, as with the NOMs, the gazette is read directly — but a treaty has no code,
only a name, which is what makes this harder than the NOMs and less certain
than the laws.

A treaty reaches the DOF as two decrees, months or years apart:

    DECRETO por el que se aprueba el Convenio entre los Estados Unidos
    Mexicanos y la República de Corea, para evitar la Doble Imposición…   (Senate)
    DECRETO de promulgación del Convenio entre los Estados Unidos Mexicanos
    y la República de Corea para Evitar la Doble Imposición…              (Executive)

Pairing them is the whole problem, and it is why the two decrees are matched
with `similitud`, not compared verbatim: the same instrument is worded
differently each time — "2007" against "dos mil siete", "dado en Madrid"
against "adoptado en Madrid".

Publishing both decrees is a recent practice. Before the 1980s the gazette ran
only one of the two, so most older treaties legitimately have a single note and
no counterpart to find; the pairing rate climbs from 0% in the 1970s to about
half in the 2010s. A treaty with one note is the norm, not a failure.
"""

import collections
import math
import re
import unicodedata

_PROMULGACION = re.compile(
    r"^\s*DECRETO\s+(?:Promulgatorio|de\s+Promulgaci[oó]n)\s*(?:de\s+la|del?|de)?\s*",
    re.I,
)
_APROBACION = re.compile(
    r"^\s*DECRETO\s+(?:por\s+el\s+que\s+se\s+)?aprueba\s*(?:el|la|los|las)?\s*", re.I
)
#: A promulgation decree is a treaty's by its own wording. An approval decree is
#: not — the Senate approves other things too — so it must also name one of the
#: forms an international instrument takes.
_INSTRUMENTO = re.compile(
    r"\b(?:Tratado|Acuerdo|Convenio|Convenci[oó]n|Protocolo|Enmienda|Canje)\b", re.I
)

APROBACION, PROMULGACION = "aprobacion", "promulgacion"

#: How much of the two names' distinguishing content must agree. Treaty names
#: are formulaic — "convenio entre el gobierno de los estados unidos mexicanos y
#: el gobierno de la república de X para…" — so plain string similarity is
#: dominated by the boilerplate and rates unrelated instruments highly: it gave
#: 0.88 to a 1977 trade agreement paired with a 1994 framework agreement, above
#: what it gave real pairs. Weighting each word by how rare it is puts that
#: false pair at 0.56 and the real ones at 0.72-0.78.
MINIMO = 0.70


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normaliza(nombre: str) -> str:
    """Lower-cased, accent- and punctuation-free form used for comparison."""
    limpio = _sin_acentos(nombre).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", limpio)).strip()


def clasifica(titulo: str) -> tuple[str, str] | None:
    """`(tipo, nombre del instrumento)` for a treaty decree, else None.

    The name is the title with the decree formula stripped, which is what the
    two decrees of one treaty have in common.
    """
    if _PROMULGACION.match(titulo):
        return PROMULGACION, _PROMULGACION.sub("", titulo, count=1).strip()
    if _APROBACION.match(titulo) and _INSTRUMENTO.search(titulo):
        return APROBACION, _APROBACION.sub("", titulo, count=1).strip()
    return None


def decretos(notas) -> list[dict]:
    """Every treaty decree in a dofjson title stream, as
    `{tipo, nombre, nota}`."""
    encontrados = []
    for nota in notas:
        clase = clasifica(nota.get("titulo") or "")
        if clase is None:
            continue
        tipo, nombre = clase
        encontrados.append({"tipo": tipo, "nombre": nombre, "nota": nota})
    return encontrados


class Pesos:
    """How rare each word is across the treaty names seen.

    The discriminating part of a treaty's name is its rare words — the
    counterparty, the subject — while "gobierno", "estados", "unidos" and
    "convenio" are in hundreds of them and say nothing about which treaty
    this is.
    """

    def __init__(self, nombres):
        nombres = [normaliza(n) for n in nombres]
        self.total = len(nombres) or 1
        self.frecuencia = collections.Counter()
        for nombre in nombres:
            self.frecuencia.update(set(nombre.split()))

    def peso(self, palabra: str) -> float:
        # The 1 + … keeps this positive. Plain log(N / (1 + df)) turns negative
        # for a word present in nearly every name — which is most of the
        # formula — and a negative weight makes the overlap below meaningless.
        return math.log(1 + self.total / (1 + self.frecuencia[palabra]))

    def similitud(self, a: str, b: str) -> float:
        """Rarity-weighted overlap of two names, in [0, 1].

        The sums run over sorted words, not over the sets themselves. Set
        iteration order follows string hashing, which is randomized per process,
        so summing in that order made the same pair of names score
        0.9999999999999998 on one run and 1.0 on the next. Floating-point
        addition is not associative, and the release only needs re-uploading
        when the data actually changed — a score that wobbles between runs
        could reorder near-tied pairs and make an unchanged collection look
        different.
        """
        pa, pb = set(normaliza(a).split()), set(normaliza(b).split())
        if not pa or not pb:
            return 0.0
        union = sum(self.peso(p) for p in sorted(pa | pb))
        return sum(self.peso(p) for p in sorted(pa & pb)) / union if union else 0.0


def _orden(nota: dict) -> tuple:
    f = nota.get("fecha") or "01-01-1900"
    return (f[-4:], f[3:5], f[:2])


def empareja(decretos_: list[dict], minimo: float = MINIMO) -> list[dict]:
    """Group the decrees into treaties, oldest first.

    Names that agree exactly are grouped first and taken as certain. The rest
    are paired approval-to-promulgation by rarity-weighted similarity, best
    first, each decree claimed once, and never with a promulgation that
    precedes its approval. Whatever stays unpaired becomes a treaty of its own
    note — which for most older treaties is the truth, not a miss.

    Returns `[{nombre, notas, certeza}]`, `certeza` being "exacta" for a name
    match, the score for a paired one, and None for a lone decree.
    """
    grupos: list[dict] = []
    por_nombre: dict[str, list[dict]] = collections.defaultdict(list)
    for d in decretos_:
        por_nombre[normaliza(d["nombre"])].append(d)

    sueltos = []
    for _clave, iguales in por_nombre.items():
        tipos = {d["tipo"] for d in iguales}
        if len(iguales) > 1 and tipos == {APROBACION, PROMULGACION}:
            grupos.append({"nombre": iguales[0]["nombre"],
                           "notas": [d["nota"] for d in iguales],
                           "certeza": "exacta"})
        else:
            sueltos.extend(iguales)

    pesos = Pesos([d["nombre"] for d in sueltos])
    aprobaciones = [d for d in sueltos if d["tipo"] == APROBACION]
    promulgaciones = [d for d in sueltos if d["tipo"] == PROMULGACION]

    pares = []
    for i, a in enumerate(aprobaciones):
        for j, p in enumerate(promulgaciones):
            if _orden(p["nota"]) < _orden(a["nota"]):
                continue
            s = pesos.similitud(a["nombre"], p["nombre"])
            if s >= minimo:
                pares.append((s, i, j))
    pares.sort(key=lambda t: -t[0])

    usadas: set[int] = set()
    usadas_p: set[int] = set()
    for s, i, j in pares:
        if i in usadas or j in usadas_p:
            continue
        usadas.add(i)
        usadas_p.add(j)
        grupos.append({"nombre": aprobaciones[i]["nombre"],
                       "notas": [aprobaciones[i]["nota"], promulgaciones[j]["nota"]],
                       "certeza": round(s, 3)})

    for i, a in enumerate(aprobaciones):
        if i not in usadas:
            grupos.append({"nombre": a["nombre"], "notas": [a["nota"]], "certeza": None})
    for j, p in enumerate(promulgaciones):
        if j not in usadas_p:
            grupos.append({"nombre": p["nombre"], "notas": [p["nota"]], "certeza": None})

    for g in grupos:
        g["notas"].sort(key=_orden)
    grupos.sort(key=lambda g: (_orden(g["notas"][0]), g["nombre"]))
    return grupos


def historia(grupo: dict) -> list[int]:
    """The codNota of a treaty's decrees, oldest first."""
    return [n["codNota"] for n in grupo["notas"]]


def catalogo(grupos: list[dict]) -> list[dict]:
    """One entry per treaty: name, dates, note count and how it was grouped."""
    return [
        {
            "nombre": g["nombre"][:240],
            "notas": len(g["notas"]),
            "desde": g["notas"][0].get("fecha"),
            "hasta": g["notas"][-1].get("fecha"),
            "certeza": g["certeza"],
        }
        for g in grupos
    ]
