:mod:`md2akn`
==============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/md2akn.svg
        :target: https://badge.fury.io/py/md2akn

Version |md2akn_version| — see :doc:`index` for the full package table.

:py:mod:`md2akn` segments a Mexican federal law's Markdown — the text
:py:mod:`nota2md` produces — into a navigable hierarchy: articles inside
their chapter, fracciones inside their article, incisos inside their
fracción. It reads Markdown and depends on nothing else in this monorepo,
which is why it is documented last.

**No Akoma Ntoso XML is produced anywhere in this project.** An earlier
converter that lived in ``nota2md`` was removed in issue #168. What this
package borrows from the standard (OASIS LegalDocML, an OASIS Standard since
2018, reviewed in issue #91) is its *vocabulary* only: the element names in
:py:data:`~md2akn.AKN_TYPES`, the ``eId`` convention, and ``refers_to`` for
the two Mexican structures — "transitorios", "apartado" — the standard has
no element for.

:py:func:`~md2akn.parse_legal_provisions` is the whole package in one call:
hand it a Markdown file and get back the root :py:class:`~md2akn.AknNode`,
walked in document order with :py:meth:`~md2akn.AknNode.walk` and addressed
by identifier with :py:meth:`~md2akn.AknNode.find` (``tree.find("art_27__para_VII")``
is "article 27, fracción VII").

The sections below follow the segmentation itself, not alphabetical order:
:py:mod:`md2akn.pipeline` (the spaCy layer and the two public entry points),
:py:mod:`md2akn.segmenter` (frontmatter and the block scan under it),
:py:mod:`md2akn.structure` (containers and articles, issue #159),
:py:mod:`md2akn.lists` (fracciones/incisos/subincisos inside an article,
issue #160), :py:mod:`md2akn.patterns` (the line patterns everything above is
built from), :py:mod:`md2akn.annotations` (reform notes as structured
metadata, issue #161), :py:mod:`md2akn.model` (the tree and its vocabulary),
:py:mod:`md2akn.validate` (structural invariants, issue #162). Every class
and function is documented, including private/internal helpers
(leading-underscore names) — useful when extending or debugging the package,
though they are not part of its public API and can change without notice.

Every example on this page is **offline**: no network call, and no fixture
read off a corpus already on disk (unlike :doc:`dofjson_api` and
:doc:`nota2md_api`, which need SIDOF/the ``scjn-leyes`` release). One small
law-shaped Markdown snippet, built once below and reused across every
section, is enough to exercise a container (``TÍTULO``/``CAPÍTULO``), two
articles — one with fracciones and incisos, one a single plain paragraph —
a reform annotation, and a ``Transitorios`` section:

``md2akn.pipeline`` — the spaCy layer and the entry points
------------------------------------------------------------------

:py:mod:`md2akn.pipeline` is where the ``akn_segmenter`` spaCy pipeline
component actually lives (registered as soon as :py:mod:`md2akn` is
imported), along with the ``Doc``/``Span`` extensions the tree is reachable
through and the two public entry points. It also settles three cost
decisions once rather than per call: a bare ``spacy.blank("es")`` (a
tokenizer, no tagger/parser a rule-based segmenter never consults),
``nlp.max_length`` raised past the corpus' largest law (``ligie-2022``,
1.89 MB), and a scan that tokenizes once and never iterates tokens
afterwards (see :py:mod:`md2akn.segmenter` below).

>>> import tempfile
>>> from pathlib import Path
>>> import md2akn
>>>
>>> FIXTURE = (
...     "---\n"
...     "fuente: scjn\n"
...     "ordenamiento: LEY DE PRUEBA\n"
...     "---\n"
...     "\n"
...     "**TITULO PRIMERO**\n"
...     "\n"
...     "Disposiciones Generales\n"
...     "\n"
...     "**CAPITULO I**\n"
...     "\n"
...     "Del Objeto\n"
...     "\n"
...     "**(REFORMADO PRIMER PARRAFO, D.O.F. 10 DE JUNIO DE 2011)**\n"
...     "\n"
...     "**ARTICULO 1o.-** Son obligaciones de los patrones:\n"
...     "\n"
...     "I.- Cumplir las disposiciones de las normas de trabajo;\n"
...     "\n"
...     "II.- Pagar a los trabajadores los salarios e indemnizaciones, "
...     "conforme a lo siguiente:\n"
...     "\n"
...     "a) El salario se pagará en el lugar de trabajo;\n"
...     "\n"
...     "b) El pago se hará en moneda de curso legal;\n"
...     "\n"
...     "**ARTICULO 2o.-** Un artículo de un solo párrafo, sin lista alguna.\n"
...     "\n"
...     "## Transitorios\n"
...     "\n"
...     "**PRIMERO.-** La presente Ley entrará en vigor al día siguiente.\n"
... )

:py:func:`~md2akn.parse_markdown` is :py:func:`~md2akn.parse_legal_provisions`
for text already in memory — both return the same tree, the root
:py:class:`~md2akn.AknNode` (``akn_type == "act"``):

>>> tree = md2akn.parse_markdown(FIXTURE)
>>> tree
AknNode(akn_type='act', eId='act', children=1)
>>> tree.meta
{'fuente': 'scjn', 'ordenamiento': 'LEY DE PRUEBA'}

>>> outdir = Path(tempfile.mkdtemp())
>>> path = outdir / "ley-de-prueba.md"
>>> _ = path.write_text(FIXTURE, encoding="utf-8")
>>> desde_archivo = md2akn.parse_legal_provisions(path)
>>> desde_archivo
AknNode(akn_type='act', eId='act', children=1)
>>> len(list(desde_archivo.walk())) == len(list(tree.walk()))
True

The segmenter is a pipeline component, so it composes with anything else a
caller wants to run over the same ``Doc`` instead of going through the
shortcut above:

>>> import spacy
>>> nlp = spacy.blank("es")
>>> _ = nlp.add_pipe("akn_segmenter")
>>> doc = nlp("**ARTICULO 1o.-** Son obligaciones de los patrones.\n")
>>> doc._.akn_tree                 # the root AknNode
AknNode(akn_type='act', eId='act', children=1)
>>> doc._.akn_meta                 # the frontmatter (empty: this note has none)
{}
>>> len(doc.spans["akn"])          # every node's Span, in document order
4

.. automodule:: md2akn.pipeline
   :members:
   :private-members:
   :undoc-members:

``md2akn.segmenter`` — frontmatter and the block scan
------------------------------------------------------------

The scan is over **lines of the raw text, never over tokens**: what makes a
line an article heading is how it begins, not what any word in it is, so
recognition is a single O(lines) pass and spaCy's tokenizer runs exactly
once per document. :py:func:`~md2akn.segmenter.split_frontmatter` peels the
YAML-like block off the front and reports where the body actually starts:

>>> from md2akn.segmenter import split_frontmatter
>>> meta, offset = split_frontmatter(FIXTURE)
>>> meta
{'fuente': 'scjn', 'ordenamiento': 'LEY DE PRUEBA'}
>>> FIXTURE[offset:offset + 19]
'\n**TITULO PRIMERO**'

:py:func:`~md2akn.segmenter.segment` (the real entry point ``parse_markdown``
calls into) lazily imports :py:func:`md2akn.structure.build` to avoid a
circular import — :py:mod:`md2akn.structure` in turn imports this module's
:py:class:`~md2akn.segmenter.Block`. ``build_tree`` in this module is dead
code kept undocumented nowhere else in this repo: grepping the package finds
no caller: :py:func:`~md2akn.structure.build` is what actually builds the
tree, out of the blocks this module scans.

.. automodule:: md2akn.segmenter
   :members:
   :private-members:
   :undoc-members:

``md2akn.structure`` — containers and articles (issue #159)
--------------------------------------------------------------------

The shape a law comes out in: ``act`` → ``preamble`` (a sibling of ``body``,
since Akoma Ntoso's ``body`` admits only hierarchical elements) and ``body``
→ ``book``/``title``/``chapter``/``section`` (nesting by precedence rank,
never by an indentation the text does not have) → ``article``, plus
``level``/``refers_to="#apartado"`` for APARTADO A/B of article 123 and
``section``/``refers_to="#transitorios"`` for one section per decree that
added some. Our fixture's ``TÍTULO PRIMERO`` → ``CAPÍTULO I`` → two
articles, plus its own ``Transitorios`` section, already walked the whole
shape above:

>>> [node.akn_type for node in tree.walk()]
['act', 'body', 'title', 'chapter', 'article', 'content', 'paragraph', 'paragraph', 'point', 'point', 'article', 'content', 'section', 'article', 'content']

A container whose numbering restarts never collides, because the eId is a
path scoped under its parent rather than a bare number:

>>> tree.find("tit_PRIMERO__cap_I").heading
'Del Objeto'

.. automodule:: md2akn.structure
   :members:
   :private-members:
   :undoc-members:

``md2akn.lists`` — fracciones, incisos, subincisos (issue #160)
--------------------------------------------------------------------------

Inside an article: a plain paragraph is ``content``, a fracción (``**I.**``)
is ``paragraph``, an inciso (``**a)**``) is ``point``, a subinciso
(``**1.**``) is ``subpoint``. The markers cannot be told apart out of
context — ``V.``/``X.``/``L.``/``C.``/``D.``/``M.`` are all valid Roman
numerals *and* valid capital letters — so what resolves it is
**consecutiveness**: a list can only be opened by the first element of its
series and continued by a label that comes after the previous one, tolerant
of repealed fracciones (numbering jumps) and Latin suffixes (``VII Bis``,
``VII Ter``) without either being an error. Our fixture's ``Artículo 1o.``
has both a chapeau and two fracciones, the second with two incisos:

>>> art1 = tree.find("tit_PRIMERO__cap_I__art_1o")
>>> [child.akn_type for child in art1.children]
['content', 'paragraph', 'paragraph']
>>> frac_ii = tree.find("tit_PRIMERO__cap_I__art_1o__para_II")
>>> [point.num for point in frac_ii.children]
['a', 'b']

.. automodule:: md2akn.lists
   :members:
   :private-members:
   :undoc-members:

``md2akn.patterns`` — the line patterns (issue #158–#161)
--------------------------------------------------------------------

Every pattern here was written against counts measured over the 315 laws of
the SCJN corpus, not against a guess at what a law looks like — where a
count is quoted in a comment, that measurement is the reason the pattern is
shaped the way it is. ``ARTICULO`` is the one everything else in the package
ultimately keys off of:

>>> from md2akn.patterns import ARTICULO
>>> ARTICULO.match("**ARTICULO 1o.-** Son obligaciones de los patrones.").group("num")
'1o'

.. automodule:: md2akn.patterns
   :members:
   :undoc-members:

``md2akn.annotations`` — reform notes as structured metadata (issue #161)
-------------------------------------------------------------------------------

The SCJN's consolidated texts interleave, with the law itself, notes saying
which reform touched which part — ``**(REFORMADO PRIMER PÁRRAFO, D.O.F. 10
DE JUNIO DE 2011)**``. In Akoma Ntoso terms these are the *passive* side of
a modification (``lifecycle``/``temporalData``/``passiveModifications``,
issue #91); no XML is emitted, only the same information at a granularity
fine enough to emit it later. An annotation whose action cannot be
recognized is still recorded, with ``action=None``, rather than dropped:

>>> from md2akn.annotations import parse_annotation, es_anotacion
>>> es_anotacion("REFORMADO PRIMER PARRAFO, D.O.F. 10 DE JUNIO DE 2011")
True
>>> es_anotacion("VEASE TABLA ANEXA")
False
>>> parse_annotation("**(REFORMADO PRIMER PARRAFO, D.O.F. 10 DE JUNIO DE 2011)**")
Annotation(raw='**(REFORMADO PRIMER PARRAFO, D.O.F. 10 DE JUNIO DE 2011)**', action='REFORMADO', scope='PRIMER PARRAFO', date=datetime.date(2011, 6, 10), source=None)

The same annotation, attached to the node it actually describes, is already
on our fixture's first article:

>>> art1.notes
[Annotation(raw='**(REFORMADO PRIMER PARRAFO, D.O.F. 10 DE JUNIO DE 2011)**', action='REFORMADO', scope='PRIMER PARRAFO', date=datetime.date(2011, 6, 10), source=None)]

.. automodule:: md2akn.annotations
   :members:
   :private-members:
   :undoc-members:

``md2akn.model`` — the tree and its vocabulary
------------------------------------------------------

:py:class:`~md2akn.AknNode` and :py:class:`~md2akn.Annotation`, and the
vocabulary their ``akn_type`` is drawn from — 14 Akoma Ntoso element names,
in the rough order they nest:

>>> from md2akn import AKN_TYPES, REFERS_TO_APARTADO, REFERS_TO_TRANSITORIOS
>>> len(AKN_TYPES)
14
>>> "article" in AKN_TYPES and "paragraph" in AKN_TYPES and "point" in AKN_TYPES
True
>>> REFERS_TO_APARTADO
'#apartado'
>>> tree.find("sec_transitorios").refers_to == REFERS_TO_TRANSITORIOS
True

:py:meth:`~md2akn.AknNode.walk` is preorder — document order, since children
are appended in document order — and :py:meth:`~md2akn.AknNode.find`
addresses any node below (and including) the one it is called on by eId:

>>> tree.find("tit_PRIMERO__cap_I__art_2o").find("tit_PRIMERO__cap_I__art_2o__p_1").text
'**ARTICULO 2o.-** Un artículo de un solo párrafo, sin lista alguna.'

``is_chapeau``/``is_tail`` are the flags that let a Mexican article keep
both a flat introduction and hierarchical children at once — a shape Akoma
Ntoso's own ``hierarchy`` content model forbids (``content`` **or**
children, never both) and that this package does not have to choose between
precisely because it emits no XML:

>>> chapeau = art1.children[0]
>>> chapeau.is_chapeau
True
>>> chapeau.text
'**ARTICULO 1o.-** Son obligaciones de los patrones:'

.. automodule:: md2akn.model
   :members:
   :private-members:
   :undoc-members:

``md2akn.validate`` — structural invariants (issue #162)
------------------------------------------------------------------

:py:func:`~md2akn.validate` is a public function and not merely a test:
whoever hands this package a document of their own has no other way to ask
whether the result is well-formed, and the mass sweep over the SCJN corpus
reuses it as an instrument rather than a pass/fail check. Nothing in this
module knows what a Mexican law looks like — only properties of *any* tree,
such as which ``akn_type`` may nest inside which:

>>> report = md2akn.validate(tree)
>>> print(report)
<Report valid, 100.00% covered>
>>> report.ok
True

A :py:class:`~md2akn.Violation` names the rule, the offending eId, and the
detail; a :py:class:`~md2akn.Report` collects them alongside the coverage
they leave behind:

>>> from md2akn import Report, Violation
>>> violation = Violation(rule="nesting", eId="art_1", detail="chapter inside article")
>>> violation
Violation(rule='nesting', eId='art_1', detail='chapter inside article')
>>> broken = Report(violations=[violation], cubiertos=9, totales=10)
>>> print(broken)
<Report 1 violation(s), 90.00% covered>
>>> broken.ok
False

.. automodule:: md2akn.validate
   :members:
   :private-members:
   :undoc-members:

``md2akn.units`` — every text of a law, ready to embed (issue #218)
------------------------------------------------------------------------

:py:func:`~md2akn.text_units` turns a parsed tree into the flat list of
texts a vector-per-law build actually embeds — one per article (or one per
*piece*, past a length cap), one per container epigraph, loose content
grouped up to the same cap, and one each for the preamble and the closing
signatures. It extends :py:func:`~md2akn.validate`'s article-only view
rather than replacing it: the container epigraphs, the transitorios, the
enacting formula and the closing rubric are real legal text too, and
:py:func:`~md2akn.coverage` turns "the whole law is represented" into
numbers a caller can check. Two things are deliberately never embedded —
the frontmatter on ``tree.meta`` and a reform :py:class:`~md2akn.Annotation`
— and both are accounted for separately rather than silently dropped.

Our fixture's ``Artículo 1o.`` carries a reform annotation right before it;
it never reaches any unit's text, but the coverage figures below still
account for its characters:

>>> units = md2akn.text_units(FIXTURE)
>>> [(u.unit_type, u.eId) for u in units]
[('heading', 'tit_PRIMERO'), ('heading', 'tit_PRIMERO__cap_I'), ('article', 'tit_PRIMERO__cap_I__art_1o'), ('article', 'tit_PRIMERO__cap_I__art_2o'), ('heading', 'sec_transitorios'), ('article', 'sec_transitorios__art_PRIMERO')]
>>> art1 = next(u for u in units if u.eId == "tit_PRIMERO__cap_I__art_1o")
>>> "REFORMADO" in art1.text
False
>>> art1.text
'**ARTICULO 1o.-** Son obligaciones de los patrones: I.- Cumplir las disposiciones de las normas de trabajo; II.- Pagar a los trabajadores los salarios e indemnizaciones, conforme a lo siguiente: a) El salario se pagará en el lugar de trabajo; b) El pago se hará en moneda de curso legal;'

A container epigraph is its own ``heading`` unit, and it — like a ``loose``
unit — always carries its ancestor ``path`` (rule 7): the containers it sits
inside, outermost first, prefixed with the law's own name when the
frontmatter has one (this fixture's does not):

>>> cap = next(u for u in units if u.eId == "tit_PRIMERO__cap_I")
>>> cap.path
('TÍTULO PRIMERO Disposiciones Generales',)
>>> cap.text
'TÍTULO PRIMERO Disposiciones Generales **CAPITULO I** Del Objeto'

:py:func:`~md2akn.coverage` is the invariant as data: every non-whitespace
character is frontmatter, an annotation, or embedded in some unit — never
none of the three:

>>> tree = md2akn.parse_markdown(FIXTURE)
>>> cov = md2akn.coverage(tree, units)
>>> cov.uncovered_chars
0
>>> cov.annotation_chars  # the REFORMADO note above art1, and nothing else
50

:py:func:`~md2akn.leaf_map` answers the reverse question, recovered from
``units`` by character-range containment rather than carried alongside them
— every leaf eId this tree has, and which unit's text carries it:

>>> refs = md2akn.leaf_map(tree, units)
>>> [(r.eId, r.unit_eId) for r in refs if r.unit_eId == "tit_PRIMERO__cap_I__art_1o"]
[('tit_PRIMERO__cap_I__art_1o__p_1', 'tit_PRIMERO__cap_I__art_1o'), ('tit_PRIMERO__cap_I__art_1o__para_I', 'tit_PRIMERO__cap_I__art_1o'), ('tit_PRIMERO__cap_I__art_1o__para_II__point_a', 'tit_PRIMERO__cap_I__art_1o'), ('tit_PRIMERO__cap_I__art_1o__para_II__point_b', 'tit_PRIMERO__cap_I__art_1o')]

Every leaf under ``art1`` — its chapeau, both fracciones, both incisos —
belongs to the *same* unit, ``piece=0``: the article was short enough that
rule 2 keeps it whole. Past :py:data:`~md2akn.DEFAULT_SPLIT_CAP` characters
(normalized), rule 3 instead splits it at its own direct children, chapeau
first, each later piece re-prefixed with the chapeau so the coverage
invariant still holds piece by piece:

>>> long_article = (
...     "**ARTICULO 9o.-** Son obligaciones de los patrones:\n\n"
...     "I.- Cumplir las disposiciones de las normas de trabajo;\n\n"
...     "II.- Pagar a los trabajadores los salarios e indemnizaciones;\n"
... )
>>> pieces = md2akn.text_units(long_article, cap=10)
>>> [(u.piece, u.piece_eId) for u in pieces]
[(1, 'art_9o__p_1'), (2, 'art_9o__para_I'), (3, 'art_9o__para_II')]
>>> pieces[1].text
'**ARTICULO 9o.-** Son obligaciones de los patrones: I.- Cumplir las disposiciones de las normas de trabajo;'

:py:func:`~md2akn.normalize` is the one function used for both the hash and
the embedded text (so a query normalized the same way lands on the same
text a unit did) — whitespace folding only, nothing a model would object to:

>>> md2akn.normalize("  Son   obligaciones\n\nde los patrones.  ")
'Son obligaciones de los patrones.'

Which of the two templates an article's own text follows —
``"bare"`` (the default, no prefix) or ``"contextual"`` (the law's name and
the article's own number) — is a manifest field, never a code edit
(:py:func:`~md2akn.text_units`'s own ``template`` argument), because #217's
own open question — whether an article needs that context to be findable —
is decided per published vector set, not in this package:

>>> contextual = md2akn.text_units(long_article, cap=10_000, template="contextual")
>>> contextual[0].text
'Artículo 9o. **ARTICULO 9o.-** Son obligaciones de los patrones: I.- Cumplir las disposiciones de las normas de trabajo; II.- Pagar a los trabajadores los salarios e indemnizaciones;'

Rules 8 and 9: the *acuerdo*-shaped instrument, and the cap
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Issue #227 added two rules to the same list, for the two collections
:py:mod:`scjn` publishes alongside the laws. A *lineamiento* is usually an
**acuerdo**, and an acuerdo does not number its provisions ``Artículo N``: it
writes ``**PRIMERO.-**``, or ``1.`` / ``2.1``. Rule 8 reads those as articles
— but only in a document that numbers *nothing* with ``Artículo N``, a
decision taken once per document by
:py:func:`~md2akn.structure.modo_sin_articulos`. That gate is what makes the
rule safe: it is off for all 315 federal laws, so none of them can change.

>>> acuerdo = "**PRIMERO.-** Uno.\n\n**SEGUNDO.-** Dos.\n\n**TERCERO.-** Tres.\n"
>>> [(u.unit_type, u.num) for u in md2akn.text_units(acuerdo)]
[('article', 'PRIMERO'), ('article', 'SEGUNDO'), ('article', 'TERCERO')]

The same three ordinals in a document that *does* have an article stay
ordinary paragraphs, which is what they are:

>>> ley = "**Artículo 1o.** Uno.\n\n" + acuerdo
>>> [(u.unit_type, u.num) for u in md2akn.text_units(ley)]
[('article', '1o')]

Rule 9 is the cap, enforced rather than hoped for: a unit still over it after
rules 2-6 is cut again at **paragraph** boundaries, reusing ``piece`` and the
same context prefix. A single paragraph longer than the cap is left whole —
nothing is ever cut mid-sentence — and :py:func:`~md2akn.max_unit_chars`
counts what is left that way:

>>> parrafos = "\n\n".join("Párrafo número %d." % n for n in range(1, 5)) + "\n"
>>> [(u.unit_type, u.piece, u.text) for u in md2akn.text_units(parrafos, cap=40)]
[('preamble', 1, 'Párrafo número 1. Párrafo número 2.'), ('preamble', 2, 'Párrafo número 3. Párrafo número 4.')]

:py:func:`~md2akn.max_unit_chars` is :py:func:`~md2akn.coverage`'s sibling:
where ``coverage()`` says nothing was lost, this says the units are usable.
``splittable == 0`` is the invariant a corpus build asserts; ``single_paragraph``
is the admissible residue:

>>> tree = md2akn.parse_markdown(parrafos)
>>> report = md2akn.max_unit_chars(tree, md2akn.text_units(parrafos, cap=40), cap=40)
>>> (report.units, report.max_chars, report.over_cap, report.splittable)
(2, 35, 0, 0)

``split_over_cap=False`` turns rule 9 off, which is how a vector set built
before #227 is reproduced byte for byte:

>>> len(md2akn.text_units(parrafos, cap=40, split_over_cap=False))
1

.. automodule:: md2akn.units
   :members:
   :private-members:
   :undoc-members:
