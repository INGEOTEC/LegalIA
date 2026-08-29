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

from md2akn.model import AknNode

#: Which types may contain which. A tree that nests outside this table is
#: broken, whatever the document said: it means the builder lost its stack,
#: not that the law was unusual. `content` and `conclusions` are leaves and so
#: appear only as values.
ANIDAMIENTO: dict[str, frozenset[str]] = {
    "act": frozenset({"preamble", "body", "conclusions"}),
    "preamble": frozenset({"content"}),
    "body": frozenset(
        {"book", "title", "chapter", "section", "level", "article", "content"}
    ),
    "book": frozenset({"title", "chapter", "section", "level", "article", "content"}),
    "title": frozenset({"chapter", "section", "level", "article", "content"}),
    "chapter": frozenset({"section", "level", "article", "content"}),
    "section": frozenset({"level", "article", "content"}),
    # An apartado holds articles; an apartado *inside* an article holds that
    # article's own fracciones -- which is why `paragraph` is here too. And
    # `point`, because an apartado may go straight to incisos with no fracción
    # in between: article 122 B of the Constitution and article 32 J of the
    # LGIPE both do, and both are the document saying so, not a broken stack.
    "level": frozenset({"article", "paragraph", "point", "content"}),
    "article": frozenset({"level", "paragraph", "content"}),
    "paragraph": frozenset({"point", "content"}),
    "point": frozenset({"subpoint", "content"}),
    "subpoint": frozenset({"content"}),
    "content": frozenset(),
    "conclusions": frozenset(),
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
