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

import bisect
from dataclasses import dataclass, field

from md2akn.model import REFERS_TO_TRANSITORIOS, AknNode
from md2akn.structure import _es_el_mismo_numero_en_otra_cifra, _extiende
from md2akn.outline import (
    MARCADORES_EN_TEXTO,
    _plano,
    es_continuacion,
    es_contenido_suelto,
    bloques_de_decreto,
    es_linea_de_etiqueta,
    lee_marcador,
    rol_de_etiqueta,
)

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
    # A section inside a section: the implicit section an API block opens (rule 23)
    # inside the transitorios section of the decree that holds it (rule 34).
    "section": frozenset(
        {"intro", "toc", "level", "section", "article", "content", "table", "hcontainer"}
    ),
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

    The preamble and the conclusions are not part of the hierarchy, but since review
    fix-1 of #301 they may not cross an API block either (they used to, and the check
    was exempt): each stays inside the block it starts in. A tree parsed from a
    document with no markers has no blocks and no violations.
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
    clases = [(lee_marcador(m.group(0)) or {}).get("kind", "") for m in encontradas]
    limites = [*marcas, len(texto) + 1]
    nodos = [n for n in tree.walk() if n.akn_type not in ("act", "body", "preamble", "conclusions")]
    violaciones: list[Violation] = []
    for nodo in tree.children:
        if nodo.akn_type not in ("preamble", "conclusions"):
            continue
        k = bisect.bisect_right(marcas, nodo.start_char) - 1
        if k + 1 < len(marcas) and nodo.end_char > marcas[k + 1]:
            violaciones.append(Violation(
                "block-escape", nodo.eId,
                f"starts in the block at {marcas[k]} and reaches into the block at {marcas[k + 1]}",
            ))
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
        continuacion = k > 0 and raices_previas and (
            es_continuacion(etiquetas[k], etiquetas[k - 1])
            # Rule 40 (issue #300): a bare `CONTENIDO` after a title-only block.
            or es_contenido_suelto(
                etiquetas[k], clases[k], texto[encontradas[k - 1].end():encontradas[k].start()],
                clases[k - 1],
            )
        )
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


# -- I5: the node an API block opened is what its marker says (review fix-2 of #300/#301) --


@dataclass(frozen=True)
class Disagreement:
    """One place where the tree disagrees with the marker of an API block (invariant I5).

    `klass` is one of ``no-node`` (the block opened no node of its own), ``type`` (its
    root is not what its `kind`/`type` say), ``number`` (its root's number is not the
    marker's `num`), ``label-line`` (the block's own label line is content, not the
    heading of its node), ``crossing`` (a leaf that starts in the block reaches into the
    next one) and ``parent`` (a container hangs from a container the markers' order does
    not imply).

    A class that starts with ``documented-`` is a disagreement the parser takes on
    purpose, each with its reason in `CLASES_DOCUMENTADAS`: it is reported, and I5 does
    not fail on it."""

    klass: str
    orden: str
    kind: str
    label: str
    eId: str | None
    detail: str

    @property
    def documented(self) -> bool:
        return self.klass.startswith("documented-")

    def __str__(self) -> str:
        return f"{self.klass} at block {self.orden} ({self.kind} {self.label!r}) {self.eId}: {self.detail}"


#: The disagreements the parser takes on purpose (rules 19, 38 and 39), and why. Each is
#: recognised by the shape of the marker and of the tree, never by an instrument.
CLASES_DOCUMENTADAS = {
    "documented-number-by-numero": (
        "the marker's `label` and its own `numero` disagree, the text prints the number "
        "`numero` says, and the node takes it (rule 19: `ARTICULO XXVII` on the block "
        "numbered 37, whose text prints `XXXVII`)"
    ),
    "documented-number-label-repeated": (
        "the marker's `label` repeats the label of an earlier block and the text prints "
        "another number: the API labelled two blocks with one number, and the node takes "
        "the text's rather than repeat an identifier (rule 19, `alternativa`)"
    ),
    "documented-article-served-as-chapter": (
        "a block the API labels `ARTÍCULO 83` whose text is the heading `Capítulo 83` "
        "(the LIGIE's tariff chapters): the node is the chapter the text says, with the "
        "label's own number (rule 19)"
    ),
    "documented-closing-signature": (
        "a block the API labels `transitorios` whose whole text is the closing signatures "
        "(`Ciudad de México, a ... Rúbrica.`): the node is the `conclusions` the text says "
        "it is"
    ),
    "documented-article-heading": (
        "an API `article` block that holds only a heading and a caption and is followed by "
        "`provision` blocks (the Reglamento General de Infantería's `ARTICULO 1°. "
        "Generalidades` over `1.- ...`): the node is the section the provisions belong to "
        "(rule 38)"
    ),
}

