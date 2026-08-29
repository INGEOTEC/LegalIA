"""Building the container/article hierarchy out of the blocks (issue #159).

The shape of a Mexican federal law, and the Akoma Ntoso element each part
becomes:

    act
    ├── preamble            "Al margen un sello...", the enacting formula
    ├── body
    │   ├── book / title / chapter / section     the containers, nesting by rank
    │   │   └── article
    │   ├── level  (refers_to="#apartado")       APARTADO A/B of article 123
    │   └── section (refers_to="#transitorios")  one per decree that added some
    └── conclusions         the closing signatures, where a law has them

Two of those are `refers_to` rather than elements of their own, because the
standard has neither: "transitorios" and "apartado". That is the same
convention `nota2md/akoma_ntoso.py` already adopted, deliberately — the two
packages say the same thing about the same gap without sharing a line of
code.

`preamble` is a **sibling** of `body`, not a child: Akoma Ntoso's `body`
admits only hierarchical elements, and the enacting formula is not one.

Containers nest by **precedence rank**, not by any depth marker in the text
(the text has none). On meeting a container, every open container of equal or
lower rank is closed. Three consequences worth stating, since each is a real
shape in the corpus rather than a hypothetical:

- A law that opens with `CAPÍTULO I` and only later reaches `TÍTULO PRIMERO`
  comes out with the chapter closed and the title starting fresh, rather than
  with a title nested inside a chapter. The chapter is left "hanging" at the
  top of the body, which is what the document actually says.
- Numbering that restarts — two `CAPÍTULO I` under different titles — needs
  no special handling at all: the eId is a path, so the two are
  `tit_PRIMERO__cap_I` and `tit_SEGUNDO__cap_I` and never collide.
- A container whose articles were all repealed keeps its node, with its span,
  as a leaf. An empty chapter is a fact about the law.

Fracciones and incisos inside an article are #160; the `(REFORMADO, ...)`
annotations are #161. Both are tolerated here — an annotation block is
recognized only so that it is never mistaken for an article's closing
paragraph — and neither is interpreted.
"""

from __future__ import annotations

import re

from md2akn.annotations import es_anotacion, parse_annotation
from md2akn.lists import ConstructorDeArticulo, Pendientes
from md2akn.model import (
    REFERS_TO_APARTADO,
    REFERS_TO_TRANSITORIOS,
    AknNode,
    EIdAllocator,
)
from md2akn.patterns import (
    ANOTACION,
    ANOTACION_EN_LINEA,
    ARTICULO,
    ARTICULO_ORDINAL,
    CONCLUSIONES,
    CONCLUSIONES_ULTIMOS_BLOQUES,
    CONTENEDOR,
    CONTENEDORES,
    DOF_TRANSITORIOS,
    MAX_EPIGRAFE,
    NOTA_EDITORIAL,
    PRECEDENCIA,
    SOLO_NEGRITAS,
    TRANSITORIOS,
)

#: eId prefixes, by `akn_type`. Akoma Ntoso's own abbreviations, and the same
#: three (`art_`, `para_`, `point_`) `nota2md/akoma_ntoso.py` already writes.
PREFIJO_EID = {
    "book": "book",
    "title": "tit",
    "chapter": "cap",
    "section": "sec",
    "level": "lvl",
    "article": "art",
    "paragraph": "para",
    "point": "point",
    "subpoint": "subpoint",
}

_ACENTOS = str.maketrans("ÁÉÍÓÚÜáéíóúü", "AEIOUUaeiouu")
_ESPACIOS = re.compile(r"\s+")


def _sin_acentos_mayusculas(texto: str) -> str:
    return texto.translate(_ACENTOS).upper()


def _limpia(texto: str) -> str:
    """A heading's text without its Markdown bold markers or its trailing
    punctuation — what a reader would call the epigraph."""
    return _ESPACIOS.sub(" ", texto.replace("**", "").strip()).strip(" .:")


