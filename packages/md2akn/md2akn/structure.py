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
standard has neither: "transitorios" and "apartado".

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

import bisect
import re

from md2akn.annotations import es_anotacion, parse_annotation
from md2akn.classify import (  # noqa: F401  (`clasifica`/`modo_sin_articulos` are re-exported: the tests import them from here)
    MODO_NUMERAL,
    MODO_ORDINAL,
    _limpia,
    clasifica,
    es_etiqueta_de_tabla,
    es_fila_tabla,
    modo_sin_articulos,
)
from md2akn.divisions import (
    ARTICULO_ORDINAL_MIXTO,
    ENCABEZADO_APENDICE_RR,
    ENCABEZADO_ARTICULO_RR,
    PROVISION_RR,
    encabezado_debil,
    encabezado_fuerte,
    inicio_de_la_serie,
)
from md2akn.hits import hit
from md2akn.lists import ConstructorDeArticulo, Pendientes
from md2akn.model import (
    REFERS_TO_ANEXO,
    REFERS_TO_APARTADO,
    REFERS_TO_APENDICE,
    REFERS_TO_CONCLUSIONES,
    REFERS_TO_DIVISION,
    REFERS_TO_FORMULARIO,
    REFERS_TO_NOTA,
    REFERS_TO_PROMULGACION,
    REFERS_TO_TRANSITORIOS,
    AknNode,
    EIdAllocator,
)
from md2akn.outline import es_linea_de_etiqueta
from md2akn.patterns import (
    ANOTACION_EN_LINEA,
    ARTICULO,
    CONSIDERANDO,
    LEYENDA_TABLA,
    MARCADOR_DISPOSITIVO,
    MAX_EPIGRAFE,
    NUMERAL_DECIMAL,
    ORDINAL_PALABRA,
    PRECEDENCIA,
    PRECEDENCIA_PARTE_EN_CAPITULO,
    SOLO_NEGRITAS,
)
from md2akn.skeleton import (  # noqa: F401  (`rangos_de_indice` is re-exported for the callers that import it from here)
    BLOQUES_CON_ROTULO,
    BLOQUES_DE_UN_SOLO_TEXTO,
    ROLES_SIN_SECCION,
    Skeleton,
    _forma_comparable,
    es_etiqueta_repetida,
    rangos_de_indice,
)

#: eId prefixes, by `akn_type` — Akoma Ntoso's own abbreviations.
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
    # A paragraph is a citable unit of its own ("el párrafo segundo del
    # artículo 1o.", and reforms published against it) -- issue #181.
    "content": "p",
    "table": "table",
    # Review fix-1 of #291 (rules 15 and 16).
    "part": "part",
    "attachment": "att",
    "toc": "toc",
    # Review fix-4 of #291 (rules 29 to 31): a labelled block or a division.
    "hcontainer": "hc",
}

#: The eId prefix of an appendix (rule 26, review fix-3 of #291): AKN types it as
#: an `attachment`, like an annex, and `refersTo` tells them apart.
PREFIJO_EID_APENDICE = "app"

_ORDINAL_PALABRA = re.compile(ORDINAL_PALABRA, re.IGNORECASE | re.UNICODE)

#: Rule 18 (review fix-1 of #291) switch: a whole numeral that starts over is a
#: list item. `True` is the rule; the one script that measures what each rule of
#: the fix contributed (`scripts/embeddings/measure_repeated_numbers.py`) turns it
#: off to see the number repeat again. Not a parameter of the public API.
REINICIO_ES_ITEM = True


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
    # A table row is the container's own first table, never its title: a
    # single line that does not end in a full stop would otherwise pass the
    # test below (issue #291).
    if es_fila_tabla(bloque):
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


_LETRA_DE_LISTA = re.compile(r"\(?[a-z]{1,2}[).]")


def _es_valor_en_minuscula(resto: str) -> bool:
    """Whether the text after a numeral starts with a lower-case word (`1.1.
    cuando ...`) rather than with a capital, a list letter (`a)`) or nothing
    (rule 31, review fix-4 of #291): ``" cuando las propiedades"`` is, ``" a) Por
    el Grupo"`` and ``" Queda constituido"`` are not.
    """
    resto = resto.lstrip()
    return resto[:1].islower() and _LETRA_DE_LISTA.match(resto) is None


