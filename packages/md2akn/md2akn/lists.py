"""The inside of an article: fracciones, incisos and subincisos (issue #160).

.. code-block:: text

    | Mexican structure          | Akoma Ntoso |
    |----------------------------|-------------|
    | a plain paragraph          | `content`   |
    | fracción  (`**I.**`)       | `paragraph` |
    | inciso    (`**a)**`)       | `point`     |
    | subinciso (`**1.**`)       | `subpoint`  |
    | apartado  (`**A.**`)       | `level` + `refers_to="#apartado"` |

the same fracción→`paragraph` / inciso→`point` mapping used throughout this
package's vocabulary (see `md2akn.model`).

## Why this is not a bigger regex

The markers cannot be told apart in isolation, and no amount of pattern is
going to change that. `V.`, `X.`, `L.`, `C.`, `D.` and `M.` are all valid
Roman numerals *and* valid capital letters; `C.` in particular opens Mexican
documents ("El C. Primer Jefe del Ejército Constitucionalista…"). `**A.**`
occurs 14,834 times and is mostly an inciso, except in articles 2 and 123 of
the Constitution where it is an apartado. A `1.` at the start of a block is a
subinciso or the first word of a sentence that begins with a figure.

**What resolves almost all of it is consecutiveness.** A list can only be
*opened* by the first element of its series — `I`, `A`, `a`, `1` — and can
only be *continued* by a label that comes after the previous one in the same
series. A `V.` with no `I.`–`IV.` before it opens nothing and stays text; a
`C.` at the head of a document is not `A`, so it opens nothing either.

Continuation is deliberately monotonic rather than strictly successive,
because three real things break strict succession and none of them is an
error:

- **Repealed fracciones vanish from the text**, so the numbering jumps from
  `III.` to `V.`.
- **Latin suffixes** insert `VII Bis.` and `VII Ter.` between `VII.` and
  `VIII.`.
- **A second list in the same article** — a body's composition, then its
  members' eligibility — restarts at `I.`. That is a new list, not a
  numbering error, and the eId allocator disambiguates the repeated labels
  (see `EIdAllocator` in `md2akn.model`).

## Blocks with no marker

A block with no marker belongs to **the deepest open node**. A closing
paragraph of the article proper is indistinguishable by form from a further
paragraph of the last fracción, so the rule is uniform and the exception is
positional: only a block after which the article ends is treated as the
article's own tail. #162's sweep is what will say whether that is good
enough; it is not worth over-designing before the measurement exists.
"""

from __future__ import annotations

import re

from md2akn.model import REFERS_TO_APARTADO, AknNode
from md2akn.patterns import MARCADOR_LISTA, SUFIJO_LATINO

_ROMANO = re.compile(r"^[IVXLCDM]+$")
_ROMANO_MIN = re.compile(r"^[ivxlcdm]+$")
_VALOR_ROMANO = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_SUFIJO = re.compile(rf"^(?P<base>.+?)[ \t]+(?i:{SUFIJO_LATINO})\b(?:[ \t]+\d+)?$")

#: Series a marker can belong to, and the node each produces. `level` is not
#: here: an uppercase letter is an apartado or an inciso depending on context,
#: which `_tipo_de_nodo` decides, not the series.
SERIE_ROMANA = "romana"
SERIE_MAYUSCULA = "mayuscula"
SERIE_MINUSCULA = "minuscula"
SERIE_DIGITO = "digito"

#: The first label of each series — the only label that may *open* a list.
PRIMERO_DE_SERIE = {
    SERIE_ROMANA: 1,
    SERIE_MAYUSCULA: 1,
    SERIE_MINUSCULA: 1,
    SERIE_DIGITO: 1,
}


def valor_romano(etiqueta: str) -> int | None:
    """`etiqueta` as a number, or None when it is not a well-formed Roman
    numeral. Well-formed matters: `IIII` and `VX` are letters someone typed,
    not numerals, and admitting them would let noise open a list."""
    total = 0
    previo = 0
    for caracter in reversed(etiqueta.upper()):
        valor = _VALOR_ROMANO.get(caracter)
        if valor is None:
            return None
        total = total - valor if valor < previo else total + valor
        previo = max(previo, valor)
    return total or None