def clasifica(bloque) -> tuple[str, dict]:
    """What a block is, from its first line alone, as ``(kind, fields)``.

    The order of the tests is the rule, not an implementation detail:

    - Annotations and editorial notes are checked **first**. `(DEROGADO, ...)`
      opens with a capitalized word and would otherwise be examined as prose;
      more importantly, `**(ADICIONADO CON LOS ARTÍCULOS QUE LO INTEGRAN, ...)`
      contains the word "ARTÍCULOS" and must never be read as an article.
    - Transitorios before containers, because `**ARTICULOS TRANSITORIOS**`
      would otherwise be examined as an article heading.
    - Containers before articles: no container word can open an article
      heading, so the order between those two is free, but keeping the
      broadest patterns first makes the sequence readable.
    """
    linea = bloque.text.split("\n", 1)[0]

    if NOTA_EDITORIAL.match(linea):
        return "nota_editorial", {}
    m = ANOTACION.match(linea)
    if m and es_anotacion(m.group("cuerpo")):
        return "anotacion", {"cuerpo": m.group("cuerpo")}
    if TRANSITORIOS.match(linea):
        return "transitorios", {}
    if DOF_TRANSITORIOS.match(linea):
        m = DOF_TRANSITORIOS.match(linea)
        return "transitorios_dof", {"num": _limpia(m.group("fecha"))}

    m = CONTENEDOR.match(linea)
    if m:
        clase = _sin_acentos_mayusculas(m.group("clase"))
        num = _limpia(m.group("num"))
        if clase in CONTENEDORES and num:
            return "contenedor", {"akn_type": CONTENEDORES[clase], "num": num}

    # Matched against the line with its bold markers removed. The CCF puts a
    # `**` in the middle of a number -- `**ARTICULO 410 **A.-`,
    # `**ARTICULO 103-**Bis.-` -- and reading those as plain "410" and "103"
    # collapsed six articles into two. Bold carries no information for an
    # article heading, so dropping it costs nothing; the patterns that do
    # rely on bold (the annotation and the bare-ordinal transitorio) are
    # matched against the raw line instead.
    m = ARTICULO.match(linea.replace("**", ""))
    if m:
        return "articulo", {"num": _limpia(m.group("num"))}

    m = ARTICULO_ORDINAL.match(linea)
    if m:
        # Only an article inside transitorios -- see `ARTICULO_ORDINAL` and
        # `_bloque_articulo_ordinal`. Reported as its own kind rather than as
        # an article so the builder can make that call with the context it
        # has and the classifier does not.
        return "articulo_ordinal", {"num": _limpia(m.group("num"))}

    return "contenido", {}


def _es_epigrafe(bloque, kind: str) -> bool:
    """Whether `bloque` is the epigraph of the container just opened.

    The corpus writes a container's title in the block *after* the container
    line, never on the same line::

        **CAPITULO I.**

        **DE LOS DERECHOS HUMANOS Y SUS GARANTIAS.**

    so the rule has to reach forward one block — and it has to be
    conservative, because reaching forward wrongly swallows the first
    paragraph of the law's text into a heading. It is taken only when the
    block is short, matched no other pattern, and either is entirely bold or
    is a single line that does not read as a sentence (no closing
    punctuation). That covers both the bold form above and the unadorned one
    the newer laws use (`Capítulo I` followed by `Del Código Nacional de
    Procedimientos Civiles y Familiares`).

    A bold epigraph may run to several lines — the corpus hard-wraps long
    ones, each line bolded separately — so "entirely bold" is checked line by
    line rather than over the block as a whole.
    """
    if kind != "contenido":
        return False
    texto = bloque.text
    if len(texto) > MAX_EPIGRAFE:
        return False
    lineas = [linea for linea in texto.splitlines() if linea.strip()]
    if lineas and all(SOLO_NEGRITAS.match(linea) for linea in lineas):
        return True
    if len(lineas) != 1:
        return False
    return not lineas[0].rstrip().endswith((".", ";", ":"))


def _rango_conclusiones(bloques) -> int:
    """Index of the first block of the closing signatures, or `len(bloques)`
    when the law has none — which is the common case: only 85 of the corpus'
    315 laws end with a rubric, the rest end at their last transitorio.

    Looked for only in the last few blocks (`CONCLUSIONES_ULTIMOS_BLOQUES`):
    the same words in the middle of a law are a decree being quoted, not this
    text being signed.
    """
    inicio = max(0, len(bloques) - CONCLUSIONES_ULTIMOS_BLOQUES)
    for i in range(inicio, len(bloques)):
        if CONCLUSIONES.search(bloques[i].text):
            return i
    return len(bloques)