#: What the root node of a block must be, by the block's `kind` (and container `type`):
#: the Akoma Ntoso `akn_type`s the builder may give it. An `other`, `preamble` and
#: `recitals` block is an `hcontainer` (rules 29 and 44) -- a `section` only where the
#: marker says so (it never does for these kinds); the document's first `header` is the
#: `preamble`.
_TIPO_DE_CONTENEDOR = {
    "book": "book", "title": "title", "chapter": "chapter", "section": "section",
    "apartado": "level", "part": "part", "annex": "attachment", "appendix": "attachment",
}
_RANGO_DE_CONTENEDOR = {"part": 0, "book": 2, "title": 3, "chapter": 4, "section": 5}
_TIPOS_DE_HOJA = frozenset(
    {"article", "content", "intro", "table", "toc", "paragraph", "point", "subpoint"}
)


def primera_linea_de_texto(texto: str) -> str | None:
    """The first line of an API block that is neither blank nor a reform annotation."""
    for linea in texto.split("\n"):
        plana = linea.strip()
        if not plana:
            continue
        if plana.replace("**", "").startswith("("):
            continue
        return plana
    return None


def block_conformance(tree: AknNode) -> list[Disagreement]:
    """Invariant I5: for every API block of a document written with outline markers, the
    node it opened is typed and numbered as its marker says, its own label line is that
    node's heading, none of its leaves reaches into the next block and its containers
    hang from the containers the markers' order implies.

    The root of a block is the first node that starts inside it (the node of the block
    itself, not the group of the promulgation decree it sits in); a block that is the
    second half of one the API split in two (rules 30 and 40) has none of its own. A tree
    parsed from a document with no markers has no blocks and no disagreements. The
    disagreements the parser takes on purpose come back with a ``documented-`` class
    (`CLASES_DOCUMENTADAS`).
    """
    texto = tree.span.doc.text
    encontradas = list(MARCADORES_EN_TEXTO.finditer(texto))
    if not encontradas:
        return []
    marcas = [len(texto[: m.start()].rstrip()) if m.start() else 0 for m in encontradas]
    attrs = [lee_marcador(m.group(0)) or {} for m in encontradas]
    limites = [*marcas, len(texto) + 1]
    nodos = [n for n in tree.walk() if n.akn_type not in ("act", "body")]
    # A block that is the second half of the one before it (rules 30 and 40) has no node
    # of its own, and the block before reaches to the end of it.
    es_segunda_mitad = [
        k > 0 and (
            es_continuacion(a.get("label", ""), attrs[k - 1].get("label", ""))
            or es_contenido_suelto(
                a.get("label", ""), a.get("kind", ""),
                texto[encontradas[k - 1].end():encontradas[k].start()], attrs[k - 1].get("kind", ""),
            )
        )
        for k, a in enumerate(attrs)
    ]
    # -- first pass: what each block opened --
    fines: list[int] = []
    dentro_de: list[list[AknNode]] = []
    raiz_de: dict[int, AknNode] = {}
    j = 0
    for k, inicio in enumerate(marcas):
        fin, ultimo = limites[k + 1], k
        while ultimo + 1 < len(marcas) and es_segunda_mitad[ultimo + 1]:
            ultimo += 1
            fin = limites[ultimo + 1]
        fines.append(fin)
        while j < len(nodos) and nodos[j].start_char < inicio:
            j += 1
        dentro = []
        m = j
        while m < len(nodos) and nodos[m].start_char < fin:
            dentro.append(nodos[m])
            m += 1
        dentro_de.append(dentro)
        if es_segunda_mitad[k] or not dentro or not texto[encontradas[k].end(): fin].strip():
            continue
        raiz = dentro[0]
        if raiz.refers_to == "#promulgation" and len(dentro) > 1 and dentro[1].parent is raiz:
            # The group the blocks of the Mexican decree are kept together under opens
            # at the first of them; the block's own node is inside it (rule 29).
            raiz = dentro[1]
        raiz_de[k] = raiz
    # -- the containers the markers' order implies above each container block --
    decreto = bloques_de_decreto(attrs)
    abiertos: list[tuple[float, int, str]] = []  # (rank, marker index, type)
    esperado: dict[int, int | None] = {}
    for k, a in enumerate(attrs):
        kind, tipo = a.get("kind", ""), a.get("type", "")
        raiz = raiz_de.get(k)
        if kind == "other":
            rol = rol_de_etiqueta(a.get("label", ""))
            if k in decreto and rol in (None, "promulgation"):
                rol = "promulgation"
            elif rol == "promulgation":
                rol = None
            if rol in ("promulgation", "conclusions", "note"):
                abiertos.clear()  # a text of its own, outside whatever was open (rule 29)
            continue
        if kind == "article" and raiz is not None and raiz.akn_type in _RANGO_DE_CONTENEDOR:
            kind, tipo = "container", raiz.akn_type  # a tariff chapter served as an article
        if kind != "container":
            continue
        if tipo == "part":
            # Rule 45: a part holds the book, title or chapter the very next marker
            # opens and ranks just above it; any other part is read from the document.
            siguiente = attrs[k + 1] if k + 1 < len(attrs) else {}
            if siguiente.get("kind") != "container" or siguiente.get("type") not in (
                "book", "title", "chapter"
            ):
                continue
            rango = _RANGO_DE_CONTENEDOR[siguiente["type"]] - 0.5
            partes = [r for r, _, t in abiertos if t == "part"]
            if partes:
                rango = min(rango, partes[0])  # parts are siblings of parts
        elif tipo in _RANGO_DE_CONTENEDOR:
            rango = float(_RANGO_DE_CONTENEDOR[tipo])
        elif tipo in ("annex", "appendix"):
            abiertos.clear()
            continue
        else:
            continue
        while abiertos and abiertos[-1][0] >= rango:
            abiertos.pop()
        esperado[k] = abiertos[-1][1] if abiertos else None
        abiertos.append((rango, k, tipo))
    # -- second pass: compare each block with the node it opened --
    resultado: list[Disagreement] = []
    etiquetas_vistas: set[str] = set()
    for k, a in enumerate(attrs):
        kind, etiqueta = a.get("kind", ""), a.get("label", "")
        siguiente_kind = attrs[k + 1].get("kind", "") if k + 1 < len(attrs) else ""

        def informa(klass, nodo, detalle):
            resultado.append(Disagreement(
                klass, a.get("orden", str(k)), kind, etiqueta,
                nodo.eId if nodo is not None else None, detalle,
            ))

        if kind == "article" and a.get("num"):
            repetida = _plano(a["num"]) in etiquetas_vistas
            etiquetas_vistas.add(_plano(a["num"]))
        else:
            repetida = False
        if es_segunda_mitad[k]:
            continue
        # A leaf may not reach into the next block (rule 34).
        for nodo in dentro_de[k]:
            if nodo.akn_type in _TIPOS_DE_HOJA and nodo.end_char > fines[k] \
                    and fines[k] < len(texto):
                informa("crossing", nodo, f"a {nodo.akn_type} ends at {nodo.end_char}, past {fines[k]}")
        if not texto[encontradas[k].end(): fines[k]].strip():
            continue
        raiz = raiz_de.get(k)
        if raiz is None:
            informa("no-node", None, "no node starts in this block")
            continue
        cuerpo = texto[encontradas[k].end(): fines[k]]
        # -- type --
        if kind == "header":
            permitidos = {"preamble"} if k == 0 else {"hcontainer"}
        elif kind in ("other", "preamble", "recitals"):
            permitidos = {"hcontainer", "article", "conclusions", "preamble"}
        elif kind in ("article", "provision"):
            permitidos = {"article"}
        elif kind == "toc":
            permitidos = {"toc"}
        elif kind in ("transitorios", "decree"):
            permitidos = {"section"}
        elif kind == "container":
            permitidos = {_TIPO_DE_CONTENEDOR.get(a.get("type", ""), "hcontainer")}
        else:
            permitidos = None
        if permitidos is not None and raiz.akn_type not in permitidos:
            if kind == "article" and raiz.akn_type in ("chapter", "section", "part", "book", "title"):
                informa("documented-article-served-as-chapter", raiz,
                        f"root is a {raiz.akn_type} numbered {raiz.num!r}")
            elif kind == "article" and raiz.akn_type in ("hcontainer", "section") \
                    and siguiente_kind == "provision":
                informa("documented-article-heading", raiz, f"root is a {raiz.akn_type}")
            elif kind == "transitorios" and raiz.akn_type == "conclusions":
                informa("documented-closing-signature", raiz, "the block is only the signatures")
            else:
                informa("type", raiz, f"root is a {raiz.akn_type}, the marker says {sorted(permitidos)}")
        # -- number --
        elif kind in ("article", "container") and a.get("num") and permitidos is not None \
                and raiz.akn_type in permitidos:
            nodo_num, marca_num = _plano(raiz.num or ""), _plano(a["num"])
            # The text may say more than the label (`82 BIS` under `ARTÍCULO 82`) or spell
            # the same number in the other numeral (`I` under `ARTÍCULO 1`): rule 19.
            if nodo_num != marca_num and not _extiende(nodo_num, marca_num) \
                    and not _extiende(marca_num, nodo_num) \
                    and not _es_el_mismo_numero_en_otra_cifra(raiz.num or "", a["num"]):
                numero = a.get("numero", "")
                if numero.isdigit() and numero != "0" and _plano(raiz.num or "") == _plano(numero) \
                        or _es_el_mismo_numero_en_otra_cifra(raiz.num or "", numero):
                    informa("documented-number-by-numero", raiz,
                            f"node {raiz.num!r}, marker num {a['num']!r}, numero {numero}")
                elif repetida:
                    informa("documented-number-label-repeated", raiz,
                            f"node {raiz.num!r}, marker num {a['num']!r} (repeated)")
                else:
                    informa("number", raiz, f"node number {raiz.num!r}, marker num {a['num']!r}")
        # -- label line --
        if kind in ("other", "preamble", "recitals", "header") and raiz.akn_type == "hcontainer":
            linea = primera_linea_de_texto(cuerpo)
            if linea is not None and es_linea_de_etiqueta(linea, etiqueta):
                posicion = texto.find(linea, encontradas[k].end())
                primero = raiz.children[0].start_char if raiz.children else raiz.end_char
                if posicion >= primero:
                    informa("label-line", raiz, f"the line {linea!r} is content, not the heading")
        # -- parent --
        if kind == "container" and k in esperado and raiz.akn_type in _RANGO_DE_CONTENEDOR \
                or k in esperado and kind == "article":
            padre, previo = raiz.parent, esperado[k]
            if previo is None:
                if padre is not None and padre.akn_type in _RANGO_DE_CONTENEDOR \
                        and _RANGO_DE_CONTENEDOR[padre.akn_type] >= _RANGO_DE_CONTENEDOR[raiz.akn_type]:
                    informa("parent", raiz, f"hangs from a {padre.akn_type} ({padre.eId})")
            else:
                esperada = raiz_de.get(previo)
                if esperada is not None and padre is not esperada \
                        and not any(n is esperada for n in _ancestros(raiz)):
                    informa("parent", raiz,
                            f"hangs from {padre.eId if padre else None}, the markers imply {esperada.eId}")
    return resultado


def _ancestros(nodo: AknNode):
    nodo = nodo.parent
    while nodo is not None:
        yield nodo
        nodo = nodo.parent
