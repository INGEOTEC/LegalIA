# `md2akn` rules

The specification of what `md2akn` does to an instrument, as one layered list. It replaces a log: the
parser had grown 47 numbered rules in the order the problems were found, and some of them corrected
others (44 corrects 23, 42 narrows 29). They were renumbered in issue #304, with the output held fixed
(the tree and the units of every snapshot of the four corpora and of every fixture are the same before
and after, by digest). The table at the end maps every old number to its new one; the changelog in
`README.md`, the dated *Measured* sections of `scripts/embeddings/README.md` and `docs/HISTORY.md` keep
the old numbers on purpose, because they describe what was true when they were written.

**The design in one sentence: the structure the SCJN states is the structure; the rules come after,
inside it.** The SCJN's API serves an instrument as ordered *blocks*, and `scjn` writes one marker line
before each (layer **W**). `md2akn.skeleton` reads those markers first and settles what each API block
*is* (layer **S**); `md2akn.structure` then places the Markdown blocks in a tree and runs the text rules
(layer **B**) *inside* the block it is reading; `md2akn.units` turns the tree into retrieval units
(layer **U**). A document with no markers is one implicit block: layer S has nothing to say and layer B
runs unbounded, exactly as it always did.

Conventions. *Marked document*: one whose Markdown carries marker lines. A rule of layer S and every
rule marked "marked only" applies to marked documents alone, so an unmarked one is read, and its eIds
written, as before (`tests/fixtures_units/marker_free_digests.json` pins it). No rule names an
instrument, a collection or an eId. Counters: where a rule takes effect the code calls
`md2akn.hits.hit(rule, branch)`; `scripts/embeddings/measure_trees.py --all-snapshots --hits` sums them
over every snapshot, which is how a branch that can no longer fire was found and removed.

## W — the writer's contract (what a marker promises)

