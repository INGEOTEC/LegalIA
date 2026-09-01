# md2akn

Segment a Mexican federal law's Markdown into a **navigable hierarchy** —
articles inside their chapter, fracciones inside their article, incisos inside
their fracción — labelled with [Akoma
Ntoso](https://www.oasis-open.org/committees/tc_home.php?wg_abbrev=legaldocml)'s
vocabulary.

```python
from md2akn import parse_legal_provisions

ley = parse_legal_provisions("cpeum/02-06-2026.md")

ley.meta["ordenamiento"]              # 'CONSTITUCION POLITICA DE LOS ESTADOS UNIDOS MEXICANOS'
ley.find("art_27__para_VII__point_a") # article 27, fracción VII, inciso a)

for nodo in ley.walk():               # every node, in document order
    print(nodo.akn_type, nodo.num, nodo.eId)
```

## The name does not mean XML

`md2akn` follows the repository's naming convention (`nota2md`, `dof2md`:
input → output), but the `2` does **not** mean this package emits Akoma Ntoso
XML. It never does, and neither does anything else in LegalIA: the project
does not emit Akoma Ntoso XML at any point (issue #168). What is taken from
the standard (an OASIS Standard since 2018, reviewed in
[issue #91](https://github.com/INGEOTEC/LegalIA/issues/91)) is its
**vocabulary**: the element names a node's `akn_type` is drawn from, the `eId`
naming convention, and `refersTo` for the structures the standard has no
element for.

Not emitting XML is what lets the tree stay faithful to the source. Akoma
Ntoso's `hierarchy` content model is a strict either/or — an element has a
flat `content` **or** hierarchical children, never both — and a Mexican
article routinely has both: an introductory sentence ("Artículo 4. Son
obligaciones:"), then its fracciones, then one or more closing paragraphs. An
XML serialization has to choose one and flatten the other away; this tree does
not. That is the reason the decision went the way it did — an earlier XML
converter did live in `nota2md`, and it was removed rather than kept alongside
this package (it remains in git history if it is ever needed again).

## Vocabulary

| Mexican structure | `akn_type` | |
|---|---|---|
| the whole document | `act` | the root |
| "Al margen un sello…", the enacting formula | `preamble` | |
| the normative body | `body` | |
| LIBRO / TÍTULO / CAPÍTULO / SECCIÓN | `book` / `title` / `chapter` / `section` | |
| APARTADO A/B | `level` | + `refers_to="#apartado"` |
| Artículo N | `article` | |
| fracción / inciso / subinciso | `paragraph` / `point` / `subpoint` | |
| TRANSITORIOS | `section` | + `refers_to="#transitorios"` |
| a plain paragraph | `content` | numbered 1..n inside its parent (`art_1o__p_2`) |
| the closing signatures | `conclusions` | |

Akoma Ntoso has no element for "transitorios" or for "apartado"; both are
expressed as an existing element plus `refers_to`, which is the convention
issue #91 settled on for structures the standard does not name.

Every paragraph of an article is a `content` child of it, whether or not the
article also has fracciones, and carries a `num` — so "el párrafo segundo del
artículo 1o." resolves to `art_1o__p_2`. That is an ordinary Mexican
citation, and reforms are published against it ("se reforma el párrafo
tercero del artículo 4o."), so the unit cannot exist only when a sibling list
happens to (issue #181). The count is scoped to the immediate parent, which
is what makes it the article's *own* paragraphs: text that continues a
fracción belongs to that fracción and never advances the article's numbering.

## What it recognizes

The container hierarchy nests by **precedence rank** (`book` > `title` >
`chapter` > `section` > `level`), not by any depth marker — the text has none.
Meeting a container closes every open one of equal or lower rank, so a law
that opens with `CAPÍTULO I` and only later reaches `TÍTULO PRIMERO` comes out
with the chapter closed and hanging at the top of the body, which is what the
document actually says. Numbering that restarts needs no special handling:
`tit_PRIMERO__cap_I` and `tit_SEGUNDO__cap_I` are different paths. An article
with no container hangs off `body` — many short laws have no chapters at all.

Every pattern was written against counts measured over all 315 laws of the
SCJN corpus, so the variation it covers is the variation that is there:
`Artículo`/`ARTICULO`/`ARTÍCULO`/`Art.`, with and without bold (the oldest
laws use none), ordinals (`1o.`, `1°`, `1º`), Latin suffixes in any case and
joined by a space or a dash (`28 Bis`, `3° bis`, `103-Bis`, `3° Bis 1`),
letter suffixes (`27-A`, and the CCF's `410 A`), two articles under one
heading (`Art. 30,31.` — kept as one node with `num="30,31"`, since dividing
the shared text between them is not possible without guessing), and container
headings with or without bold, with the epigraph in the *following* block.

Inside an article, the markers cannot be told apart in isolation and no
pattern can fix that: `V.`, `X.`, `C.` and `D.` are valid Roman numerals *and*
valid capital letters, `**A.**` is an inciso 14,834 times and an apartado in
articles 2 and 123 of the Constitution, and a `1.` opening a block is a
subinciso or a sentence that begins with a figure. What resolves almost all of
it is **consecutiveness**: a list can only be *opened* by the first label of
its series (`I`, `A`, `a`, `1`) and only *continued* by a label that comes
after the previous one. A `V.` with no `I.` before it opens nothing; the `C.`
in "El C. Primer Jefe" opens nothing.

Continuation is monotonic rather than strictly successive, because three real
things break strict succession and none is an error: repealed fracciones
vanish and leave gaps (`III.` then `V.`), Latin suffixes insert `VII Bis.`
between `VII.` and `VIII.`, and a second list in the same article restarts at
`I.` — a new list, whose repeated labels the eId allocator disambiguates.

A block with no marker belongs to the deepest open node when another item
follows it, and is the article's own closing paragraph when the article ends
instead. The two are identical in form, so the decision waits for the next
block; `is_chapeau` and `is_tail` mark the result. Those two flags are the
only thing hierarchy adds to a paragraph — the paragraph itself is a node
either way.

A law's transitional provisions come out as **several sibling sections**, one
per decree that added some — the `## Transitorios` marker opens the first and
each `**D.O.F. 15 DE SEPTIEMBRE DE 2024.**` header opens another. They are
never merged: which decree a provision belongs to is exactly what the
separation records.

## The tree

`parse_legal_provisions(path)` returns the root `AknNode`:

| | |
|---|---|
| `akn_type` | the vocabulary term above |
| `num` | the number as the document wrote it: `"1o."`, `"27"`, `"I"`, `"a"`, `"PRIMERO"` |
| `heading` | the epigraph, when the structure has one |
| `eId` | the hierarchical identifier: `art_27__para_VII__point_a` |
| `span` | the spaCy `Span` covering the node |
| `parent` / `children` | the hierarchy |
| `notes` | the `(REFORMADO, D.O.F. …)` annotations attached to this node |
| `refers_to` | `"#transitorios"` / `"#apartado"` where the standard falls short |
| `meta` | the file's YAML frontmatter (root only) |
| `text` | the node's own raw Markdown |

`walk()` is a preorder traversal of that same tree — one structure, two ways
to consume it, not two implementations — and `find(eId)` addresses a node by
identifier. eIds are unique across the document: a law can legally restate the
same fracción label under two separate "I. a X." lists, and the second
claimant gets a `_2` suffix rather than a duplicate.

## Annotations

The SCJN's consolidated texts carry, between the articles, the notes that say
which reform touched which part:

```
**(REFORMADO PRIMER PÁRRAFO, D.O.F. 10 DE JUNIO DE 2011)**

**ARTICULO 1o.-** En los Estados Unidos Mexicanos …
```

Those are not nodes and never appear in `walk()`. They are read into
`Annotation`s on the `notes` of the node that follows them — the node whose
span is pulled back to cover the annotation's own text, so the document stays
fully covered:

| | |
|---|---|
| `action` | `REFORMADO` / `ADICIONADO` / `DEROGADO` / `REUBICADO` / `ACTUALIZADO` / `F. DE E.`, and the compounds (`REFORMADO Y REUBICADO`) |
| `scope` | what was touched: `PRIMER PARRAFO`, `SU DENOMINACION`, `CON LOS ARTICULOS QUE LO INTEGRAN` |
| `date` | the DOF date, as a `datetime.date` |
| `source` | the instrument cited instead of, or besides, the date |
| `raw` | the annotation's own text, always, parsed or not |

Gender and number are folded (`REFORMADA` → `REFORMADO`): they agree with what
was reformed and say nothing about the reform.

Only a parenthesis whose head is one of those actions is an annotation. The
corpus writes plenty of other things in the same shape — `(NOTA: EL 22 DE
JUNIO DE 2023, EL PLENO DE LA SUPREMA CORTE…)`, `(ARANCEL)`, `(VÉASE TABLA
ANEXA)` — and none of them is a reform; they stay text.

In Akoma Ntoso terms this is the **passive** side of a modification
(`lifecycle` / `passiveModifications`). No such XML is emitted — see below —
but the information is here at the granularity someone would need to emit it.
The **active** side, a DOF decree saying what *it* changed, is issue #163.

Over the 315 laws of the SCJN corpus this reads **36,836 annotations, of which
36,835 yield an action and a date or a source**; the one that does not is a
typo in the source itself (`13 DE AGOSTO **CE** 2009`), and it is kept with
its `raw` rather than dropped.

## Validation

`validate(tree)` returns a `Report`: the structural invariants that broke, if
any, and how much of the document the segmenter placed.

```python
from md2akn import parse_legal_provisions, validate

rep = validate(parse_legal_provisions("lft/01-10-2024.md"))
rep.ok            # no invariant broken
rep.violations    # [Violation(rule, eId, detail), ...]
rep.cobertura     # 100.0
```

The invariants are properties of *any* tree, never of one particular law: a
child's span lies inside its parent's, siblings' spans are disjoint, `walk()`
is document order, eIds are unique across the act, `node.parent.children`
contains `node`, and the nesting is one the `ANIDAMIENTO` table allows. A law
with no articles at all is not a violation — it is a *measurement*, and it
belongs in a report rather than in a list of errors.

**Coverage** is the percentage of non-whitespace characters the segmenter
placed, where a character counts as placed when the innermost node containing
it is one that may hold text of its own. Text sitting directly on the `body`
is text no article or container claimed — that, and not "no leaf claims it",
is what losing text means: a fracción's own introductory line has children
under it and is not lost at all. The frontmatter is outside the denominator,
since it is deliberately parsed into `meta` rather than into a node.

### Against the SCJN corpus

`scripts/md2akn_sweep.py` runs the whole thing over the 315 laws and writes a
report sorted worst-metric-first. It is not a test and does not run in CI: it
needs data this repository deliberately does not version. The current run:

| | |
|---|---|
| laws parsed | 315 / 315, none raising |
| coverage | **100.00 % median, 99.99 % mean, 99.45 % worst** — all 315 above 99 % |
| invariant violations | **0**, across every law |
| laws with no article recognized | 0 |
| articles / fracciones / incisos | 53,740 / 65,318 / 7,657 |
| sibling articles sharing a number | 498 (0.9 %) |
| annotations, and unparsed ones | 36,838 / **0** |
| throughput | 533 KB/s; the 1.89 MB worst case (`ligie-2022`) in 6.8 s |

The threshold this was held to, fixed in issue #162 before it closed: **zero
invariant violations, every law with at least one article, and ≥99 % coverage
on ≥95 % of the laws.** The measured result clears it — 100 % of the laws are
above 99 %, not 95 % of them.

## The spaCy layer

The segmenter is a pipeline component, so it composes with anything else you
want to run over the same `Doc`:

```python
import spacy

nlp = spacy.blank("es")
nlp.add_pipe("akn_segmenter")
doc = nlp(markdown_text)

doc._.akn_tree             # the root AknNode
doc._.akn_meta             # the frontmatter
doc.spans["akn"]           # every node's Span, in document order, labelled with its eId
```

and `Span._.akn_type`, `._.num`, `._.eId`, `._.node` get from a span back to
its node.

**One caveat on those extensions.** spaCy stores them on the `Doc` keyed by
`(name, start_char, end_char)` — the span's label is *not* part of that key —
so two nodes covering the same characters share one set of values. That is
common, not exotic: `act` and `body` coincide in any law with no preamble, and
a container coincides with its only child. The rule is that **the innermost
(most specific) node wins**. `doc.spans["akn"]` is unaffected: it is a list,
so it keeps one entry per node, each labelled with its own eId. When in doubt,
the tree is the authority.

Three things are settled for cost, not taste:

- `spacy.blank("es")` — the tokenizer and nothing else. The rules are
  deterministic; a tagger and parser no rule consults would cost per document
  for nothing.
- `nlp.max_length` is raised to 10,000,000. spaCy's default is 1,000,000 and
  the corpus' largest law, `ligie-2022`, is 1.89 MB. The default exists to
  stop a parser/NER pipeline from exhausting memory; a tokenizer-only pipeline
  does not incur that cost.
- The scan walks **lines of the raw string**, never tokens. Markdown is a
  block format — what makes a line an article heading is how it begins — so
  tokenization is paid once and no rule iterates tokens.

## Which file to hand it

Whichever you like: the input is a Markdown file on disk, and that is the
package's whole interface to the world. In particular the SCJN corpus'
convention — *the most recent date without a `-2`/`-3` suffix* — is a fact
about that corpus, not about Markdown, and lives in
[`scripts/`](https://github.com/INGEOTEC/LegalIA/tree/master/scripts), not
here.

For the same reason the only dependency is `spacy`. Nothing from `dofjson`,
`nota2md` or `dof2md`: taking any of them would drag in release downloads and
mineru for a package that reads a file.

## Installation

```bash
pip install md2akn
```

```bash
pip install -e "packages/md2akn[test]"    # for development in this monorepo
```

## Development

```bash
pytest packages/md2akn
```