def analiza_etiqueta(etiqueta: str) -> tuple[str, int] | None:
    """`etiqueta` as ``(series, ordinal)`` — what kind of list it could
    belong to and where in it — or None when it belongs to none.

    A Latin suffix is stripped first: `VII Bis` sits at `VII`'s own ordinal,
    which is what makes it continue the list instead of breaking it.

    An uppercase label that is a valid Roman numeral is reported as Roman.
    That is the ambiguity, not a resolution of it: `_tipo_de_nodo` and the
    open-list state decide what it actually is.
    """
    m = _SUFIJO.match(etiqueta)
    if m:
        etiqueta = m.group("base")
    etiqueta = etiqueta.strip()
    if not etiqueta:
        return None
    if etiqueta.isdigit():
        return SERIE_DIGITO, int(etiqueta)
    if _ROMANO.match(etiqueta):
        valor = valor_romano(etiqueta)
        if valor is not None:
            return SERIE_ROMANA, valor
    if len(etiqueta) == 1 and etiqueta.isalpha():
        if etiqueta.isupper():
            return SERIE_MAYUSCULA, ord(etiqueta) - ord("A") + 1
        return SERIE_MINUSCULA, ord(etiqueta.lower()) - ord("a") + 1
    if _ROMANO_MIN.match(etiqueta):
        # `i)`, `ii)` — lowercase Roman is used for incisos, not fracciones,
        # so it is treated as the lowercase-letter series it visually is.
        valor = valor_romano(etiqueta)
        if valor is not None:
            return SERIE_MINUSCULA, valor
    return None


class Pendientes:
    """Annotations read but not yet attached to anything.

    An annotation **precedes** the node it describes, so it has to wait for
    that node to be created. It also carries the offset it was read at: the
    node it lands on has its span pulled back to there, so that the
    annotation's own text stays covered by the tree even though it produces
    no node of its own (issue #161, and the coverage invariant of #162).

    Shared between `md2akn.structure` and `ConstructorDeArticulo`, since
    either may be the one to create the node that claims them.
    """

    __slots__ = ("notas", "inicio")

    def __init__(self):
        self.notas = []
        self.inicio = None

    def agrega(self, anotacion, inicio: int) -> None:
        if self.inicio is None:
            self.inicio = inicio
        self.notas.append(anotacion)

    def toma(self, inicio: int):
        """``(start, annotations)`` for a node opening at `inicio`: the start
        pulled back over whatever was held, and the held annotations. Empties
        the holder."""
        if not self.notas:
            return inicio, []
        notas, propio = self.notas, self.inicio
        self.notas, self.inicio = [], None
        return min(inicio, propio), notas


class _Lista:
    """One open list: its series, the node its items hang off, and how far
    the numbering has got."""

    __slots__ = ("serie", "akn_type", "padre", "ultimo", "ultimo_nodo")

    def __init__(self, serie, akn_type, padre, ordinal, nodo):
        self.serie = serie
        self.akn_type = akn_type
        self.padre = padre
        self.ultimo = ordinal
        self.ultimo_nodo = nodo