Written by `scjn.markdown`/`scjn.outline`, verified by `scripts/verify_scjn_outline.py` and pinned by the
shared fixture `tests/fixtures_units/marker_vocabulary.json` (issue #303). `md2akn` imports nothing from
`scjn`; the fixture is the bridge.

- **W1.** Every API block that yields text is preceded by exactly one marker line,
  `<!-- scjn:block orden=N numero=N kind=K [type=T] [num="N"] label="L" -->`, carrying the API's own
  `orden`, `numero` and `referencia` (`label`), and `kind`/`type`/`num` derived from the label alone.
  `md2akn.outline.lee_marcador` reads it back; `test_outline_markers.py` parses every example of the
  fixture to the attributes `scjn.outline.parse_marker` gives.
- **W2.** A marker is metadata, not text: it is never embedded, `text_units` strips it as it strips a
  reform annotation, and `coverage()` counts its characters as annotation. (`MARCADORES_EN_TEXTO`.)
- **W3.** A block that yields no text (an editorial `N. DE E.` aside, an empty block) has no marker, so
  its `orden` is a gap that the raw API answer explains; a document with no marker at all is one
  implicit block. (Verified by `verify_scjn_outline.py`, "marker completeness".)

## S — skeleton rules (outline → nodes)

Read by `md2akn.skeleton.Skeleton` before any block is placed, or applied by `md2akn.structure` the
moment an API block's marker is met. **Marked documents only.** An API block is the unit of ownership
(invariant **I1**, checked independently by `md2akn.validate._block_escapes`): everything up to the next
marker hangs from the node that block opened.

- **S1. The marker says what the block opens.** The class of an API block's heading is what its marker
  says (a container of that `type` and `num`, an article of that number, transitorios, an index as a `toc`),
  never what the text alone would suggest; for a container, article, transitorios or decree the heading is
  the first block that reads as one (`Skeleton._encabezado_de`), and what stands between the marker and it
  is the heading's own annotation or heading. An article's number is what its text prints when it equals or
  extends the label, the label when it extends the text, the text when it is the same number in another
  numeral (`ARTÍCULO I` under the label `ARTÍCULO 1`), else the one consistent with the API's own `numero`,
  else the label (`_numero_de_articulo`). *Named deviations* (`skeleton.DEVIATIONS`, reported by
  `validate.block_conformance` as `documented-*`): `documented-number-by-numero` (`es_numero_por_numero`),
  `documented-number-label-repeated` (`es_etiqueta_repetida`: the label's identifier is taken, so the
  node keeps the text's number) and `documented-article-served-as-chapter`
  (`es_articulo_servido_como_capitulo`: the LIGIE labels its tariff chapters `ARTÍCULO 83`). Pinned by
  `test_marker_conformance.py`, `test_outline_markers.py`, `test_skeleton.py`.
- **S2. A single-text block opens no structure.** Inside an API block the SCJN labels `article`,
  `provision`, `header`, `preamble` or `recitals`, no line opens a container, an article or transitorios:
  an amendment article quoting `ARTÍCULO 5.-`, an IMDG `1.1.2 Convenios` listing `Capítulo` lines, is that
  block's own text. (`BLOQUES_DE_UN_SOLO_TEXTO`, `APERTURAS_DE_ESTRUCTURA`.) Pinned by
  `test_marker_conformance.py`, `test_outline_markers.py`.
- **S3. A reform decree holds what follows it.** A block the API marks `decree` (`D.O.F.` plus a date)
  opens one section holding everything after it until the next marker: its own transitorios, an `ANEXO
  ÚNICO` reprinting the instrument. Pinned by `test_outline_markers.py`, `test_marker_conformance.py`.
- **S4. An API block is the root of all its text.** What a block's text holds hangs from the node the block
  opened, whatever transitorios section is open around it. A heading the text of a block prints nests *below*
  that block's root, keeping its order among its siblings (an `ANEXO` holds the chapters printed after it, a
  chapter its parts, a bare `APÉNDICE` after an annex of the same block is inside it, a bare `ANEXO` right
  after an annex with only its title is inside that one) and the next marker closes everything the block
  opened. Every `RESOLUCIÓN ...` block is a `section`. The lines between a marker and the heading it opens a
  container with are that heading's. The one annex that leaves its block is the one with no container or
  article after its heading in the block's text (the code a resolution amends). **The interior bound:** no
  text rule may open a container above the block's root rank, and no node may end after the block does.
  Pinned by `test_block_hierarchy.py`, `test_block_root.py`, `test_skeleton.py`.
- **S5. An identifier is never silently reused.** A second claimant of an eId takes a `_2` suffix and
  `AknNode.repeated` is true on **both**; a number that restarts inside another container is a different
  identifier and no repeat. (`EIdAllocator`, `_nuevo_nodo`.) Applies to unmarked documents too. Pinned by
  `test_repeated_numbers.py`, `test_other_blocks.py`.
- **S6. Implicit sections.** An API block labelled `other` (or a second `preamble`) that holds articles in
  its own text or is followed by article blocks holds them (`Skeleton.aperturas`), until the next marker that
  is not an article. An API `article` block whose text is only a heading and a caption, followed by `provision`
  blocks, opens a section of those provisions, when the document does it three times or more (named deviation
  `documented-article-heading`, `es_articulo_de_encabezado`). Pinned by `test_other_blocks.py`,
  `test_marker_conformance.py`.
- **S7. Annexes and appendices.** An annex the API numbers after a chapter (`numero`, or the `N-` prefix of its
  `num`) is a child of the innermost open container carrying that number (`cap_2__att_2-A`), a quarter of a
  rank below it; an appendix is an `attachment` with `refers_to="#apendice"` and the eId prefix `app`, nested
  in the annex that is open or else in the instrument; a bare `**APÉNDICE**` directly under the unnumbered
  appendix it repeats is that appendix's running page header; a section a block opens while an annex or
  appendix is open hangs from the body. Pinned by `test_annexes_appendices.py`.
- **S8. An `other` block is a labelled container.** Every block the API labels `other` (and a preamble,
  recitals or later title block) is an `hcontainer` whose `num` is its label and whose eId derives from it —
  never a `section` because articles follow it. An explicit role table (`md2akn.outline.ROLES_DE_ETIQUETA`)
  gives some a `refers_to` (`#conclusions`, `#note`, the steps of the Mexican promulgation decree kept
  together under one `#promulgation`); a block that merely starts with such a word has no role. When the
  block opens with a line that is only its own label (compared without case, accents, bold, `:` or `.`, a
  leading numeral allowed) that line is the node's **heading**, never an `intro` unit. Pinned by
  `test_other_blocks.py`, `test_marker_conformance.py`.
- **S9. A numbered provision block is an article.** A block the API labels `provision` (`PRIMERO`, `1.1.0`)
  is an article, and an `other` block labelled `N (SIC)` whose text opens with that number is the article.
  Pinned by `test_outline_markers.py`, `test_marker_conformance.py`.
- **S10. A block the API split in two is one container.** `ARTÍCULOS` and `CONTENIDO ARTICULOS`, or a title
  and a bare `CONTENIDO` that follows a block holding only its own title (`ANEXO` / `CONTENIDO`), are one
  container: the second opens nothing. A `CONTENIDO` after a block with text of its own is read by the other
  rules. Pinned by `test_bare_contenido.py`.
- **S11. Preamble and conclusions are the blocks the API says.** The `preamble` is exactly the title block
  (`kind=header`) and `conclusions` never cross an API block: a dated closing line followed by another API
  block leaves no `conclusions` node. (Named deviation `documented-closing-signature`,
  `es_firma_de_cierre`.) Pinned by `test_decree_position.py`, `test_marker_conformance.py`.
- **S12. A part is ranked from the next marker.** A part the SCJN labels whose very next marker opens a book,
  a title or a chapter holds it and ranks just above that type; a part is a sibling of the part before it;
  any other part keeps the document-wide reading (B5). Pinned by `test_marker_conformance.py`.
- **S13. An annotation belongs to the block it was read in.** An annotation read after the last text of an API
  block and before the next marker is attached to that block's last node, never to the node the next block
  opens. Pinned by `test_marker_conformance.py`.
- **S14. A table of contents is a `toc` node.** A block the API labels `ÍNDICE` is one; in a document with no
  marker, a run of short blocks after an index marker (`Índice`, `Contenido`) that the document then repeats
  as its own headings. An index belongs to the API block it starts in and ends at the next marker. It is a
  leaf, never containers or articles, and yields `toc` units. Applies to unmarked documents (the text form).
  Pinned by `test_repeated_numbers.py`, `test_block_root.py`.

## B — block-interior rules (text → nodes, inside one block)

Run by `md2akn.structure._Constructor` over the Markdown blocks of the API block being read (the whole
document when it has no markers).

- **B1. An article number is read whole.** `Artículo 1o.-A BIS`, `3o.- Bis`, `228 a`, `Artículo 5 Bis 1`,
  the roman `ARTÍCULO IV`, the decimal `Artículo 1.1:` (`art_1.1`), a trailing capital that closes the line
  (`Artículo 11 A`): each is its own number. A heading line that is only `Art. 8` straight before `**ARTICULO 8**`
  is a running head, not an article. Unconditional (`patterns.NUM_ARTICULO`, `NUMERO_ROMANO`,
  `NUMERO_DECIMAL_ARTICULO`). Pinned by `test_roman_articles.py`, `test_repeated_numbers.py`,
  `test_tables_intros.py`, `test_bare_contenido.py`.
- **B2. The grammar of a radio-regulations text, and a few mixed shapes.** `S5.43`/`5.43A` is a provision (an
  article), `ARTÍCULO S5` the container of its provisions, `APÉNDICE S30` an appendix, `Artículo Primero.-`
  with a mixed-case ordinal inside transitorios a transitory article, and the caption line the SCJN prints
  before an article's heading (`Objeto`) the start of that article. Marked only. Pinned by
  `test_other_blocks.py`.
- **B3. How an instrument with no `Artículo N` numbers its provisions.** Decided once per document
  (`classify.modo_sin_articulos`): bold ordinals (`**PRIMERO.-**`, three or more), or failing those decimal
  numerals (`1.`, `2.1`, five or more), open articles in its body too. Gated on the *document* — any `ARTICULO`
  outside transitorios turns it off — so no law and no article-numbered reglamento can change. Pinned by
  `test_units.py`, `test_roman_articles.py`, `test_tables_intros.py`.
- **B4. Recitals are not provisions; a restarted numeral is a list item.** Under `CONSIDERANDO` (the API block
  of that kind, or its text in an unmarked document) numbered paragraphs stay in the preamble in an instrument
  numbered the same way; they end at a container, at the heading that opens what is ordered, at
  transitorios or when their numbering starts over. In numeral mode a whole numeral that starts over (`1.`
  after `3.`), or carries on a list that did, is a list item of the provision above; a dotted numeral always
  opens one. A `D.O.F.`-headed decree quoted in the text opens its own transitorios when an ordinal provision
  follows. Pinned by `test_repeated_numbers.py`, `test_units.py`.
- **B5. `PARTE`, `ANEXO`, `APÉNDICE` and the other containers by text.** `PARTE` is a container above books,
  titles and chapters (below a chapter when the document opens one of those first), `ANEXO`/`APÉNDICE` an
  `attachment` told apart by `refers_to`; containers nest by precedence rank; a container's *epigraph* — the
  short block after its heading — is its heading text. Pinned by `test_structure.py`,
  `test_annexes_appendices.py`.
- **B6. A container's introduction is its `intro`.** Every block between a container's heading (epigraph
  included) and its first child container or article is one `intro` node (`<container>__intro`) and yields
  `intro` units; the text a container holds after its index or after a captioned table is a further `intro`.
  Text before any container keeps its `preamble`/`loose` treatment. Pinned by `test_tables_intros.py`,
  `test_block_root.py`.
- **B7. A table is a node.** Consecutive blocks that open and close with `|` are one `table` node
  (`<parent>__table_N`), with one lone `**(ARANCEL)**` label tolerated between two rows; the one or two bold-led
  caption lines before its rows are its first lines (its `heading` is the first one) and the plain paragraphs
  right after them its notes, up to the next caption. A table outside an article yields `table` units cut at
  row boundaries; one inside an article stays article text. Pinned by `test_tables_intros.py`,
  `test_block_root.py`.
- **B8. A reference is not an opener.** `**Artículo 8o. **fracción II primer párrafo: $179,017.26`, a line that
  names an article and goes on in lower case, is a reference, unless the API says the block is an article.
  Marked only. Pinned by `test_repeated_numbers.py`, `test_block_root.py`.
- **B9. A numbering that restarts inside a block opens a division.** An `hcontainer` (`refers_to="#division"`,
  `#form` for the `MODELO DE CERTIFICADO ...` of an appendix and the `SE CERTIFICA` placeholder of certificates
  that print no title) opens at the nearest heading before the restart, so an identifier is the same article or
  it is not repeated; with no heading there is no division and the repeat stays. The boxes of a form are not
  articles; a numeral before a lower-case word is a value, not an opener; a numeral equal to the number of the
  article just above is that article's paragraph; an `ANEXO 1` a block's text prints under the appendix before
  it is that appendix's. Marked only. Pinned by `test_other_blocks.py`.

## U — unit rules (`text_units`)

The retrieval units (issue #218). `cap` is 2,000 normalized characters.

- **U1. Metadata is never embedded.** Frontmatter, reform annotations and marker lines are not text of a unit.
- **U2. An article is one unit, whole, or splits at its direct children.** An article no longer than `cap`
  (normalized) is one unit; a longer one splits into the chapeau alone and one piece per remaining child, each
  prefixed with the chapeau. With `split_articles=False` every article is one unit whatever its length and the
  split never applies.
- **U3. Headings, loose text, preamble and conclusions.** A container's epigraph is its own `heading` unit;
  consecutive leaf children of a non-article node are grouped into one `loose` unit each up to `cap`;
  `preamble` and `conclusions` are one unit each. A node with `intro`, `table` or `toc` children yields the
  units B6, B7 and S14 name.
- **U4. Context.** `heading`/`loose` units always carry their ancestor `path` in the embedded text; whether an
  article carries its own number and the law's name is the `template`'s call (`"bare"` or `"contextual"`).
- **U5. No unit exceeds `cap`.** One still over it after U2–U3 is cut again at paragraph boundaries into
  consecutive pieces; a single paragraph longer than `cap` is left whole and counted by `max_unit_chars`.
  `split_over_cap=False` turns it off. Never applied to an article when `split_articles=False`.
- **U6. A childless container is a `heading` unit.** An annex, a part, a chapter whose text is only its heading
  line is a `heading` unit, never `loose`.
- **U7. The envelope.** A unit is `envelope` when it starts inside the title block, the promulgation decree's
  steps or its closing certification — read from the markers and their position, never from an instrument
  (`md2akn.outline.bloques_de_decreto`, `rangos_de_envoltura`): a run of decree blocks right after the title
  block or at the end of the document, made of blocks labelled as a step of the decree and of the blocks it
  interleaves between two steps. A decree-step label inside the instrument's own text is not envelope. An
  instrument that is only its envelope marks none. An envelope unit is neither a source nor a candidate of the
  Atlas matrix. Pinned by `test_envelope.py`, `test_decree_position.py`.

## Invariants

- **I1.** Ownership: no leaf reaches into the next API block (`validate._block_escapes`).
- **I3.** No repeated number: no node has `repeated` (checked by the release sweep).
- **I4.** Every numbering gap is classified in `tests/fixtures_units/genuine_gaps.json`, and every entry there
  is a gap.
- **I5.** Conformance: for every API block the node it opened is of the type, number and rank its marker says
  and takes the block's own label line as its heading (`validate.block_conformance`); the departures the
  parser takes on purpose are the named deviations listed under S1, S6 and S11
  (`validate.CLASES_DOCUMENTADAS`, 57 blocks in `tests/fixtures_units/marker_documented.json`).

## Old → new

Every number of the old list of 47, in the order they were found. "Merged" means the statement is now one
clause of the new rule; nothing was dropped.

| old | new | what it was |
|---|---|---|
| 1 | U1 | frontmatter and annotations are metadata |
| 2 | U2 | an article within `cap` is one unit |
| 3 | U2 | a longer article splits at its children |
| 4 | U3 | a container's epigraph is a `heading` unit |
| 5 | U3 | consecutive leaves group into `loose` units |
| 6 | U3 | `preamble` and `conclusions` are one unit each |
| 7 | U4 | headings and loose text carry their `path` |
| 8 | B3 | articleless instruments: ordinals or numerals |
| 9 | U5 | no unit over `cap` |
| 10 | B1 | roman-numbered articles |
| 11 | B1 | decimal article numbers |
| 12 | B6 | a container's `intro` |
| 13 | B7 | tables are nodes |
| 14 | B1 | the article number is read whole |
| 15 | S14 | a table of contents is a `toc` |
| 16 | B5 | `PARTE`, `ANEXO`, `APÉNDICE` |
| 17 | B4 | recitals are not provisions |
| 18 | B4 | a restarted numeral is a list item |
| 19 | S1 | the markers decide the class of a block's heading |
| 20 | S3 | a reform decree holds what follows it |
| 21 | S4 | a container found in a block's text nests below it |
| 22 | S5 | identifiers are never silently reused |
| 23 | S6 | an `other` block holding articles opens an implicit section (corrected by 44) |
| 24 | S2 | a single-text block opens no structure |
| 25 | S7 | an annex numbered after a chapter is its child |
| 26 | S7 | an appendix is `#apendice`, prefix `app` |
| 27 | S7 | a bare `APÉNDICE` header is no appendix |
| 28 | S7 | a section opened under an attachment hangs from the body |
| 29 | S8 | every `other` block is an `hcontainer`, with roles (narrowed by 42) |
| 30 | S10 | a title and its `CONTENIDO <title>` block are one container |
| 31 | B9 | a restart inside a block opens a division |
| 32 | B2 | radio-regulations grammar, mixed-case ordinals, captions |
| 33 | S4 | headings in a block keep their order; `RESOLUCIÓN` is a `section` |
| 34 | S4 | an API block is the root of all its text |
| 35 | U6 | a childless container is a `heading` unit |
| 36 | B7 | a table's caption and notes belong to it |
| 37 | B8 | `Artículo 8o. fracción ...` is a reference |
| 38 | S6 | a heading-only `article` block opens a section of provisions |
| 39 | S9 | `98 (SIC)` is an article |
| 40 | S10 | a bare `CONTENIDO` after a title-only block |
| 41 | B1 | a running head `Art. 8`; a trailing capital in the number |
| 42 | S11 | the preamble is the title block, conclusions never cross a block |
| 43 | U7 | the envelope is read by position |
| 44 | S8 | the outline governs first: label line is the heading, no `section` from text |
| 45 | S12 | a part is ranked from the next marker |
| 46 | S13 | an annotation belongs to its block |
| 47 | S2 | a `provision` block is one text |

W1–W3 have no old number: they state, for the first time as rules, what the writer promises (issue #303).

## Open findings

Defects found while restructuring, recorded and **not** fixed (the output of the parser is held fixed by
issue #304; a fix would move vectors, the Atlas and the instrument files). None so far beyond the three
`incomplete` instruments the repository owner decided to leave (`leyes/lft`, `reglamentos/81587`,
`reglamentos/67602`), which are a fact about the SCJN's API, not about the parser.