class _Constructor:
    """The single pass over the blocks that builds the tree.

    Kept as a class only because the walk carries state — the stack of open
    containers, the article being filled, whether transitorios have started.
    One instance per document; nothing survives between documents.
    """

    def __init__(self, doc, meta, bloques):
        from md2akn.segmenter import node_span

        self._doc = doc
        self._node_span = node_span
        self._bloques = bloques
        self._eids = EIdAllocator()
        self._fin_cuerpo = _rango_conclusiones(bloques)

        inicio = bloques[0].start if bloques else 0
        fin = bloques[-1].end if bloques else 0
        self.act = AknNode(
            "act", self._span(inicio, fin, "act"), eId=self._eids.allocate("act"),
            meta=dict(meta),
        )
        self.body: AknNode | None = None
        self.preambulo: AknNode | None = None
        self.conclusiones: AknNode | None = None
        #: Open containers, outermost first, as (rank, node).
        self._pila: list[tuple[int, AknNode]] = []
        self._articulo: AknNode | None = None
        self._interior: ConstructorDeArticulo | None = None
        #: Annotations read but not yet attached: an annotation precedes the
        #: node it describes, so it waits for that node to exist.
        self._pendientes = Pendientes()
        self._transitorios: AknNode | None = None
        self._pendiente_epigrafe: AknNode | None = None

    # -- helpers ---------------------------------------------------------

    def _span(self, inicio, fin, label):
        return self._node_span(self._doc, inicio, fin, label)

    def _asegura_body(self, inicio):
        if self.body is None:
            self.body = self.act.add(
                AknNode("body", self._span(inicio, inicio, "body"),
                        eId=self._eids.allocate("body"))
            )
        return self.body

    def _padre_contenedor(self, inicio) -> AknNode:
        """Where a new article or container hangs: the deepest open
        container, or the body itself — many short laws have no containers at
        all, and an article with no chapter is not an error."""
        if self._transitorios is not None:
            return self._transitorios
        if self._pila:
            return self._pila[-1][1]
        return self._asegura_body(inicio)

    def _cierra_hasta(self, rango: int):
        while self._pila and self._pila[-1][0] >= rango:
            self._pila.pop()

    def _nuevo_nodo(self, akn_type, inicio, fin, num=None, refers_to=None, padre=None):
        padre = padre if padre is not None else self._padre_contenedor(inicio)
        eid = self._eids.child(
            "" if padre.akn_type in ("act", "body") else padre.eId,
            PREFIJO_EID.get(akn_type, akn_type),
            num if num is not None else akn_type,
        )
        inicio, notas = self._pendientes.toma(inicio)
        # The pull-back may not escape the parent, so the parent comes back
        # with it as far as it can. It cannot always: a law whose *title*
        # carries an annotation (`(REFORMADA SU DENOMINACIÓN, …)`, written
        # above the heading and so above the body) would drag the body back
        # over the preamble. Where the parent stops, so does the pull-back —
        # the annotation is still attached, only its characters stay with
        # whoever already held them.
        inicio = max(inicio, self._retrocede(padre, inicio))
        nodo = AknNode(
            akn_type, self._span(inicio, fin, eid), eId=eid, num=num, refers_to=refers_to,
            notes=notas,
        )
        return padre.add(nodo)

    # -- the pass --------------------------------------------------------

    def run(self) -> AknNode:
        for i, bloque in enumerate(self._bloques):
            if i >= self._fin_cuerpo:
                self._agrega_conclusiones(bloque)
                continue
            kind, campos = clasifica(bloque)

            if self._pendiente_epigrafe is not None:
                contenedor = self._pendiente_epigrafe
                self._pendiente_epigrafe = None
                if _es_epigrafe(bloque, kind):
                    contenedor.heading = _limpia(bloque.text)
                    self._extiende(contenedor, bloque.end)
                    continue

            manejador = getattr(self, f"_bloque_{kind}", self._bloque_contenido)
            manejador(bloque, campos)

        self._cierra_articulo()
        # An annotation with no node after it — the last thing in the file —
        # hangs off the act rather than being lost.
        _, notas = self._pendientes.toma(self.act.start_char)
        self.act.notes.extend(notas)
        # An `act` always has a `body`, even an empty document's, so that
        # consumers never have to special-case its absence. It is created
        # last when nothing needed it earlier, which also keeps it after the
        # `preamble` in document order.
        self._asegura_body(self.act.start_char)
        self._cierra_spans()
        return self.act

    def _bloque_contenedor(self, bloque, campos):
        akn_type = campos["akn_type"]
        # A container ends whatever transitorios were open: transitional
        # provisions are the tail of the law, and a container after them
        # belongs to a further decree's own text.
        self._transitorios = None
        self._cierra_articulo()
        self._cierra_hasta(PRECEDENCIA[akn_type])
        refers_to = REFERS_TO_APARTADO if akn_type == "level" else None
        nodo = self._nuevo_nodo(
            akn_type, bloque.start, bloque.end, num=campos["num"], refers_to=refers_to,
        )
        self._pila.append((PRECEDENCIA[akn_type], nodo))
        self._pendiente_epigrafe = nodo

    def _bloque_articulo(self, bloque, campos):
        self._cierra_articulo()
        self._articulo = self._nuevo_nodo(
            "article", bloque.start, bloque.end, num=campos["num"],
        )
        self._interior = ConstructorDeArticulo(
            self._articulo, self._eids, self._node_span, self._doc, PREFIJO_EID,
            self._pendientes,
        )
        # An annotation written inside the heading itself belongs to the
        # article, not to whatever comes next.
        for m in ANOTACION_EN_LINEA.finditer(bloque.text.split("\n", 1)[0]):
            if es_anotacion(m.group("cuerpo")):
                self._articulo.notes.append(
                    parse_annotation(m.group(0), m.group("cuerpo"))
                )
        # The heading block is the article's own opening text -- "Artículo 4.
        # Son obligaciones:" -- so it is placed as text, never examined for a
        # list marker it cannot carry.
        self._interior.agrega(bloque, es_marcador_estructural=True)

    def _cierra_articulo(self):
        """Finish the article that was open, if any: the list machine only
        knows where the article's closing paragraphs are once something else
        has started."""
        if self._interior is not None:
            self._interior.cierra()
        self._articulo = None
        self._interior = None

    def _bloque_articulo_ordinal(self, bloque, campos):
        # `**Primero.**` numbers a transitorio provision; outside
        # transitorios the same shape is an ordinary bolded paragraph opener
        # and must stay content.
        if self._transitorios is None:
            self._bloque_contenido(bloque, campos)
            return
        self._bloque_articulo(bloque, campos)

    def _bloque_transitorios(self, bloque, campos):
        self._abre_transitorios(bloque, campos.get("num"))

    def _bloque_transitorios_dof(self, bloque, campos):
        # A `**D.O.F. <fecha>.**` header only opens a new block of transitional
        # provisions once inside transitorios; before them, in the body, the
        # same shape is an ordinary paragraph.
        if self._transitorios is None:
            self._bloque_contenido(bloque, campos)
            return
        self._abre_transitorios(bloque, campos.get("num"))

    def _abre_transitorios(self, bloque, num):
        # Several sibling sections, one per decree that added transitional
        # provisions -- never merged, since which decree a provision belongs
        # to is exactly what the separation records.
        self._pila.clear()
        self._cierra_articulo()
        self._transitorios = None
        self._transitorios = self._nuevo_nodo(
            "section", bloque.start, bloque.end,
            num=num or "transitorios",
            refers_to=REFERS_TO_TRANSITORIOS,
            padre=self._asegura_body(bloque.start),
        )

    def _bloque_contenido(self, bloque, campos, es_marcador_estructural=False):
        if self._articulo is not None:
            self._extiende(self._articulo, bloque.end)
            self._interior.agrega(bloque, es_marcador_estructural)
            return
        if self._pila or self._transitorios is not None or self.body is not None:
            destino = self._padre_contenedor(bloque.start)
            self._nuevo_nodo("content", bloque.start, bloque.end, padre=destino)
            return
        # Nothing structural has opened yet, so this is the preamble.
        if self.preambulo is None:
            self.preambulo = self.act.add(
                AknNode("preamble", self._span(bloque.start, bloque.end, "preamble"),
                        eId=self._eids.allocate("preamble"))
            )
        else:
            self._extiende(self.preambulo, bloque.end)

    # Annotations and editorial notes are not nodes: their text belongs to
    # whatever node they precede, which is what keeps #162's coverage
    # invariant true. #161 turns the first kind into `AknNode.notes`.
    #
    # They are handed on as structural markers so that the list machine never
    # examines one for a fracción label. `**(REFORMADO...` cannot match the
    # marker pattern today, but a list broken by an annotation would be a
    # silent, hard-to-find failure, and saying so here costs one argument.
    def _bloque_anotacion(self, bloque, campos):
        # Annotations are not nodes and never appear in `walk()`. The block is
        # held instead, and attaches to the next node created — which also
        # pulls that node's span back to cover the annotation's own text, so
        # the document stays fully covered.
        self._pendientes.agrega(
            parse_annotation(bloque.text, campos.get("cuerpo")), bloque.start
        )
        # The open article is deliberately *not* grown over the annotation
        # here. Whatever claims it — a fracción of this article, or the next
        # article — pulls its own span back over it, and `_cierra_spans`
        # grows the ancestors afterwards; growing the article eagerly as well
        # made it overlap the sibling that then claimed the same characters
        # (201 laws' worth of `overlapping-siblings` in #162's first sweep).
        if self._articulo is not None:
            self._interior.marca_anotacion()

    def _bloque_nota_editorial(self, bloque, campos):
        self._bloque_contenido(bloque, campos, es_marcador_estructural=True)

    def _agrega_conclusiones(self, bloque):
        if self.conclusiones is None:
            self.conclusiones = self.act.add(
                AknNode("conclusions", self._span(bloque.start, bloque.end, "conclusions"),
                        eId=self._eids.allocate("conclusions"))
            )
        else:
            self._extiende(self.conclusiones, bloque.end)

    # -- spans -----------------------------------------------------------

    def _retrocede(self, nodo: AknNode, inicio: int) -> int:
        """Grow `nodo` and its ancestors backwards to `inicio` where they can,
        and return the earliest offset actually reached.

        A node may only move back as far as its own previous sibling's end:
        past that, growing it would make two siblings claim the same
        characters. The chain stops at the first ancestor that cannot move,
        since an ancestor that stays put pins everything under it.
        """
        cadena = []
        actual = nodo
        while actual is not None and actual.start_char > inicio:
            cadena.append(actual)
            actual = actual.parent

        piso = inicio
        for hijo in cadena[::-1]:
            padre = hijo.parent
            if padre is not None:
                # `h is not hijo` matters: a container opens with an empty
                # span, so without it a node counts as its own previous
                # sibling and can never move.
                previos = [
                    h for h in padre.children
                    if h is not hijo and h.end_char <= hijo.start_char
                ]
                if previos:
                    piso = max(piso, max(h.end_char for h in previos))
                piso = max(piso, padre.start_char)
            if hijo.start_char <= piso:
                return hijo.start_char
            hijo.span = self._span(piso, hijo.end_char, hijo.eId)
        return piso

    def _extiende(self, nodo: AknNode, fin: int):
        """Grow `nodo`'s span to `fin`, and every ancestor's with it — a
        parent always covers its children."""
        actual = nodo
        while actual is not None:
            if actual.end_char < fin:
                actual.span = self._span(actual.start_char, fin, actual.eId)
            actual = actual.parent

    def _cierra_spans(self):
        """Grow every node to cover its children, bottom up.

        A container's span opens at its own heading and has to reach the end
        of its last article — which is not known when the container opens,
        since it is a fact about blocks not yet read. Doing it here, in one
        post-order pass, is what makes "a parent covers its children" true of
        every node rather than only of the ones content was appended to.
        """
        def cierra(nodo: AknNode) -> int:
            fin = nodo.end_char
            for hijo in nodo.children:
                fin = max(fin, cierra(hijo))
            if fin > nodo.end_char:
                nodo.span = self._span(nodo.start_char, fin, nodo.eId)
            return fin

        cierra(self.act)


def build(doc, meta, bloques) -> AknNode:
    """`doc`'s law as a tree — the entry point `md2akn.segmenter` calls."""
    return _Constructor(doc, meta, bloques).run()
