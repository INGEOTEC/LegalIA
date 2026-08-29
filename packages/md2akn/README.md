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
XML. It never does — that conversion already exists, in
[`nota2md.akoma_ntoso`](../nota2md). What is taken from the standard (an OASIS
Standard since 2018, reviewed in
[issue #91](https://github.com/INGEOTEC/LegalIA/issues/91)) is its
**vocabulary**: the element names a node's `akn_type` is drawn from, the `eId`
naming convention, and `refersTo` for the structures the standard has no
element for.

Not emitting XML is what lets the tree stay faithful to the source. Akoma
Ntoso's `hierarchy` content model is a strict either/or — an element has a
flat `content` **or** hierarchical children, never both — and a Mexican
article routinely has both: an introductory sentence ("Artículo 4. Son
obligaciones:"), then its fracciones, then one or more closing paragraphs. The
XML converter has to choose and flatten; this tree does not.

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
| a plain paragraph | `content` | |
| the closing signatures | `conclusions` | |

Akoma Ntoso has no element for "transitorios" or for "apartado"; both are
expressed as an existing element plus `refers_to`, which is the same
convention `nota2md.akoma_ntoso` already adopted — deliberately, so the two
packages say the same thing even though they share no code.

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