class _Constructor:
    """The single pass over the blocks that builds the tree.

    Kept as a class only because the walk carries state — the stack of open
    containers, the article being filled, whether transitorios have started.
    One instance per document; nothing survives between documents.
    """

    def __init__(self, doc, meta, bloques, skeleton):
        from md2akn.segmenter import node_span

        self._doc = doc
        self._node_span = node_span
        self._bloques = bloques
        #: The first phase's answer (`md2akn.skeleton`): what each API block is,
        #: decided from the markers before any block is placed in the tree.
        self._sk = skeleton
        self._nodos_division: set[AknNode] = set()
        self._formularios: set[AknNode] = set()
        #: The block each article was opened at, ``{eId: block index}`` -- what a
        #: later pass needs to place a division in front of a restarted series.
        self._bloque_de_articulo: dict[str, int] = {}
        self._eids = EIdAllocator()

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
        #: The table (rule 13, issue #291) the previous block belonged to, as
        #: long as the blocks that follow are still its rows; any other block
        #: ends it.
        self._tabla: AknNode | None = None
        #: Rule 36 (review fix-6 of #291): the table opened at its caption, if
        #: that is the one `_tabla` is.
        self._tabla_con_leyenda: AknNode | None = None
        #: Where the marker line not yet claimed by a node starts, so that
        #: node's span is pulled back over it (the way an annotation's is).
        self._marca_inicio: int | None = None
        #: The tables of contents, still to be placed (a copy: the pass consumes it).
        self._indices = dict(skeleton.indices)
        #: Rule 17 (review fix-1 of #291): inside an instrument's recitals,
        #: whose numbered `PRIMERO.`/`1.` paragraphs are not provisions.
        self._en_considerandos = False
        self._recien_abiertos = False
        # Rule 18 (review fix-1 of #291): the highest whole numeral that opened
        # an article, and the last item of the list being read, if any.
        self._ultimo_entero = 0
        self._item_de_lista: int | None = None
        self._numeros_de_considerandos: set[str] = set()
        #: Rule 20 (review fix-2 of #291): inside a block the SCJN labels
        #: `D.O.F. <fecha>`, a reform decree's own text, which nests under
        #: the decree whatever it reprints.
        self._decreto = False
        self._decreto_nodo: AknNode | None = None
        #: Rule 29 (review fix-4 of #291): the labelled container the current API
        #: block opened, the group of the Mexican promulgation decree's blocks, and
        #: the role of the current block's label.
        self._bloque_abierto: AknNode | None = None
        self._piso_del_bloque: float | None = None
        self._grupo: AknNode | None = None
        self._rol_actual: str | None = None
        self._indice_marca = -1
        #: Rule 23: the implicit section open now, as (node, index of the block
        #: that ends it), or `None`.
        self._implicita: tuple[AknNode, int] | None = None
        #: The node that holds each eId, for `AknNode.repeated`.
        self._por_eid: dict[str, AknNode] = {}
        #: The node created last, and the node the API block being read opened
        #: (rule 44, review fix-2 of #300 and #301).
        self._ultimo_nodo: AknNode | None = None
        self._nodo_de_bloque: AknNode | None = None
        #: Rule 21 (review fix-2 of #291): the rank of the container the
        #: current API block opened, or `None`. A heading the *text* of that
        #: block contains (an `APÉNDICE` inside a `PARTE`) belongs to it.
        self._piso: float | None = None
        self._i = 0

    def _toma_marca(self, inicio: int) -> int:
        """`inicio`, pulled back over the marker line still waiting for a node
        -- the way a node's span is pulled back over its annotation."""
        if self._marca_inicio is None:
            return inicio
        marca, self._marca_inicio = self._marca_inicio, None
        return min(inicio, marca)

    def _suelta_anotaciones_pendientes(self, fin: int) -> None:
        """Rule 44 (review fix-2 of #300 and #301): an annotation read after the last
        text of an API block and before the next marker -- `(DEROGADO SEGUNDO PÁRRAFO,
        ...)` printed after an article's last paragraph -- belongs to the block it was
        read in. Held until "the next node", it was pulled into the node the *next*
        block opens, whose span then began before its own marker: article 188 of the
        Código Civil Federal started inside article 187's block. It is attached to the
        last node of the block that ends here, which grows over it."""
        if not self._pendientes.notas or self._ultimo_nodo is None:
            return
        nodo = self._ultimo_nodo
        # The deepest last descendant: the notes of a node are read before its
        # children's, so one at the tail of an article held by the article itself
        # would be read before the annotations of the paragraphs it follows, and
        # `units._annotation_ranges`' left-to-right search would skip past them.
        while nodo.children:
            nodo = nodo.children[-1]
        _, notas = self._pendientes.toma(fin)
        hit("S13", "annotation-kept-by-its-block")
        nodo.notes.extend(notas)
        self._extiende(nodo, len(self._doc.text[:fin].rstrip()))

    def _bloque_marcador(self, bloque, campos):
        """A marker is metadata: it ends whatever the previous API block had
        open (an article never reaches into the next block the SCJN serves,
        which is what kept the text after a treaty's last article inside it),
        and its characters belong to the node the block it heads opens."""
        marca = self._sk.marcas[self._i]
        self._indice_marca = self._i
        self._nodo_de_bloque = None
        self._suelta_anotaciones_pendientes(bloque.start)
        if self._marca_inicio is None:
            self._marca_inicio = bloque.start
        self._decreto = False
        self._piso = None
        # Recitals, a preamble and a title block are not provisions: no
        # numbered paragraph of theirs opens an article (rule 8, rule 17).
        self._en_considerandos = marca.get("kind") in ("recitals", "preamble", "header")
        self._recien_abiertos = False
        self._numeros_de_considerandos = set()
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        self._cierra_divisiones()
        continuacion = self._i in self._sk.fusion
        if not continuacion:
            self._cierra_bloque_otro(marca)
        elif self._piso_del_bloque is not None:
            # The second half of a block the API split in two (rule 30): what its
            # text opens still nests under the container the first half opened.
            hit("S10", "continuation-keeps-root")
            self._piso = self._piso_del_bloque
        self._rol_actual = self._sk.rol_de.get(self._i) if marca.get("kind") == "other" else None
        if self._implicita is not None and self._i >= self._implicita[1]:
            # Rule 23: the implicit section ends where the first marker that is
            # not an article begins.
            nodo, _ = self._implicita
            hit("S6", "implicit-section-ends")
            self._implicita = None
            self._piso_del_bloque = None
            while self._pila and self._pila[-1][1] is not nodo and nodo in [n for _, n in self._pila]:
                self._pila.pop()
            if self._pila and self._pila[-1][1] is nodo:
                self._pila.pop()
        if marca.get("kind") == "article":
            # An article the SCJN serves as a block of its own is not a
            # transitory provision of the block before it.
            hit("S1", "article-block-closes-transitorios")
            self._transitorios = None
        if marca.get("kind") in ("header", "preamble", "recitals") and (
            self.body is not None or self._pila
        ):
            # An instrument's own opening text comes first; one of these
            # later on (the preamble of a resolution annexed to an acuerdo) is
            # text of its own, not the tail of the transitorios before it.
            hit("S8", "opening-block-closes-transitorios")
            self._transitorios = None
        if self._i in self._sk.aperturas:
            # Rule 28 (review fix-3 of #291): a text of its own -- the
            # `RESOLUCIÓN MSC.411(97)` a treaty prints after the code it amends --
            # is not part of the annex or appendix that came before it: when one
            # is open it hangs from the body, a sibling of the instrument's own
            # parts. (Without an attachment open the section stays where rule 23
            # put it: `DISPOSICIONES GENERALES` inside the chapter that holds it.)
            if any(n.akn_type == "attachment" for _, n in self._pila):
                hit("S7", "section-leaves-attachment")
                self._pila.clear()
                self._transitorios = None
            if self._rol_actual == "resolution":
                # An amending resolution is a text of its own, not the tail of the
                # transitorios before it (rule 29, whichever container it opens).
                hit("S8", "resolution-closes-transitorios")
                self._transitorios = None
            # Rule 44 (review fix-2 of #300 and #301): a block the API labels `other` is
            # an `hcontainer` of that label, never a `section` the text of the articles
            # that follow it would suggest -- `SECCIÓN` is what the SCJN says when it
            # says a section (`type=section`), and it never said it here.
            etiqueta = _limpia(marca.get("label", "")) or "block"
            if marca.get("kind") == "article":
                # Rule 38 is the one exception, and a documented one (`I5`'s
                # `documented-article-heading`): the API block is an article's heading
                # line, and the section is what the provisions after it belong to.
                hit("S6", "article-heading-section")
                nodo = self._nuevo_nodo("section", bloque.start, bloque.end, num=etiqueta)
            else:
                hit("S6", f"implicit-hcontainer-{self._rol_actual or marca.get('kind')}")
                padre = self._padre_contenedor(bloque.start)
                nodo = self._nuevo_nodo(
                    "hcontainer", bloque.start, bloque.end, num=etiqueta, padre=padre,
                    eid=self._eid_de_bloque(padre, etiqueta),
                )
            nodo.heading = None
            self._marca_inicio = None
            self._nodo_de_bloque = nodo
            self._pila.append((5.0, nodo))
            self._piso = self._piso_del_bloque = 5.0
            self._implicita = (nodo, self._sk.aperturas[self._i])
        elif not continuacion and self._i + 1 < self._sk.fin_cuerpo and (
            (marca.get("kind") == "other" and id(marca) not in self._sk.provisiones_sic)
            or marca.get("kind") in ("preamble", "recitals")
            or (marca.get("kind") == "header"
                and (self.preambulo is not None or self.body is not None or self._pila))
        ):
            # Rule 29. A title block, a preamble or recitals that come after the
            # instrument's own text began (a treaty's preamble after the
            # promulgation decree that precedes it) are a text of their own: a
            # labelled container, not loose text of the body.
            # Rule 42 (review fix-1 of #301): so is a preamble or recitals block that
            # follows the title block straight away (`CONSIDERANDO`, a treaty's own
            # `PREÁMBULO`): the document's `preamble` is exactly the title block, and it
            # used to swallow whatever API block came next until a container opened.
            hit("S8", f"labelled-hcontainer-{marca.get('kind')}")
            self._abre_bloque_otro(bloque, marca)

    # -- blocks the API labels `other` (rules 29-31, review fix-4 of #291) --

    #: Where a labelled block, a promulgation decree's group and a division sit
    #: in the stack of open containers: below every container a text can open
    #: (`PRECEDENCIA` ends at 6), so any heading of the text closes them.
    RANGO_BLOQUE = 7.0
    RANGO_BLOQUE_EN_GRUPO = 7.25

    def _cierra_a_traves_de(self, nodo) -> None:
        """Pop the stack through `nodo`, if it is on it."""
        if nodo is not None and any(n is nodo for _, n in self._pila):
            while self._pila and self._pila.pop()[1] is not nodo:
                pass

    def _cierra_divisiones(self) -> None:
        """Every division ends where the API block it was opened in does."""
        for k, (_, nodo) in enumerate(self._pila):
            if nodo in self._nodos_division:
                del self._pila[k:]
                return

    def _cierra_bloque_otro(self, marca) -> None:
        """The previous API block's labelled container ends at this marker, and
        so does the group of promulgation blocks, unless this block is one too."""
        self._cierra_a_traves_de(self._bloque_abierto)
        self._bloque_abierto = None
        self._piso_del_bloque = None
        rol = self._sk.rol_de.get(self._i) if marca.get("kind") == "other" else None
        if self._grupo is not None and rol != "promulgation":
            self._cierra_a_traves_de(self._grupo)
            self._grupo = None

    def _eid_de_bloque(self, padre: AknNode, etiqueta: str) -> str:
        """An eId for a labelled block or a division: the parent's path and
        `hc_<label>`, with a counter when the same label comes up again under the
        same parent -- never a repeat (rule 22 is about articles): two blocks both
        labelled `PARTE FINAL DE LAS ACTAS` are two blocks."""
        propuesto = self._eids.propuesto(
            "" if padre.akn_type in ("act", "body") else padre.eId, "hc", etiqueta[:60]
        )
        eid, i = propuesto, 2
        while eid in self._eids.used:
            eid = f"{propuesto}_{i}"
            i += 1
        return eid

    def _abre_bloque_otro(self, bloque, marca) -> None:
        """Rule 29: a block the API labels `other` is a container of its own, an
        `hcontainer` named by its label. One whose label has a role in the Mexican
        decree (`rol_de_etiqueta`) hangs from the body, whatever was open; the
        blocks of the promulgation decree are kept together under one container
        for the decree, and `PARTE FINAL ...` is its `#conclusions`, `NOTA
        EXPLICATIVA` its `#note`. Any other label opens a plain labelled container
        where the text stands, as rule 23's sections do."""
        etiqueta = _limpia(marca.get("label", "")) or "block"
        rol = self._rol_actual
        refers_to = {
            "promulgation": REFERS_TO_PROMULGACION,
            "conclusions": REFERS_TO_CONCLUSIONES,
            "note": REFERS_TO_NOTA,
        }.get(rol)
        rango, piso = self.RANGO_BLOQUE, self.RANGO_BLOQUE
        padre = None
        if rol == "resolution":
            # An amending resolution is a text of its own, not the tail of the
            # transitorios before it.
            hit("S8", "resolution-container")
            self._transitorios = None
        if rol in ROLES_SIN_SECCION:
            hit("S8", f"role-container-{rol}")
            # Whatever transitorios were open end: this is a text of its own.
            self._transitorios = None
            self._decreto = False
            # Nor is it part of the chapter or annex before it (unless it is the
            # next block of the promulgation decree already open).
            if not (rol == "promulgation" and self._grupo is not None):
                self._pila.clear()
                self._grupo = None
            if rol == "promulgation":
                if self._grupo is None:
                    hit("S8", "promulgation-group-opened")
                    cuerpo = self._asegura_body(bloque.start)
                    self._grupo = self._nuevo_nodo(
                        "hcontainer", bloque.start, bloque.start, num="DECRETO PROMULGATORIO",
                        refers_to=REFERS_TO_PROMULGACION, padre=cuerpo,
                        eid=self._eid_de_bloque(cuerpo, "DECRETO PROMULGATORIO"),
                    )
                    self._pila.append((self.RANGO_BLOQUE, self._grupo))
                padre = self._grupo
                rango, piso = self.RANGO_BLOQUE_EN_GRUPO, self.RANGO_BLOQUE_EN_GRUPO
                refers_to = None
        padre_real = padre if padre is not None else self._padre_contenedor(bloque.start)
        nodo = self._nuevo_nodo(
            "hcontainer", bloque.start, bloque.end, num=etiqueta, refers_to=refers_to,
            padre=padre_real, eid=self._eid_de_bloque(padre_real, etiqueta),
        )
        self._marca_inicio = None
        self._pila.append((rango, nodo))
        self._piso = self._piso_del_bloque = piso
        self._bloque_abierto = nodo
        self._nodo_de_bloque = nodo

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
            # Rule 20: a reform decree's own containers nest under it.
            if self._decreto and self._pila:
                hit("S3", "decree-contains")
                return self._pila[-1][1]
            # Rule 34 (review fix-6 of #291): so does everything the text of an
            # API block that opened a container of its own holds. The block's
            # container is the root of its text's hierarchy, whatever
            # transitorios section is open around it.
            if self._pila and self._raiz_de_bloque_en_pila():
                hit("S4", "block-root-inside-transitorios")
                return self._pila[-1][1]
            return self._transitorios
        if self._pila:
            return self._pila[-1][1]
        return self._asegura_body(inicio)

    def _raiz_de_bloque_en_pila(self) -> bool:
        """Whether the container the current API block opened (rules 23, 28,
        29 and 31) is still open: while it is, it bounds where the block's own
        text can hang."""
        raices = [self._bloque_abierto, *self._nodos_division]
        if self._implicita is not None:
            raices.append(self._implicita[0])
        return any(n is r for _, n in self._pila for r in raices if r is not None)

    def _cierra_hasta(self, rango: int):
        while self._pila and self._pila[-1][0] >= rango:
            self._pila.pop()

    def _nuevo_nodo(self, akn_type, inicio, fin, num=None, refers_to=None, padre=None,
                    eid=None):
        padre = padre if padre is not None else self._padre_contenedor(inicio)
        if eid is None:
            prefijo = PREFIJO_EID.get(akn_type, akn_type)
            if self._sk.marcas and akn_type == "attachment" and refers_to == REFERS_TO_APENDICE:
                # Rule 26 (review fix-3 of #291): an appendix is not an annex, and
                # its identifier says so. Only where the document has markers,
                # so one without them keeps the eIds it always had.
                hit("S7", "appendix-eid-prefix")
                prefijo = PREFIJO_EID_APENDICE
            propuesto = self._eids.propuesto(
                "" if padre.akn_type in ("act", "body") else padre.eId,
                prefijo,
                num if num is not None else akn_type,
            )
        else:
            propuesto = eid
        eid = self._eids.allocate(propuesto)
        inicio, notas = self._pendientes.toma(inicio)
        inicio = self._toma_marca(inicio)
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
            notes=notas, repeated=eid != propuesto,
        )
        # A repeat (review fix-2 of #291) involves both claimants of the
        # identifier: the one that had to take a suffix and the one that held it.
        if eid != propuesto:
            hit("S5", "repeated-identifier", bool(self._sk.marcas))
        if eid != propuesto and propuesto in self._por_eid:
            self._por_eid[propuesto].repeated = True
        self._por_eid.setdefault(eid, nodo)
        self._ultimo_nodo = nodo
        return padre.add(nodo)

    # -- the pass --------------------------------------------------------

    def run(self) -> AknNode:
        i = 0
        while i < len(self._bloques):
            bloque = self._bloques[i]
            if i >= self._sk.fin_cuerpo:
                self._agrega_conclusiones(bloque)
                i += 1
                continue
            if self._sk.marcas and self._es_linea_de_rotulo(i):
                # Rule 44: the block's own label line is the heading of the node
                # the block opened, whatever the text rules would have made of it
                # (the title line of an index, the caption of a table). The annotation
                # that stands before it (`(REFORMADO, D.O.F. ...)`) describes that
                # heading, and is attached to it.
                hit("S8", "label-line-is-the-heading")
                _, notas = self._pendientes.toma(self._bloques[i].start)
                self._nodo_de_bloque.notes.extend(notas)
                self._extiende(self._nodo_de_bloque, self._bloques[i].end)
                self._tabla = None
                if i in self._indices:
                    fin = self._indices.pop(i)
                    if fin > i + 1:
                        self._indices[i + 1] = fin
                i += 1
                continue
            if i in self._indices:
                fin = self._indices[i]
                hit("S14", "index-node", bool(self._sk.marcas))
                if i in self._sk.marcas:
                    # Rule 42 (review fix-1 of #301): an index the SCJN labels `ÍNDICE`
                    # is an API block like any other, and its marker ends the block
                    # before it (the `CONSIDERANDO` that precedes it is not its parent).
                    self._i = i
                    self._bloque_marcador(self._bloques[i], None)
                self._bloque_indice(self._bloques[i], self._bloques[fin - 1])
                i = fin
                continue
            kind, campos = self._sk.clases[i]
            self._i = i
            if self._sk.marcas:
                kind, campos = self._refina_con_marcas(bloque, kind, campos)
            if self._sk.modo is not None:
                kind, campos = self._promueve_a_articulo(bloque, kind, campos)

            # Rule 13 (issue #291): a table lasts exactly as long as its rows
            # do, plus one tolerated label block between two of them.
            if (self._tabla is not None and not es_fila_tabla(bloque)
                    and not (self._sk.marcas and kind == "contenido"
                             and self._tabla_con_leyenda is self._tabla
                             and self._es_nota_de_tabla(i))):
                if (
                    kind == "contenido"
                    and es_etiqueta_de_tabla(bloque)
                    and i + 1 < self._sk.fin_cuerpo
                    and es_fila_tabla(self._bloques[i + 1])
                ):
                    hit("B7", "label-between-rows")
                    self._extiende(self._tabla, bloque.end)
                    i += 1
                    continue
                self._tabla = None

            if self._pendiente_epigrafe is not None:
                contenedor = self._pendiente_epigrafe
                self._pendiente_epigrafe = None
                if _es_epigrafe(bloque, kind):
                    hit("B5", "epigraph", bool(self._sk.marcas))
                    contenedor.heading = _limpia(bloque.text)
                    self._extiende(contenedor, bloque.end)
                    i += 1
                    continue

            if self._sk.marcas and self._articulo is None and kind == "contenido":
                # Rule 36 (review fix-6 of #291): the caption of a table.
                if self._tabla is None:
                    fin = self._fin_de_leyenda(i)
                    if fin is not None:
                        hit("B7", "caption-opens-table")
                        self._abre_tabla_con_leyenda(i, fin)
                        i = fin
                        continue
                elif self._tabla_con_leyenda is self._tabla and self._es_nota_de_tabla(i):
                    hit("B7", "table-note")
                    self._extiende(self._tabla, bloque.end)
                    i += 1
                    continue

            if self._sk.marcas and i in self._sk.divisiones:
                # Rule 31. After the epigraph check: the title of the container
                # just opened is its own, whatever it is called.
                hit("B9", "division-opened")
                self._abre_division(bloque, *self._sk.divisiones[i])
                i += 1
                continue
            self._i = i
            manejador = getattr(self, f"_bloque_{kind}", self._bloque_contenido)
            manejador(bloque, campos)
            i += 1

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
        self.act.modo = self._sk.modo
        return self.act

    def _es_linea_de_rotulo(self, i: int) -> bool:
        """Rule 44 (review fix-2 of #300 and #301): whether block `i` is the first text
        of an API block that opened a labelled `hcontainer` and consists only of that
        label (`**DISPOSICIONES PRELIMINARES**`, `**III. CONSIDERANDOS**`,
        `Finalidad.`), compared without case, accents, bold, `:` or `.`, and with a
        leading numeral allowed. The SCJN serves the heading it names a block with as
        the block's first line; it is the heading of the node, and read as content it
        became an `intro` unit that is nothing but a title and is compared as text."""
        marca = self._sk.primeros.get(i)
        nodo = self._nodo_de_bloque
        if (marca is None or nodo is None or nodo.akn_type != "hcontainer"
                or marca.get("kind") not in BLOQUES_CON_ROTULO
                or nodo.children or self._articulo is not None):
            return False
        return es_linea_de_etiqueta(self._bloques[i].text, marca.get("label", ""))

    def _refina_con_marcas(self, bloque, kind, campos):
        """Rule 32 (review fix-4 of #291): numbering the ordinary grammar does not
        read, on a snapshot that carries markers. Inside transitorios, `Artículo
        Primero.-` with the ordinal in mixed case is a transitory article; outside
        them, `ARTÍCULO S5` of a radio-regulations text is the container of its
        provisions and `S5.43` is a provision (an article). Never inside the
        blocks rule 24 reads as one text, the recitals or a Mexican decree's own
        blocks."""
        primera = self._sk.primeros.get(self._i)
        if kind == "articulo" and primera is None and not self._en_considerandos:
            # Rule 37 (review fix-6 of #291): `**Artículo 8o. **fracción II primer
            # párrafo: $179,017.26`, a line of a tariff that names the article it
            # is a quantity of, is a reference and not an opener: no provision
            # begins with a lower-case word after its number, and the API did not
            # say this block is an article (an API article stays one).
            linea = bloque.text.split("\n", 1)[0].replace("**", "")
            m = ARTICULO.match(linea)
            if m and linea[m.end():].lstrip()[:1].islower():
                hit("B8", "article-reference-line")
                return "contenido", {}
        if (
            kind in ("contenido", "articulo_ordinal") and primera is not None
            and primera.get("kind") == "provision" and primera.get("num")
            and not self._en_considerandos
        ):
            # Rule 29: the API says this block is a numbered provision of an
            # acuerdo (`PRIMERO`, `1.1.0`): it is an article, whatever case its
            # ordinal is written in and whether or not the document as a whole is
            # read as numbering that way (rule 8).
            # The number the text writes wins over the label's (an upper-case
            # `PRIMERO` and the instrument's own `Primero` are two provisions).
            numero = campos["num"] if kind == "articulo_ordinal" else _limpia(primera["num"])
            hit("S9", f"provision-block-is-article-{kind}")
            if self._sk.modo == MODO_NUMERAL and numero.isdigit():
                # The numbered list inside this provision (`1.`, `2.`) starts
                # over after it, exactly as after a provision numeral mode read
                # itself (rule 18).
                self._ultimo_entero = int(numero)
                self._item_de_lista = None
            return "articulo", {"num": numero}
        if kind != "contenido" or self._en_considerandos:
            return kind, campos
        marca = self._sk.de_marca[self._i]
        if marca is not None and marca.get("kind") in BLOQUES_DE_UN_SOLO_TEXTO:
            return kind, campos
        linea = bloque.text.split("\n", 1)[0]
        if self._transitorios is not None:
            m = ARTICULO_ORDINAL_MIXTO.match(linea.replace("**", ""))
            if m:
                hit("B2", "mixed-case-ordinal-in-transitorios")
                return "articulo", {"num": _limpia(m.group("num"))}
            return kind, campos
        if self._rol_actual in ROLES_SIN_SECCION:
            return kind, campos
        m = ENCABEZADO_APENDICE_RR.match(linea)
        if m:
            hit("B2", "radio-regulations-appendix")
            return "contenedor", {
                "akn_type": "attachment", "num": m.group("num"),
                "refers_to": REFERS_TO_APENDICE, "heading": None,
            }
        m = ENCABEZADO_ARTICULO_RR.match(linea)
        if m:
            hit("B2", "radio-regulations-article-section")
            return "contenedor", {"akn_type": "section", "num": m.group("num")}
        m = PROVISION_RR.match(linea)
        if m:
            hit("B2", "radio-regulations-provision")
            return "articulo", {"num": m.group("num")}
        return kind, campos

    def _en_formulario(self) -> bool:
        return any(nodo in self._formularios for _, nodo in self._pila)

    def _abre_division(self, bloque, etiqueta, rol) -> None:
        """Rule 31: the heading at `bloque` opens a division, an `hcontainer`
        named by it, that lasts until the next division, the next container the
        text opens or the end of the API block. What it holds is segmented as
        anywhere else; its articles are its own."""
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        self._cierra_divisiones()
        padre = self._padre_contenedor(bloque.start)
        nodo = self._nuevo_nodo(
            "hcontainer", bloque.start, bloque.end, num=etiqueta,
            refers_to=REFERS_TO_FORMULARIO if rol == "form" else REFERS_TO_DIVISION,
            padre=padre, eid=self._eid_de_bloque(padre, etiqueta or "division"),
        )
        self._extiende(nodo, bloque.end)
        self._pila.append((self.RANGO_BLOQUE, nodo))
        self._nodos_division.add(nodo)
        if rol == "form":
            self._formularios.add(nodo)

    def pide_divisiones(self) -> dict[int, tuple[str | None, str | None]]:
        """The divisions that would keep apart the articles this pass could only
        tell apart with a `_2` suffix (rule 31): for each article that took one
        (outside transitorios), the series of article numbers it belongs to -- the
        run that strictly increases up to it, in its container -- begins where the
        numbering started over, and the heading just before that point opens a
        division. The nearest heading in the gap since the previous article is the
        one (an upper-case line, a document title, else a short plain paragraph);
        with none there is no division and the repeat stays what the text says: the
        same number twice with nothing between to say it is another text."""
        nuevas: dict[int, tuple[str | None, str | None]] = {}
        for nodo in self.act.walk():
            if nodo.akn_type != "article" or nodo.eId not in self._eids.suffixed:
                continue
            if any(a.refers_to == REFERS_TO_TRANSITORIOS for a in self._ancestros(nodo)):
                continue
            hermanos = [h for h in nodo.parent.children if h.akn_type == "article"]
            posicion = hermanos.index(nodo)
            inicio = inicio_de_la_serie([h.num for h in hermanos], posicion)
            if inicio == 0:
                inicio = posicion
            primero = self._bloque_de_articulo.get(hermanos[inicio].eId)
            if primero is None:
                continue
            anterior = self._bloque_de_articulo.get(hermanos[inicio - 1].eId) if inicio else None
            encontrado = self._encabezado_de_serie(primero, anterior)
            if encontrado is not None and encontrado[0] not in self._sk.divisiones:
                nuevas[encontrado[0]] = (encontrado[1], None)
        return nuevas

    def _ancestros(self, nodo):
        while nodo.parent is not None:
            nodo = nodo.parent
            yield nodo

    def _encabezado_de_serie(self, primero: int, anterior: int | None) -> tuple[int, str] | None:
        """The block a division in front of the series that starts at block
        `primero` opens at, and its label: the nearest strong heading between the
        previous article's block and `primero`, else the nearest weak one, else
        `primero` itself and no label. The scan stops at a marker (another API
        block) and never reads an annotation, a table row or a block the text
        classes as structure."""
        fuerte = debil = None
        for k in range(primero - 1, (anterior if anterior is not None else -1), -1):
            if k in self._sk.marcas or self._sk.clases[k][0] != "contenido" or es_fila_tabla(self._bloques[k]):
                if k in self._sk.marcas:
                    break
                continue
            texto = self._bloques[k].text
            if fuerte is None:
                titulo = encabezado_fuerte(texto)
                if titulo is not None:
                    fuerte = (k, titulo)
                    break
            if debil is None:
                titulo = encabezado_debil(texto)
                if titulo is not None:
                    debil = (k, titulo)
        elegido = fuerte or debil
        if elegido is None:
            return None
        return elegido[0], elegido[1][:80]

    def _bloque_indice(self, primero, ultimo):
        """Rule 15 (review fix-1 of #291): a table of contents is one `toc`
        node, a leaf spanning its marker and every entry, hung where any other
        block of text would be. Nothing in it is classified: its entries are
        the headings the document repeats as its body, and reading them twice
        is how a `PARTE` of the IMDG Code came to have 241 articles that were
        index lines."""
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        destino = self._padre_contenedor(primero.start)
        numero = 1 + sum(1 for h in destino.children if h.akn_type == "toc")
        nodo = self._nuevo_nodo(
            "toc", primero.start, ultimo.end, padre=destino, num=str(numero),
        )
        self._extiende(nodo, ultimo.end)

    def _rango(self, akn_type: str, forzado: bool = False, refers_to: str | None = None,
               num: str | None = None):
        rango = (
            PRECEDENCIA_PARTE_EN_CAPITULO
            if akn_type == "part" and self._sk.parte_en_capitulo
            else PRECEDENCIA[akn_type]
        )
        if akn_type == "part" and forzado and self._sk.marcas:
            propio = self._rango_de_parte()
            if propio is not None:
                hit("S12", "part-ranked-from-next-marker")
                rango = propio
        if (
            self._sk.marcas and akn_type == "attachment" and not forzado
            and refers_to == REFERS_TO_ANEXO and self._es_codigo_de_resolucion()
        ):
            # Rule 29: an `ANEXO` a labelled block prints and whose structure the
            # API serves as blocks of their own *after* it (the code a resolution
            # amends, the reglamento an `ANEXO ÚNICO` approves) is the instrument's,
            # not the block's: it ends the block's container and takes the blocks
            # that follow. An annex whose structure is in the block's own text is
            # the block's (rule 33).
            hit("S4", "annex-leaves-its-block")
            self._piso = self._piso_del_bloque = None
            self._bloque_abierto = None
            self._implicita = None
            return rango
        if forzado:
            self._piso = rango
        elif self._piso is not None and rango <= self._piso:
            # Rules 21 and 33: a heading the text of this API block contains nests
            # under the container the API block itself opened, and keeps its place
            # among the other headings of the same text -- an annex holds the
            # chapters printed after it, a chapter its parts (review fix-5 of #291;
            # before it every such heading took the same rank and they were
            # siblings).
            hit("S4", "heading-nests-in-block")
            return self._piso + self.RANGO_EN_BLOQUE + self._orden_en_bloque(akn_type, rango, refers_to, num)
        return rango

    def _rango_de_parte(self) -> float | None:
        """Rule 45 (review fix-2 of #300 and #301): where a part the SCJN labels
        (`type=part`) sits, read from the order of the markers rather than from a
        guess about the whole document. When the very next marker opens a book, a title
        or a chapter, the part holds it -- the Código Civil Federal's `PRIMERA PARTE`
        and its titles inside the fourth book, a treaty's `SEGUNDA PARTE` and its
        chapters VIII to XIII -- and ranks just above that type: below the open book,
        above the titles; below the open title, above the chapters; closing a chapter
        that was open. A part whose next marker is anything else (SOLAS: `Parte A` and
        its regulations) keeps the document-wide reading, `None`."""
        n = bisect.bisect_left(self._sk.indices_de_marca, self._indice_marca)
        if n + 1 >= len(self._sk.indices_de_marca):
            return None
        siguiente = self._sk.marcas[self._sk.indices_de_marca[n + 1]]
        tipo = siguiente.get("type")
        if siguiente.get("kind") != "container" or tipo not in ("book", "title", "chapter"):
            return None
        rango = PRECEDENCIA[tipo] - 0.5
        # A part is a sibling of the part before it, never inside it.
        abiertas = [r for r, nodo in self._pila if nodo.akn_type == "part"]
        return min(rango, abiertas[0]) if abiertas else rango

    def _es_codigo_de_resolucion(self) -> bool:
        """Whether the annex heading being read is the instrument's own and not
        the labelled block's: the block's container is open, and nothing after the
        heading in the block's text opens a container or an article -- the annex
        is a title and the text it amends or introduces, and its structure (the
        chapters of a code, the instrument an `ANEXO ÚNICO` approves) is what the
        API serves in the blocks that follow."""
        abierto = self._bloque_abierto
        if abierto is None and self._implicita is not None:
            abierto = self._implicita[0]
        if abierto is None or self._piso != self._piso_del_bloque:
            return False
        if not any(n is abierto for _, n in self._pila):
            return False
        fin = self._sk.siguiente_de_marca.get(self._indice_marca)
        if fin is None or fin >= len(self._bloques) or self._sk.rol_de.get(fin) in ("resolution", *ROLES_SIN_SECCION):
            # Nothing follows, or what follows is a text of its own (the next
            # resolution, the closing formulas): there is no structure to take.
            return False
        return not any(
            self._sk.clases[j][0] in ("contenedor", "articulo") for j in range(self._i + 1, fin)
        )

    #: Rule 33 (review fix-5 of #291): the first rank below the container an API
    #: block opened, and how far the precedence of a heading the block's text
    #: prints moves it down from there (`precedence / 100` stays below every
    #: step of 0.25 and 0.5 the other rules use).
    RANGO_EN_BLOQUE = 0.5

    def _orden_en_bloque(self, akn_type: str, rango: float, refers_to: str | None,
                         num: str | None) -> float:
        """How far below `RANGO_EN_BLOQUE` a heading printed inside an API block
        sits: its own precedence, scaled so the order among headings is kept. A bare
        `APÉNDICE` printed after an annex of the same block is below the annex and
        above every chapter; one that carries a number (`APÉNDICE III-A`, which
        names the annex it belongs to) keeps the rank of any other attachment."""
        if akn_type == "attachment" and refers_to == REFERS_TO_APENDICE and num is None and any(
            n.akn_type == "attachment" and n.refers_to == REFERS_TO_ANEXO
            for _, n in self._pila
        ):
            hit("S4", "bare-appendix-after-annex")
            return 0.005
        if (
            akn_type == "attachment" and refers_to == REFERS_TO_ANEXO and num is None
            and self._pila and self._pila[-1][1].akn_type == "attachment"
            and self._pila[-1][1].refers_to == REFERS_TO_ANEXO
            and not self._pila[-1][1].children
        ):
            # A bare `ANEXO` that follows another with nothing between them but its
            # title (`ANEXO` / `ENMIENDAS AL PROTOCOLO` / `ANEXO` / `MODIFICACIONES
            # Y ADICIONES AL ANEXO DEL CONVENIO`) is that annex's own text, the
            # annex of the annex: the first is not left an empty node.
            hit("S4", "bare-annex-after-title-only-annex")
            return 0.001
        return rango / 100

    def _dueno_numerado(self, campos) -> tuple[float, AknNode] | None:
        """Rule 25 (review fix-3 of #291): the open container an annex or an
        appendix the SCJN numbers belongs to, as ``(rank, node)``.

        A treaty prints `ANEXO 2-A` after the chapter it annexes, and the API
        says so: the block's `numero` is the chapter's number (2), and so is the
        `N-` prefix of its `num`. The innermost open container (a chapter, a
        title, a part...; never another annex or appendix) whose number is that
        one is the owner. `numero` 0 -- the API's "no owner" -- falls back to the
        prefix, and a prefix that names no open container, or none at all
        (`ANEXO I`, `APÉNDICE 1`), means no owner: the annex stays where its
        own rank puts it."""
        numero = (campos.get("numero") or "").strip()
        tipos = None
        if not numero.isdigit() or int(numero) == 0:
            # The prefix is a weaker signal than the API's own number (the
            # letter of a section is not the number of a chapter), so it is
            # only read against the containers that carry numbers.
            prefijo = re.match(r"\s*(\w+?)[-.]", campos.get("num") or "")
            numero = prefijo.group(1) if prefijo else ""
            tipos = ("book", "title", "chapter", "part")
        if not numero:
            return None
        buscado = _forma_comparable(numero)
        for rango, nodo in reversed(self._pila):
            if nodo.akn_type == "attachment" or nodo.num is None:
                continue
            if tipos is not None and nodo.akn_type not in tipos:
                continue
            if _forma_comparable(nodo.num) == buscado:
                return rango, nodo
        return None

    #: Rule 25: how far below its owner an annex sits -- under the chapter, above
    #: everything the chapter's own sections would hold.
    RANGO_BAJO_DUENO = 0.25
    #: Rule 26: where an appendix with no owner sits inside the annex that holds
    #: it -- under the annex, above every chapter or part of the annex's text.
    RANGO_APENDICE_EN_ANEXO = 0.5

    def _rango_de_anexo(self, campos) -> float | None:
        """Rules 25 and 26: the rank an annex or appendix the markers name takes
        when it belongs to something open, or `None` when it keeps its own."""
        if campos.get("tipo") not in ("annex", "appendix"):
            return None
        dueno = self._dueno_numerado(campos)
        if dueno is not None:
            hit("S7", "numbered-annex-under-owner")
            return dueno[0] + self.RANGO_BAJO_DUENO
        if campos["tipo"] == "appendix" and any(
            n.akn_type == "attachment" and n.refers_to == REFERS_TO_ANEXO
            for _, n in self._pila
        ):
            hit("S7", "appendix-in-annex")
            return self.RANGO_APENDICE_EN_ANEXO
        return None

    def _es_cabecera_repetida(self, campos) -> bool:
        """Rule 27 (review fix-3 of #291): a bare `**APÉNDICE**` heading, with no
        number, directly under the unnumbered appendix it repeats.

        The T-MEC prints the tariff appendix of its annex 2-C over seven pages,
        and the SCJN's text carries each page's running header (`**APÉNDICE**`,
        `**AUTOPARTES**`, `Continúa`) in the middle of the table. Read as
        headings they were seven appendices (`att_attachment` ... `_7`) holding
        one table each; they are the one appendix's own text. Only where the
        document has markers, so a document without them parses as before."""
        if not self._sk.marcas or campos.get("forzado") or campos.get("num") is not None:
            return False
        if campos["akn_type"] != "attachment" or campos.get("refers_to") != REFERS_TO_APENDICE:
            return False
        if not self._pila:
            return False
        arriba = self._pila[-1][1]
        return (
            arriba.akn_type == "attachment"
            and arriba.refers_to == REFERS_TO_APENDICE
            and arriba.num is None
        )

    def _bloque_contenedor(self, bloque, campos):
        akn_type = campos["akn_type"]
        if self._es_cabecera_repetida(campos):
            hit("S7", "running-page-header")
            self._bloque_contenido(bloque, campos)
            return
        # A container ends whatever transitorios were open: transitional
        # provisions are the tail of the law, and a container after them
        # belongs to a further decree's own text.
        if not self._decreto:
            self._transitorios = None
        self._cierra_articulo()
        rango_propio = self._rango_de_anexo(campos) if campos.get("forzado") else None
        if rango_propio is not None:
            hit("S7", "annex-owner-or-appendix-in-annex")
            self._piso = rango = rango_propio
        else:
            rango = self._rango(akn_type, campos.get("forzado", False), campos.get("refers_to"),
                               campos.get("num"))
            if self._sk.marcas and not campos.get("forzado") and campos.get("refers_to") == REFERS_TO_ANEXO:
                # Rule 31 (review fix-4 of #291): an `ANEXO 1` the text of one block
                # prints under the appendix before it (`APÉNDICE S30` and its annexes
                # 1 to 7) is that appendix's, not a sibling whose number repeats
                # the annexes of every other appendix.
                abierto = next((e for e in reversed(self._pila) if e[1].akn_type == "attachment"), None)
                if abierto is not None and abierto[1].refers_to == REFERS_TO_APENDICE:
                    hit("B9", "annex-under-appendix")
                    rango = max(rango, abierto[0] + 0.25)
        hit("B5" if not campos.get("forzado") else "S1", f"container-{akn_type}", bool(self._sk.marcas))
        self._cierra_hasta(rango)
        refers_to = REFERS_TO_APARTADO if akn_type == "level" else campos.get("refers_to")
        nodo = self._nuevo_nodo(
            akn_type, bloque.start, bloque.end, num=campos["num"], refers_to=refers_to,
        )
        if campos.get("heading"):
            nodo.heading = campos["heading"]
        self._pila.append((rango, nodo))
        # A heading the label's own line already carried (`PARTE 1- ENSAYO`)
        # is not looked for again in the block after it (rule 19).
        self._pendiente_epigrafe = None if campos.get("forzado") and nodo.heading else nodo

    def _bloque_articulo(self, bloque, campos):
        if (
            self._i in self._sk.encabezados_de_leyenda and self._articulo is not None
            and self._bloque_de_articulo.get(self._articulo.eId)
            == self._sk.encabezados_de_leyenda[self._i]
        ):
            # Rule 29: the heading line of an article already open at its caption.
            hit("B2", "article-heading-after-caption")
            self._extiende(self._articulo, bloque.end)
            self._interior.agrega(bloque, es_marcador_estructural=True)
            for m in ANOTACION_EN_LINEA.finditer(bloque.text.split("\n", 1)[0]):
                if es_anotacion(m.group("cuerpo")):
                    self._articulo.notes.append(parse_annotation(m.group(0), m.group("cuerpo")))
            return
        self._cierra_articulo()
        num = campos["num"]
        if campos.get("alternativa") is not None:
            # The label and the text disagree and neither explains the other
            # (`ARTÍCULO 6` on a block whose text prints `ARTICULO 7.-`): the
            # one whose identifier is not already taken is the article's.
            padre = self._padre_contenedor(bloque.start)
            tomado = self._eids.propuesto(
                "" if padre.akn_type in ("act", "body") else padre.eId, "art", num
            ) in self._eids.used
            if es_etiqueta_repetida(num, campos["alternativa"], tomado):
                hit("S1", "number-label-repeated")
                num = campos["alternativa"]
        self._articulo = self._nuevo_nodo(
            "article", bloque.start, bloque.end, num=num,
        )
        self._bloque_de_articulo[self._articulo.eId] = self._i
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

    def _promueve_a_articulo(self, bloque, kind, campos):
        """Rule 8 (issue #227) applied to one block: in a document that
        numbers nothing with `Artículo N` (`modo_sin_articulos`), the shape
        that document *does* number with opens an article in the body too.

        Called before the epigraph check rather than inside the block
        handlers, so that a promoted provision can never be swallowed as the
        epigraph of the container above it.

        Except inside the recitals (rule 17, review fix-1 of #291): the
        paragraphs numbered `PRIMERO.` .. `NOVENO.` (or `1.` ..) under a
        `CONSIDERANDO` heading are the instrument's preamble, and numbering
        them as articles gave every such lineamiento two articles `PRIMERO`.
        The recitals end at a container, at the heading that opens what the
        instrument orders (`MARCADOR_DISPOSITIVO`), at transitorios, or when
        their own numbering starts over -- a number already used inside them
        is the first provision of the next series.
        """
        linea = bloque.text.split("\n", 1)[0]
        if self._sk.marcas:
            # Rule 19: the recitals are the API block labelled `CONSIDERANDO`,
            # and end where it does -- no text heuristic needed.
            if self._en_considerandos:
                return kind, campos
            # Rule 31: the numbered boxes of a model certificate are its form
            # fields, and the numbered paragraphs of the Mexican decree's own
            # blocks are not provisions of the instrument either.
            if self._en_formulario() or self._rol_actual in ROLES_SIN_SECCION:
                return kind, campos
        elif kind == "contenido" and self._transitorios is None and CONSIDERANDO.match(linea):
            hit("B4", "recitals-by-text", False)
            self._en_considerandos = True
            self._recien_abiertos = True
            self._numeros_de_considerandos = set()
            return kind, campos
        numero = None
        if self._sk.modo == MODO_ORDINAL:
            if kind == "articulo_ordinal":
                numero = campos["num"]
        elif kind == "contenido" and self._transitorios is None:
            m = NUMERAL_DECIMAL.match(linea)
            if m and not (self._sk.marcas and _es_valor_en_minuscula(linea[m.end():])):
                # A numeral followed by a lower-case word is a list item or a
                # value (`1.1. cuando las propiedades ...`), never the opener of
                # a provision (rule 31, review fix-4 of #291); `3. a) Por ...`
                # is still a provision whose first paragraph is lettered.
                numero = _limpia(m.group("num"))
            elif m:
                hit("B9", "numeral-before-lower-case-word")

        if self._en_considerandos and self._recien_abiertos:
            # Numbered recitals are the ones that start with a number right
            # after the heading; `Que ...` recitals leave the numbered
            # paragraphs below them to be the provisions.
            self._recien_abiertos = False
            if numero is None:
                self._en_considerandos = False
        if self._en_considerandos:
            if kind in ("contenedor", "transitorios", "transitorios_dof") or (
                kind == "contenido" and MARCADOR_DISPOSITIVO.match(linea)
            ):
                hit("B4", "recitals-end-at-structure", bool(self._sk.marcas))
                self._en_considerandos = False
            elif numero is not None:
                if numero not in self._numeros_de_considerandos:
                    hit("B4", "numbered-recital", bool(self._sk.marcas))
                    self._numeros_de_considerandos.add(numero)
                    return kind, campos
                hit("B4", "recitals-end-at-restart", bool(self._sk.marcas))
                self._en_considerandos = False
        if numero is None:
            return kind, campos
        if (
            self._sk.marcas and self._sk.modo == MODO_NUMERAL
            and self._articulo is not None and self._articulo.num == numero
        ):
            hit("B9", "numeral-repeats-provision-above")
            # Rule 31 (review fix-4 of #291): a numeral that repeats the number of
            # the provision directly above it is a paragraph of that provision,
            # not a second one with its number: `1.1 De conformidad con ...`
            # followed by `1.1 Las divisiones ...` (the SCJN prints `1.1` for what
            # reads `1.1.1`). The text stays in the provision it continues.
            return kind, campos
        if REINICIO_ES_ITEM and self._sk.modo == MODO_NUMERAL and numero.isdigit():
            # Rule 18 (review fix-1 of #291): a whole numeral that starts over
            # (`1.` after `3.`) or carries on a list that did is a list item
            # of the provision above, not a further article. Dotted numerals
            # (`2.1`) are provisions in their own right and never come here.
            valor = int(numero)
            if valor <= self._ultimo_entero or (
                self._item_de_lista is not None and valor == self._item_de_lista + 1
            ):
                hit("B4", "numeral-restart-is-list-item", bool(self._sk.marcas))
                self._item_de_lista = valor
                return kind, campos
            self._ultimo_entero = valor
            self._item_de_lista = None
        hit("B3", f"promote-{self._sk.modo}", bool(self._sk.marcas))
        return "articulo", ({"num": numero} if self._sk.modo != MODO_ORDINAL else campos)

    def _bloque_articulo_ordinal(self, bloque, campos):
        # `**Primero.**` numbers a transitorio provision; outside
        # transitorios the same shape is an ordinary bolded paragraph opener
        # and must stay content -- unless the whole document numbers its
        # provisions that way, which `_promueve_a_articulo` decides before
        # this handler is ever reached (rule 8, issue #227).
        if self._transitorios is None:
            self._bloque_contenido(bloque, campos)
            return
        self._bloque_articulo(bloque, campos)

    def _bloque_transitorios(self, bloque, campos):
        self._abre_transitorios(bloque, campos.get("num"))

    def _bloque_transitorios_dof(self, bloque, campos):
        # A `**D.O.F. <fecha>.**` header only opens a new block of transitional
        # provisions once inside transitorios; before them, in the body, the
        # same shape is an ordinary paragraph -- unless what follows it is a
        # decree's own provisions (rule 17, review fix-1 of #291).
        if campos.get("forzado"):
            # Rule 20: the SCJN labels this block a decree, whatever came before.
            hit("S3", "decree-section")
            self._abre_transitorios(bloque, campos.get("num"), decreto=True)
            self._decreto = True
            return
        if self._transitorios is None and not self._abre_decreto(self._i):
            hit("B4", "dof-header-in-body-is-text", bool(self._sk.marcas))
            self._bloque_contenido(bloque, campos)
            return
        if self._transitorios is None:
            hit("B4", "dof-header-opens-decree", bool(self._sk.marcas))
        self._abre_transitorios(bloque, campos.get("num"))

    #: How many blocks after a `D.O.F.` header an ordinal provision is looked
    #: for (rule 17): room for the explanatory line the SCJN sometimes puts
    #: between the header and the decree's text (`SE TRANSCRIBE UNICAMENTE EL
    #: TRANSITORIO DEL DECRETO...`) and for a decree that has none.
    VENTANA_DECRETO = 4

    def _abre_decreto(self, indice: int) -> bool:
        """Whether the `D.O.F.` header at `indice` heads a decree's
        transitional provisions even though no `TRANSITORIOS` heading came
        before it (rule 17, review fix-1 of #291): an ordinal provision --
        `**PRIMERO.-**`, `**ARTICULO UNICO.-**` -- follows within
        `VENTANA_DECRETO` blocks, with nothing but explanatory lines, annotations
        and further `D.O.F.` headers between. The Ley sobre el Contrato de
        Seguro ends with five such decrees and no `TRANSITORIOS` heading at all,
        and every one of its `ARTICULO PRIMERO` was an article of the law."""
        for k in range(indice + 1, min(indice + 1 + self.VENTANA_DECRETO, self._sk.fin_cuerpo)):
            kind, campos = self._sk.clases[k]
            if kind == "articulo_ordinal":
                return True
            if kind == "articulo":
                return bool(_ORDINAL_PALABRA.fullmatch(campos["num"]))
            if kind not in ("contenido", "anotacion", "nota_editorial", "transitorios_dof"):
                return False
        return False

    def _abre_transitorios(self, bloque, num, decreto=False):
        # Several sibling sections, one per decree that added transitional
        # provisions -- never merged, since which decree a provision belongs
        # to is exactly what the separation records.
        #
        # Inside a decree's own text (rule 20) a `Transitorios` heading is that
        # decree's own transitory provisions, nested under it: it must not
        # end the decree and start a sibling at the body.
        padre = self._decreto_nodo if self._decreto and not decreto else None
        self._pila.clear()
        self._cierra_articulo()
        self._transitorios = None
        self._transitorios = self._nuevo_nodo(
            "section", bloque.start, bloque.end,
            num=num or "transitorios",
            refers_to=REFERS_TO_TRANSITORIOS,
            padre=padre if padre is not None else self._asegura_body(bloque.start),
        )
        if decreto:
            self._decreto_nodo = self._transitorios

    def _bloque_contenido(self, bloque, campos, es_marcador_estructural=False):
        if self._articulo is not None:
            self._extiende(self._articulo, bloque.end)
            self._interior.agrega(bloque, es_marcador_estructural)
            return
        if self._pila or self._transitorios is not None or self.body is not None:
            if self._tabla is not None and es_fila_tabla(bloque):
                # The next row of the table open now (it ends at the first block that
                # is not a row, or at a marker): wherever it hangs, so do its rows.
                self._extiende(self._tabla, bloque.end)
                return
            destino = self._destino_de_contenido(bloque)
            if es_fila_tabla(bloque):
                self._agrega_fila_de_tabla(destino, bloque)
            elif destino.akn_type == "intro":
                self._nuevo_nodo(
                    "content", bloque.start, bloque.end, padre=destino,
                    num=str(1 + sum(1 for h in destino.children if h.akn_type == "content")),
                )
                self._extiende(destino, bloque.end)
            else:
                self._nuevo_nodo("content", bloque.start, bloque.end, padre=destino)
            return
        # Nothing structural has opened yet, so this is the preamble.
        if self.preambulo is None:
            hit("S11", "preamble-node", bool(self._sk.marcas))
            self.preambulo = self.act.add(
                AknNode("preamble", self._span(self._toma_marca(bloque.start), bloque.end, "preamble"),
                        eId=self._eids.allocate("preamble"))
            )
        else:
            self._extiende(self.preambulo, bloque.end)

    def _destino_de_contenido(self, bloque) -> AknNode:
        """The node a block of plain text (or a table row) hangs from when no
        article is open."""
        destino = self._padre_contenedor(bloque.start)
        if self._transitorios is None and self._pila:
            hit("B6", "container-introduction", bool(self._sk.marcas))
            return self._intro_de(destino, bloque)
        if self._sk.marcas and destino.akn_type != "act":
            hit("B6", "introduction-of-transitorios-or-body")
            # Rule 29: the text a decree's transitorios section (or a division in
            # one) opens with -- `SE TRANSCRIBEN ÚNICAMENTE LOS TRANSITORIOS DEL
            # DECRETO...` -- is its introduction, like any container's, not text
            # hanging loose from it. Rule 34 (review fix-6 of #291): so is the
            # text of the body itself that follows a table of contents (the rest
            # of a header block, after its index).
            return self._intro_de(destino, bloque)
        return destino

    def _intro_de(self, contenedor: AknNode, bloque) -> AknNode:
        """Where a block that is neither an article nor a container goes when
        it falls straight under `contenedor` (rule 12, issue #291): the
        container's `intro`, opened by the first such block.

        Everything between a container's heading (epigraph included) and its
        first child container or article is its introduction -- the *Notas.*
        of a LIGIE section or chapter, an acuerdo's opening paragraphs -- and
        so is a table that sits there. The builder only ever reaches this
        with a container that has no child yet or whose last child is its
        `intro`: any block after an article belongs to that article until the
        next container opens, which is why this text used to come out as a
        container's own loose `content` leaves. The guard on `children` keeps
        an `intro` from opening after an article all the same.
        """
        if contenedor.children and contenedor.children[-1].akn_type == "intro":
            return contenedor.children[-1]
        if (self._sk.marcas and contenedor.children
                and all(h.akn_type in ("intro", "toc", "table") for h in contenedor.children)):
            # Rule 34 (review fix-6 of #291): what a container's own text holds
            # after its table of contents, or after a captioned table (rule 36),
            # is its introduction too -- neither is a child container or an
            # article, which is what ends one.
            n = 1 + sum(1 for h in contenedor.children if h.akn_type == "intro")
            hit("B6", "second-introduction-after-index-or-table")
            return self._nuevo_nodo(
                "intro", bloque.start, bloque.end, padre=contenedor,
                eid=f"{contenedor.eId}__intro_{n}",
            )
        if contenedor.children:
            return contenedor
        return self._nuevo_nodo(
            "intro", bloque.start, bloque.end, padre=contenedor,
            eid=f"{contenedor.eId}__intro",
        )

    #: Rule 36: the longest caption and note, in characters, a captioned table
    #: takes with it.
    MAX_LEYENDA = 400
    MAX_NOTA_DE_TABLA = 1000

    def _es_leyenda(self, k: int) -> bool:
        bloque = self._bloques[k]
        texto = bloque.text.strip()
        return (self._sk.clases[k][0] == "contenido" and k not in self._sk.divisiones
                and len(texto) <= self.MAX_LEYENDA and LEYENDA_TABLA.match(texto) is not None)

    def _fin_de_leyenda(self, i: int) -> int | None:
        """Rule 36 (review fix-6 of #291): if blocks `i` to `j - 1` (one or two,
        each a bold-led line, `**A.** Tarifa para determinar ...` and `**TARIFA**`)
        are the caption of the table whose first row is block `j`, `j`."""
        j = i
        while j < self._sk.fin_cuerpo and j - i < 2 and self._es_leyenda(j):
            j += 1
        if j == i or j >= self._sk.fin_cuerpo or j in self._sk.marcas:
            return None
        return j if es_fila_tabla(self._bloques[j]) else None

    def _es_nota_de_tabla(self, i: int) -> bool:
        """A plain paragraph straight after the rows of a captioned table -- `Si el
        precio del automóvil es superior a ... se reducirá ...` -- is that table's
        note, unless it opens the next caption."""
        bloque = self._bloques[i]
        return (self._sk.clases[i][0] == "contenido" and i not in self._sk.marcas
                and i not in self._sk.divisiones
                and len(bloque.text) <= self.MAX_NOTA_DE_TABLA
                and not es_fila_tabla(bloque) and self._fin_de_leyenda(i) is None)

    def _abre_tabla_con_leyenda(self, i: int, fin: int) -> None:
        """Open a `table` at the caption blocks `i .. fin - 1`; the rows that
        follow extend it (rule 13) and the caption is its heading."""
        destino = self._padre_contenedor(self._bloques[i].start)
        if self._transitorios is None and self._pila and not destino.children:
            # A container whose first child this is has no introduction of its
            # own to speak of: the table is the first thing it holds.
            pass
        self._agrega_fila_de_tabla(destino, self._bloques[i])
        for k in range(i + 1, fin):
            self._extiende(self._tabla, self._bloques[k].end)
        self._tabla.heading = _limpia(self._bloques[i].text)
        self._tabla_con_leyenda = self._tabla

    def _agrega_fila_de_tabla(self, destino: AknNode, bloque) -> None:
        """One table row (rule 13): the open table grows over it, or a new
        `table` opens under `destino` -- the intro it sits in, the container,
        a transitorios section or the body."""
        if self._tabla is not None and self._tabla.parent is destino:
            self._extiende(self._tabla, bloque.end)
            return
        numero = 1 + sum(1 for h in destino.children if h.akn_type == "table")
        self._tabla = self._nuevo_nodo(
            "table", bloque.start, bloque.end, padre=destino, num=str(numero),
        )
        self._extiende(self._tabla, bloque.end)

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

    def _bloque_encabezado_de_seccion(self, bloque, campos):
        # Rule 38: a line of the heading of the section the API block opened;
        # the section's span grows over it and its heading is the lines' text.
        hit("S6", "article-heading-line")
        nodo = self._implicita[0]
        self._extiende(nodo, bloque.end)
        linea = _limpia(bloque.text)
        nodo.heading = f"{nodo.heading} {linea}" if nodo.heading else linea
        self._tabla = None

    def _bloque_encabezado_previo(self, bloque, campos):
        # Rule 34: the lines that stand between an API block's marker and the
        # heading it opens a container with. They create no node: the node the
        # heading opens is pulled back over the marker (`_toma_marca`), and so
        # over them, so they are that container's heading and nothing is lost.
        self._tabla = None

    def _bloque_nota_editorial(self, bloque, campos):
        self._bloque_contenido(bloque, campos, es_marcador_estructural=True)

    def _agrega_conclusiones(self, bloque):
        if self.conclusiones is None:
            hit("S11", "conclusions-node", bool(self._sk.marcas))
            self.conclusiones = self.act.add(
                AknNode("conclusions",
                        self._span(self._toma_marca(bloque.start), bloque.end, "conclusions"),
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


#: How many times a document with outline markers is built again to place the
#: divisions its repeated article numbers asked for (rule 31). Each pass can only
#: add divisions, and the worst document measured needs three.
MAX_PASADAS = 6


def build(doc, meta, bloques) -> AknNode:
    """`doc`'s law as a tree — the entry point `md2akn.segmenter` calls.

    Two phases (issue #304). The skeleton (`md2akn.skeleton`) reads what each API block
    is from the outline markers; the pass below then places the blocks in a tree, with
    the text rules working inside the block they are reading.

    A document that carries outline markers is built again, with the divisions
    that separate its restarted numberings (rule 31), until no article has to
    take a `_2` suffix or no division can still be added: the builder reads the
    text in one pass, and where a series of numbers *restarts* is only known
    once it has been read."""
    divisiones: dict[int, tuple[str | None, str | None]] = {}
    for _ in range(MAX_PASADAS):
        skeleton = Skeleton(bloques, divisiones)
        constructor = _Constructor(doc, meta, bloques, skeleton)
        act = constructor.run()
        if not skeleton.marcas or not constructor._eids.suffixed:
            return act
        nuevas = {
            k: v for k, v in constructor.pide_divisiones().items()
            if k not in skeleton.divisiones
        }
        if not nuevas:
            return act
        divisiones.update(nuevas)
    return act
