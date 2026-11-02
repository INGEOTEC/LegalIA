"""The two phases of the builder (issue #304): `md2akn.skeleton` reads what each API block is
from the outline markers, `md2akn.structure` then places the text inside the block it is in.

The tests below pin what the split promises and the output refactor left unchanged:

* the skeleton alone, with no tree, knows each API block, its role, its heading block and the
  split halves (`Skeleton.blocks`);
* the five departures from a marker the parser takes on purpose are named predicates
  (`skeleton.DEVIATIONS`), one per class of `validate.CLASES_DOCUMENTADAS`, and the 57 blocks of
  the release recorded in `fixtures_units/marker_documented.json` are exactly those classes;
* the interior bound (`RULES.md`, S4): a text rule may open a node below the root of the block it
  is reading and never above it, whatever the block's text prints;
* a document with no marker has no API block at all.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from md2akn import parse_markdown
from md2akn.hits import HITS, hit, reset
from md2akn.segmenter import iter_blocks
from md2akn.skeleton import (
    DEVIATIONS,
    Skeleton,
    es_articulo_de_encabezado,
    es_articulo_servido_como_capitulo,
    es_etiqueta_repetida,
    es_firma_de_cierre,
    es_numero_por_numero,
)
from md2akn.validate import CLASES_DOCUMENTADAS, _block_escapes, block_conformance

M = "<!-- scjn:block "
UNIT_FIXTURES = Path(__file__).parent / "fixtures_units"


def _bloque(orden, kind, label, body, num=None, numero=None, typ=None):
    attrs = f"orden={orden} numero={numero if numero is not None else orden} kind={kind}"
    if typ:
        attrs += f" type={typ}"
    if num:
        attrs += f' num="{num}"'
    return f'{M}{attrs} label="{label}" -->\n\n{body}\n'


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _ancestors(node):
    while node.parent is not None:
        node = node.parent
        yield node


# -- the skeleton on its own -------------------------------------------------------------


def test_skeleton_reads_every_api_block_without_building_a_tree():
    text = "\n".join([
        _bloque(1, "container", "CAPÍTULO I", "**CAPÍTULO I**\n\n**Del objeto**", num="I", typ="chapter"),
        _bloque(2, "article", "Artículo 1", "**Artículo 1.-** Esta ley regula el objeto.", num="1"),
        _bloque(3, "other", "NOTA EXPLICATIVA", "**NOTA EXPLICATIVA**\n\nUna nota."),
    ])
    skeleton = Skeleton(list(iter_blocks(text)))
    api_blocks = skeleton.blocks
    assert [b.marca["kind"] for b in api_blocks] == ["container", "article", "other"]
    assert [b.rol for b in api_blocks] == [None, None, "note"]
    assert [b.marca["label"] for b in api_blocks] == ["CAPÍTULO I", "Artículo 1", "NOTA EXPLICATIVA"]
    # Each API block ends where the next marker starts, the last one at the end of the document.
    assert [b.fin for b in api_blocks[:-1]] == [b.inicio for b in api_blocks[1:]]
    assert api_blocks[-1].fin == len(skeleton.bloques)
    # The block each marker's kind is about is the heading block, never the marker itself.
    assert all(b.encabezado is not None and b.encabezado > b.inicio for b in api_blocks)
    assert not any(b.continuacion for b in api_blocks)


def test_a_document_with_no_marker_has_no_api_block():
    skeleton = Skeleton(list(iter_blocks("**Artículo 1o.** Uno.\n\n**Artículo 2o.** Dos.\n")))
    assert skeleton.blocks == []
    assert skeleton.marcas == {}
    assert skeleton.indices == {}


def test_the_second_half_of_a_split_block_is_a_continuation():
    text = "\n".join([
        _bloque(1, "other", "ARTÍCULOS", "**ARTÍCULOS**"),
        _bloque(2, "other", "CONTENIDO ARTICULOS", "**Artículo 1.-** Uno.\n\n**Artículo 2.-** Dos."),
    ])
    api_blocks = Skeleton(list(iter_blocks(text))).blocks
    assert [b.continuacion for b in api_blocks] == [False, True]


# -- the named departures ----------------------------------------------------------------


DEPARTURES = {
    "documented-number-by-numero": [
        _bloque(1, "article", "ARTICULO XXVII", "**ARTICULO XXXVII.-** Texto.", num="XXVII", numero=37),
    ],
    "documented-number-label-repeated": [
        _bloque(1, "article", "ARTÍCULO 47-J", "**Artículo 47-J.-** Uno.", num="47-J", numero=47),
        _bloque(2, "article", "ARTÍCULO 47-J", "**Artículo 47-K.-** Dos.", num="47-J", numero=47),
    ],
    "documented-article-served-as-chapter": [
        _bloque(1, "article", "ARTÍCULO 67", "**Capítulo 67**\n\nAnimales vivos.", num="67", numero=67),
        _bloque(2, "article", "ARTÍCULO 68", "**Capítulo 68**\n\nCarne.", num="68", numero=68),
    ],
    "documented-closing-signature": [
        _bloque(1, "article", "ARTÍCULO 1", "**Artículo 1.-** Uno.", num="1"),
        _bloque(2, "transitorios", "TRANSITORIOS", "Ciudad de México, a 1 de enero de 2020.- Rúbrica."),
    ],
    "documented-article-heading": [
        piece
        for n in range(1, 4)
        for piece in (
            _bloque(3 * n - 2, "article", f"ARTICULO {n}°", f"**ARTICULO {n}°.**\n\nGeneralidades", num=str(n)),
            _bloque(3 * n - 1, "provision", f"{n}.1", f"**{n}.1.-** Uno."),
            _bloque(3 * n, "provision", f"{n}.2", f"**{n}.2.-** Dos."),
        )
    ],
}


def test_every_class_of_documented_deviation_has_exactly_one_named_predicate():
    assert set(DEVIATIONS) == set(CLASES_DOCUMENTADAS) == set(DEPARTURES)
    assert all(callable(predicate) for predicate in DEVIATIONS.values())


@pytest.mark.parametrize("klass", sorted(DEPARTURES))
def test_a_departure_is_reported_under_its_own_class_and_no_other(klass):
    tree = parse_markdown("\n".join(DEPARTURES[klass]))
    classes = {d.klass for d in block_conformance(tree)}
    assert klass in classes
    assert {c for c in classes if c.startswith("documented-")} == {klass}
    # Taken on purpose, so nothing else disagrees (the shortened heading-only fixture
    # does not give its provisions the numbering of a real regulation, hence the exception).
    if klass != "documented-article-heading":
        assert all(c.startswith("documented-") for c in classes)


def test_the_predicates_say_when_a_departure_does_not_apply():
    # number-by-numero: the text prints the number the API's own `numero` says.
    assert es_numero_por_numero("XXXVII", "XXVII", "37") is True
    assert es_numero_por_numero("XXXVII", "XXXVII", "37") is False
    # label-repeated: only when an earlier block already took the label's identifier.
    assert es_etiqueta_repetida("47-J", "47-K", True) is True
    assert es_etiqueta_repetida("47-J", "47-K", False) is False
    assert es_etiqueta_repetida("47-J", None, True) is False
    # served-as-chapter: the node is the chapter of the label's own number.
    assert es_articulo_servido_como_capitulo("article", ("contenedor", {"num": "83"}), {"num": "83"}) is True
    assert es_articulo_servido_como_capitulo("article", ("contenedor", {"num": "84"}), {"num": "83"}) is False
    # article-heading: a heading and a caption, not an article with its text.
    assert es_articulo_de_encabezado(["**ARTICULO 1°.**", "Generalidades"]) is True
    assert es_articulo_de_encabezado(["**Artículo 1.** " + "x" * 200]) is False
    assert es_articulo_de_encabezado([]) is False
    # closing-signature: the block holds only the signatures.
    blocks = list(iter_blocks("**Artículo 1.** Uno.\n\nCiudad de México, a 1 de enero.- Rúbrica."))
    assert es_firma_de_cierre(blocks, 1) is True
    assert es_firma_de_cierre(blocks, 0) is False


def test_the_documented_blocks_of_the_release_are_exactly_the_named_classes():
    entries = json.loads((UNIT_FIXTURES / "marker_documented.json").read_text(encoding="utf-8"))
    assert len(entries) == 57
    assert {e["class"] for e in entries} == set(DEVIATIONS)
    by_class = Counter(e["class"] for e in entries)
    assert sum(by_class.values()) == 57
    assert all(by_class[k] > 0 for k in CLASES_DOCUMENTADAS)


# -- the interior bound ------------------------------------------------------------------


def test_text_rules_never_open_a_node_above_the_root_of_the_block_they_read():
    """A block whose own text prints `TÍTULO`/`CAPÍTULO`/`ANEXO` lines nests those below its
    root; the next block's root is never inside it, and nothing it opens ends after it."""
    text = "\n".join([
        _bloque(1, "container", "CAPÍTULO I", "**CAPÍTULO I**\n\n**Del objeto**\n\n"
                "**TÍTULO SEGUNDO**\n\n**De lo demás**\n\n**ANEXO 1**\n\nTexto del anexo.", num="I", typ="chapter"),
        _bloque(2, "article", "Artículo 1", "**Artículo 1.-** Esta ley regula el objeto.\n\n"
                "**CAPÍTULO II**\n\nUna cita que parece un encabezado.", num="1"),
        _bloque(3, "container", "CAPÍTULO II", "**CAPÍTULO II**\n\n**Segundo**", num="II", typ="chapter"),
    ])
    tree = parse_markdown(text)
    assert _block_escapes(tree) == []
    assert not [d for d in block_conformance(tree) if not d.documented]
    marks = [m.start() for m in re.finditer(r"<!-- scjn:block ", text)] + [len(text)]
    roots = []
    for k in range(3):
        inside = [n for n in _walk(tree) if marks[k] <= n.start_char < marks[k + 1]
                  and n.akn_type not in ("act", "body")]
        root = inside[0]
        roots.append(root)
        for node in inside[1:]:
            assert root is node or root in _ancestors(node), (k, root.eId, node.eId)
            assert node.end_char <= max(root.end_char, marks[k + 1]), (k, node.eId)
    # The third block's chapter is a sibling of the first's, never inside it.
    assert roots[2].parent is roots[0].parent
    # Article 1 keeps its quoted heading as its own text, opening no container.
    assert [n.akn_type for n in _walk(roots[1])].count("chapter") == 0


# -- the counters ------------------------------------------------------------------------


def test_rule_hit_counters_count_per_rule_branch_and_marking():
    reset()
    hit("S4", "x")
    hit("S4", "x")
    hit("B3", "mode-ordinal", False)
    assert dict(HITS) == {"S4/x/marked": 2, "B3/mode-ordinal/unmarked": 1}
    reset()
    assert not HITS
    parse_markdown(_bloque(1, "article", "Artículo 1", "**Artículo 1.-** Uno.", num="1"))
    assert any(key.startswith("S1/") for key in HITS)
    reset()
