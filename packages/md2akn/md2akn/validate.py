"""Structural invariants of a parsed tree (issue #162).

`validate(tree)` is a public function and not merely a test, for two reasons:
the mass sweep over the SCJN corpus reuses it — the sweep is not a test that
passes or fails, it is the instrument that says where work is still missing —
and whoever hands this package a document of their own has no other way to ask
whether the result is well-formed.

What is checked here are properties of *any* tree, never of one particular
law: nothing in this module knows what a Mexican law looks like. A law with no
articles at all is not invalid — it is a *measurement*, and it belongs in the
sweep's report, not in a list of violations. What is invalid is a tree that
contradicts itself: a child outside its parent, two nodes claiming one eId, a
`point` with no `paragraph` over it.

Coverage is the one number that is reported rather than judged: it has no
correct value in the abstract, so `validate` returns the figure and lets the
caller set the bar. `packages/../scripts/md2akn_sweep.py` is
where that bar lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from md2akn.model import REFERS_TO_TRANSITORIOS, AknNode
from md2akn.outline import MARCADORES_EN_TEXTO, es_continuacion, lee_marcador

#: Which types may contain which. A tree that nests outside this table is
#: broken, whatever the document said: it means the builder lost its stack,
#: not that the law was unusual. `content` and `conclusions` are leaves and so
#: appear only as values.
ANIDAMIENTO: dict[str, frozenset[str]] = {
    "act": frozenset({"preamble", "body", "conclusions"}),
    "preamble": frozenset({"content"}),
    # A `part` (`PARTE`), an `attachment` (`ANEXO`/`APÉNDICE`) and a `toc` are
    # review fix-1 of #291's rules 15 and 16: an attachment closes every other
    # container, so it sits straight under the body; a part sits under the
    # body, an attachment or, in a document whose chapters come first, inside
    # a chapter or a title.
    "body": frozenset(
        {"attachment", "part", "toc", "intro", "book", "title", "chapter", "section", "level",
         "article", "content", "table", "hcontainer"}
    ),
    "attachment": frozenset(
        {"intro", "toc", "part", "book", "title", "chapter", "section", "level", "article",
         "content", "table", "hcontainer"}
    ),
    "part": frozenset(
        {"intro", "toc", "book", "title", "chapter", "section", "level", "article", "content",
         "table", "hcontainer"}
    ),
    "toc": frozenset(),
    # A container's `intro` (rule 12, issue #291) and the `table`s of rule 13
    # sit among its children; an `intro` holds the plain paragraphs and the
    # tables of the text a container opens with.
    "book": frozenset(
        {"intro", "toc", "part", "title", "chapter", "section", "level", "article", "content",
         "table", "hcontainer"}
    ),
    "title": frozenset(
        {"intro", "toc", "part", "chapter", "section", "level", "article", "content", "table", "hcontainer"}
    ),
    "chapter": frozenset(
        {"intro", "toc", "part", "section", "level", "article", "content", "table", "hcontainer"}
    ),
    "section": frozenset({"intro", "toc", "level", "article", "content", "table", "hcontainer"}),
    "intro": frozenset({"content", "table"}),
    # An apartado holds articles; an apartado *inside* an article holds that
    # article's own fracciones -- which is why `paragraph` is here too. And
    # `point`, because an apartado may go straight to incisos with no fracción
    # in between: article 122 B of the Constitution and article 32 J of the
    # LGIPE both do, and both are the document saying so, not a broken stack.
    "level": frozenset({"intro", "article", "paragraph", "point", "content", "table", "hcontainer"}),
    "article": frozenset({"level", "paragraph", "content"}),
    "paragraph": frozenset({"point", "content"}),
    "point": frozenset({"subpoint", "content"}),
    "subpoint": frozenset({"content"}),
    "content": frozenset(),
    "table": frozenset(),
    "conclusions": frozenset(),
    # Review fix-4 of #291 (rules 29 to 31): a labelled block or division holds
    # whatever a container holds, divisions and further blocks included.
    "hcontainer": frozenset(
        {"intro", "toc", "part", "attachment", "book", "title", "chapter", "section", "level",
         "hcontainer", "article", "content", "table"}
    ),
}


@dataclass
class Violation:
    """One broken invariant, named by the node it was found on."""

    rule: str
    eId: str | None
    detail: str

    def __str__(self) -> str:
        return f"{self.rule} at {self.eId}: {self.detail}"


@dataclass
class Report:
    """The outcome of `validate`.

    `ok` is about the invariants only. `covered` is reported, never judged —
    see the module docstring.
    """

    violations: list[Violation] = field(default_factory=list)
    #: Non-whitespace characters whose innermost owner is a node that may
    #: hold text — that is, characters the segmenter actually *placed*.
    cubiertos: int = 0
    #: Non-whitespace characters in the document, frontmatter excluded.
    totales: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def cobertura(self) -> float:
        """Percentage of the document the segmenter placed, 100.0 if empty.

        A character is *placed* when the innermost node containing it is one
        that may hold text of its own. Text sitting directly on the `body` is
        text no article or container claimed — that, and not "no leaf claims
        it", is what "the segmenter lost this" means: a fracción's own
        introductory line has children under it and is not lost at all.
        """
        if not self.totales:
            return 100.0
        return 100.0 * self.cubiertos / self.totales

    def __str__(self) -> str:
        cabeza = "valid" if self.ok else f"{len(self.violations)} violation(s)"
        return f"<Report {cabeza}, {self.cobertura:.2f}% covered>"


#: Types that hold no text of their own. Everything else may: an article's
#: chapeau, a fracción's introductory line and a container's own heading are
#: all text that legitimately belongs to a node *with* children.
SIN_TEXTO_PROPIO = frozenset({"act", "body"})


def _duenio(nodo: AknNode, reclamados: bytearray, desplazamiento: int, largo: int):
    """Mark, for every character, the innermost node that contains it.

    Children are painted after the parent, so the deepest node wins — which is
    the whole point: a character's owner is the most specific node that claims
    it, and *that* is what says whether the segmenter placed it or merely left
    it lying on the body.
    """
    inicio = max(0, nodo.start_char - desplazamiento)
    fin = min(largo, nodo.end_char - desplazamiento)
    marca = 0 if nodo.akn_type in SIN_TEXTO_PROPIO else 1
    for i in range(inicio, fin):
        reclamados[i] = marca
    for hijo in nodo.children:
        _duenio(hijo, reclamados, desplazamiento, largo)


def validate(tree: AknNode, *, texto: str | None = None) -> Report:
    """Check a parsed tree against the structural invariants.

    `texto` is the source the tree was parsed from, needed only for the
    coverage figure. Left out, coverage is measured against the root's own
    span, which is the same thing for any tree this package builds.
    """
    rep = Report()
    vistos: dict[str, AknNode] = {}
    orden_anterior = -1

    for nodo in tree.walk():
        eid = nodo.eId

        # -- eIds are unique across the act ---------------------------------
        if eid is not None:
            if eid in vistos:
                rep.violations.append(
                    Violation("duplicate-eId", eid, "already used by another node")
                )
            vistos[eid] = nodo

        # -- walk() is document order ---------------------------------------
        if nodo.start_char < orden_anterior:
            rep.violations.append(
                Violation(
                    "walk-order",
                    eid,
                    f"starts at {nodo.start_char}, after a node at {orden_anterior}",
                )
            )
        orden_anterior = nodo.start_char

        # -- the parent link is symmetric -----------------------------------
        if nodo.parent is not None and not any(
            h is nodo for h in nodo.parent.children
        ):
            rep.violations.append(
                Violation("orphan", eid, f"not among {nodo.parent.eId}'s children")
            )

        # -- nesting is legal ------------------------------------------------
        permitidos = ANIDAMIENTO.get(nodo.akn_type)
        if permitidos is None:
            rep.violations.append(
                Violation("unknown-type", eid, f"{nodo.akn_type!r} is not an AKN type")
            )
            permitidos = frozenset()

        previo_fin = None
        for hijo in nodo.children:
            if hijo.akn_type not in permitidos:
                rep.violations.append(
                    Violation(
                        "bad-nesting",
                        hijo.eId,
                        f"a {hijo.akn_type} cannot sit inside a {nodo.akn_type}",
                    )
                )
            # -- children lie inside the parent -----------------------------
            if hijo.start_char < nodo.start_char or hijo.end_char > nodo.end_char:
                rep.violations.append(
                    Violation(
                        "span-outside-parent",
                        hijo.eId,
                        f"[{hijo.start_char}, {hijo.end_char}) escapes "
                        f"{nodo.akn_type} [{nodo.start_char}, {nodo.end_char})",
                    )
                )
            # -- and are disjoint from each other ---------------------------
            if previo_fin is not None and hijo.start_char < previo_fin:
                rep.violations.append(
                    Violation(
                        "overlapping-siblings",
                        hijo.eId,
                        f"starts at {hijo.start_char}, before {previo_fin}",
                    )
                )
            previo_fin = hijo.end_char

    # -- coverage -----------------------------------------------------------
    # Coverage counts non-whitespace characters only: the blank line between
    # two blocks belongs to neither of them and never will, since a node's
    # span ends where its text ends. Counting it as lost put a ceiling near
    # 96% on a tree that had lost nothing, which is what #162's first sweep
    # measured before this was fixed.
    #
    # The frontmatter is out of the denominator too. It is deliberately not a
    # node — it is parsed into the root's `meta` — so charging it as uncovered
    # would be scoring the segmenter for doing exactly what it should.
    fuente = texto if texto is not None else tree.text
    desplazamiento = 0 if texto is not None else tree.start_char
    inicio_cuerpo = max(0, tree.start_char - desplazamiento)

    reclamados = bytearray(len(fuente))
    _duenio(tree, reclamados, desplazamiento, len(fuente))

    rep.totales = sum(1 for c in fuente[inicio_cuerpo:] if not c.isspace())
    rep.cubiertos = sum(
        1
        for i in range(inicio_cuerpo, len(fuente))
        if reclamados[i] and not fuente[i].isspace()
    )
    return rep


#: What can open a node of its own from the text of an API block: the types
#: `_block_escapes` lets sit beside a block's article, because Akoma Ntoso has no
#: article inside an article.
_ESTRUCTURA_DE_TEXTO = frozenset(
    {"article", "book", "title", "chapter", "section", "level", "part", "attachment", "hcontainer"}
)


def _block_escapes(tree: AknNode) -> list[Violation]:
    """Invariant I1 (rule 34, review fix-6 of #291): what an API block's text
    holds hangs from what the block opened.

    A block is the characters from its marker to the next one. The nodes that
    start inside it are read in document order, and the first, *the block's
    root*, is what the block opened (a block that is the continuation of the one
    before, `CONTENIDO ...`, has the earlier block's root). Then:

    * a block whose root is a **container** (an `hcontainer`, a `section`, a
      chapter, an annex ...) holds everything else of its text *inside* that
      root -- not beside it, not in the container the block before opened --
      except a transitorios section its own text opens (rule 20: it hangs from
      the body);
    * a block whose root is an **article**, or a leaf of an article opened
      before it, may print further articles or headings (an article of a
      treaty's `1.1.1` printing its `1.1.1.1`, a heading ahead of the next
      article), which sit beside the root -- there is no article inside an
      article -- but every leaf of text (`content`, `table`, `toc`, `intro`)
      that comes after a node the block opened hangs from a node the block
      opened, never from something older.

    The preamble and the conclusions are not part of the hierarchy and are left
    alone. A tree parsed from a document with no markers has no blocks and no
    violations.
    """
    texto = tree.span.doc.text
    encontradas = list(MARCADORES_EN_TEXTO.finditer(texto))
    if not encontradas:
        return []
    # A block's range begins where the text before its marker ends: the blank
    # lines between two blocks belong to the block that follows, and a node's
    # span is pulled back over them.
    marcas = [len(texto[: m.start()].rstrip()) if m.start() else 0 for m in encontradas]
    etiquetas = [(lee_marcador(m.group(0)) or {}).get("label", "") for m in encontradas]
    limites = [*marcas, len(texto) + 1]
    nodos = [n for n in tree.walk() if n.akn_type not in ("act", "body", "preamble", "conclusions")]
    violaciones: list[Violation] = []
    raices_previas: list[AknNode] = []
    j = 0
    for k, inicio in enumerate(marcas):
        fin = limites[k + 1]
        while j < len(nodos) and nodos[j].start_char < inicio:
            j += 1
        dentro = []
        m = j
        while m < len(nodos) and nodos[m].start_char < fin:
            dentro.append(nodos[m])
            m += 1
        if not dentro:
            continue
        continuacion = k > 0 and es_continuacion(etiquetas[k], etiquetas[k - 1]) and raices_previas
        raices = list(raices_previas) if continuacion else [dentro[0]]
        raiz = raices[0]
        # A block whose first node is a leaf, an index or a table continues what is
        # open already (the heading line of the article is the last line of the
        # block before it): what it holds is siblings of that node, inside its parent.
        hoja = not continuacion and raiz.akn_type not in _ESTRUCTURA_DE_TEXTO
        propios = {id(n) for n in dentro}

        def cuelga_de(nodo, candidatos):
            while nodo is not None:
                if any(nodo is c for c in candidatos):
                    return True
                nodo = nodo.parent
            return False

        abiertos = [] if continuacion else [dentro[0].parent if hoja else dentro[0]]
        for nodo in dentro[0 if continuacion else 1:]:
            if nodo.refers_to == REFERS_TO_TRANSITORIOS and nodo.akn_type == "section":
                raices.append(nodo)
                abiertos.append(nodo)
                continue
            if nodo.akn_type == "attachment" and nodo.parent is not None \
                    and nodo.parent.akn_type == "body":
                # Rule 16: an annex a text prints sits straight under the body,
                # and what it holds is its own.
                escapa = False
                raices.append(nodo)
                abiertos.append(nodo)
            elif not hoja and raiz.akn_type in _ESTRUCTURA_DE_TEXTO and raiz.akn_type != "article":
                escapa = not cuelga_de(nodo, raices)
            elif nodo.akn_type in _ESTRUCTURA_DE_TEXTO:
                escapa = False
            else:
                # A leaf that comes after something the block opened.
                escapa = bool(abiertos) and not cuelga_de(nodo, abiertos)
            if escapa:
                violaciones.append(Violation(
                    "block-escape", nodo.eId,
                    f"starts in the block at {inicio} (root {raiz.eId}) but hangs "
                    f"from {nodo.parent.eId if nodo.parent is not None else None}",
                ))
            if id(nodo) in propios:
                abiertos.append(nodo)
        raices_previas = raices
    return violaciones