class ConstructorDeArticulo:
    """Turns an article's own blocks into its children.

    One instance per article. The state it carries is the stack of open lists
    — which is exactly the state the ambiguity needs and a regex cannot have.
    """

    def __init__(self, articulo: AknNode, eids, node_span, doc, prefijo_eid,
                 pendientes=None):
        self._articulo = articulo
        self._pendientes = pendientes if pendientes is not None else Pendientes()
        self._vio_anotacion = False
        self._eids = eids
        self._node_span = node_span
        self._doc = doc
        self._prefijo = prefijo_eid
        self._pila: list[_Lista] = []
        self._contenidos_iniciales: list[AknNode] = []
        self._vio_lista = False
        #: Text blocks seen after a list item, held until it is known whether
        #: another item follows them -- see `_agrega_texto`. Not to be
        #: confused with `_pendientes`, which holds annotations.
        self._retenidos: list[tuple[object, AknNode]] = []

    # -- placing a block -------------------------------------------------

    def agrega(self, bloque, es_marcador_estructural: bool = False) -> None:
        """Place one of the article's blocks."""
        etiqueta, sep = (None, None)
        if not es_marcador_estructural:
            etiqueta, sep = _marcador(bloque)
        if etiqueta is None:
            self._agrega_texto(bloque)
            return
        analizada = analiza_etiqueta(etiqueta)
        if analizada is None:
            self._agrega_texto(bloque)
            return
        serie, ordinal = analizada
        lista = self._lista_para(serie, ordinal)
        if lista is None:
            self._agrega_texto(bloque)
            return
        self._agrega_item(lista, bloque, etiqueta, ordinal)

    def _agrega_texto(self, bloque) -> None:
        if self._pila:
            # Held, not placed. A block with no marker belongs to the deepest
            # open node *if another item follows it*, and is the article's own
            # closing paragraph if the article ends instead — and by form the
            # two are identical, so the decision has to wait for the next
            # block. `_descarga_pendientes` settles it one way and `cierra`
            # the other.
            nodo = self._pila[-1].ultimo_nodo
            # An annotation immediately above a marker-less block describes
            # *that block*, so it is claimed here rather than left waiting for
            # the next marker. Waiting made the next fracción pull its span
            # back over this block, which the current item had already claimed
            # — 89 laws' worth of `overlapping-siblings` in #162's sweep. No
            # pull-back is needed: the node opened before the annotation and
            # `_descarga_pendientes` grows it past the block.
            _, notas = self._pendientes.toma(nodo.start_char)
            nodo.notes.extend(notas)
            self._retenidos.append((bloque, nodo))
            return
        hijo = self._nuevo("content", self._articulo, bloque, None)
        if not self._vio_lista:
            self._contenidos_iniciales.append(hijo)

    def _descarga_pendientes(self) -> None:
        """Another list item followed, so the held blocks did belong to the
        item they came after."""
        for bloque, nodo in self._retenidos:
            _extiende(nodo, bloque.end, self._node_span, self._doc)
        self._retenidos.clear()

    def _lista_para(self, serie, ordinal) -> _Lista | None:
        """The list this item belongs to: an open one it continues, or a new
        one it opens — or None, meaning the marker is not a list item here
        and the block is text."""
        # Continue the innermost open list of the same series whose numbering
        # this item advances. Monotonic, not strictly successive: repealed
        # items leave gaps and Latin suffixes share an ordinal.
        for profundidad in range(len(self._pila) - 1, -1, -1):
            lista = self._pila[profundidad]
            if lista.serie == serie and ordinal >= lista.ultimo:
                del self._pila[profundidad + 1:]
                return lista
        return self._abre_lista(serie, ordinal)

    def _abre_lista(self, serie, ordinal) -> _Lista | None:
        """A new list, if this label may open one — only the first label of a
        series may. That single rule is what keeps `V.` with no `I.` before
        it, and the `C.` that opens "El C. Primer Jefe", from becoming list
        items."""
        if ordinal != PRIMERO_DE_SERIE[serie]:
            return None
        akn_type, padre = self._tipo_de_nodo(serie)
        if akn_type is None:
            return None
        lista = _Lista(serie, akn_type, padre, 0, None)
        if akn_type == "paragraph":
            # A fracción list restarting inside the same article is a sibling
            # of the first, not a continuation, so everything below it is
            # closed -- but *not* an enclosing apartado. Article 123's `A.`
            # contains its own fracciones and has to survive them, or its
            # `B.` finds no open list and stops being an apartado at all.
            while self._pila and self._pila[-1].akn_type != "level":
                self._pila.pop()
        self._pila.append(lista)
        return lista

    def _tipo_de_nodo(self, serie):
        """What a list of `serie` produces here, and whose child it is.

        This is where the two context rules of #160 live:

        - An **uppercase letter** is an *apartado* (`level`) when no fracción
          is open — that is the shape of articles 2 and 123 of the
          Constitution, where `A.` precedes the fracciones and contains them
          — and an *inciso* (`point`) when it appears under one.
        - A **digit** is a subinciso only when there is an open inciso to
          hang it under. Otherwise it is a sentence that begins with a
          figure, of which the tariff schedules alone hold tens of thousands.
        """
        abierto = self._pila[-1] if self._pila else None
        if serie == SERIE_ROMANA:
            return "paragraph", self._padre_de_fraccion()
        if serie == SERIE_MAYUSCULA:
            if abierto is None:
                return "level", self._articulo
            if abierto.akn_type == "paragraph":
                return "point", abierto.ultimo_nodo
            return None, None
        if serie == SERIE_MINUSCULA:
            if abierto is not None and abierto.akn_type in ("paragraph", "level"):
                return "point", abierto.ultimo_nodo
            return None, None
        if serie == SERIE_DIGITO:
            if abierto is not None and abierto.akn_type == "point":
                return "subpoint", abierto.ultimo_nodo
            return None, None
        return None, None

    def _padre_de_fraccion(self) -> AknNode:
        """A fracción hangs off the open apartado when there is one (article
        123's `A.` contains its own fracciones), and off the article
        otherwise."""
        for lista in self._pila:
            if lista.akn_type == "level" and lista.ultimo_nodo is not None:
                return lista.ultimo_nodo
        return self._articulo

    def _agrega_item(self, lista, bloque, etiqueta, ordinal) -> None:
        self._descarga_pendientes()
        nodo = self._nuevo(lista.akn_type, lista.padre, bloque, etiqueta)
        if lista.akn_type == "level":
            nodo.refers_to = REFERS_TO_APARTADO
        lista.ultimo = ordinal
        lista.ultimo_nodo = nodo
        self._vio_lista = True

    def marca_anotacion(self) -> None:
        """Told by the builder that an annotation was read inside this
        article. It makes the article keep its `content` children even with
        no list in it — otherwise the annotation would have no paragraph to
        attach to and would drift onto the next article."""
        self._vio_anotacion = True

    def _nuevo(self, akn_type, padre, bloque, num) -> AknNode:
        if akn_type == "content":
            # Numbered within its own parent, 1-based, in document order --
            # so an article's paragraphs are `art_1o__p_1`, `art_1o__p_2`,
            # and a paragraph inside a fracción restarts at 1 under it
            # (`art_1o__para_II__p_1`). Mexican citation counts an article's
            # own paragraphs, not the text inside its fracciones, so the
            # count that answers "el párrafo segundo del artículo 1o." is
            # the one scoped to the article (issue #181). Until then every
            # `content` was allocated the literal `"p"`, so two paragraphs
            # of one article proposed the *same* eId and only the
            # allocator's `_2` suffix kept them apart.
            num = str(1 + sum(1 for h in padre.children if h.akn_type == "content"))
        eid = self._eids.child(
            padre.eId, self._prefijo.get(akn_type, akn_type),
            num if num is not None else "p",
        )
        inicio, notas = self._pendientes.toma(bloque.start)
        nodo = AknNode(akn_type, self._node_span(self._doc, inicio, bloque.end, eid),
                       eId=eid, num=num, notes=notas)
        return padre.add(nodo)

    # -- closing ---------------------------------------------------------

    def cierra(self) -> None:
        """Mark the article's own leading and trailing paragraphs.

        The flags are only meaningful for an article that has both text and
        hierarchy — which is the shape Akoma Ntoso forbids and a Mexican
        article routinely has. An article of nothing but paragraphs needs
        none of them: there is no choice for a later XML conversion to make.
        It keeps its paragraphs all the same.

        Until issue #181 such an article had its children *cleared* here, on
        the reasoning that a lone `content` twin of the article would only
        double the tree. That optimizes the tree's shape at the cost of the
        citation the Mexican legal register actually uses: "el párrafo
        segundo del artículo 1o." is an ordinary reference, reforms are
        published against it ("se reforma el párrafo tercero del artículo
        4o."), and a unit that exists only when a sibling list happens to
        exist is not one anybody can cite against. So the paragraph is a
        unit of the tree unconditionally, and the asymmetry is gone.
        """
        if self._vio_lista:
            for nodo in self._contenidos_iniciales:
                nodo.is_chapeau = True
        # Whatever is still held ends the article, so it is the article's
        # own closing text rather than more of the last fracción.
        for bloque, _ in self._retenidos:
            self._nuevo("content", self._articulo, bloque, None).is_tail = True
        self._retenidos.clear()


def _marcador(bloque) -> tuple[str | None, str | None]:
    """The list label a block opens with, and its separator — or
    ``(None, None)``."""
    m = MARCADOR_LISTA.match(bloque.text.split("\n", 1)[0])
    if not m:
        return None, None
    return m.group("etiqueta"), m.group("sep")


def _extiende(nodo, fin, node_span, doc) -> None:
    actual = nodo
    while actual is not None:
        if actual.end_char < fin:
            actual.span = node_span(doc, actual.start_char, fin, actual.eId)
        actual = actual.parent
