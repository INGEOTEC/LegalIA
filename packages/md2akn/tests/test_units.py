"""Every text of a law, ready to embed (issue #218 Fase 1, plan for #217).

The headline test is the coverage invariant: over every fixture law,
`coverage()` must report zero uncovered non-whitespace characters once the
frontmatter and the reform annotations are accounted for. Everything else —
`(eId, piece)` uniqueness, `leaf_map` completeness, determinism, the split
rule on hand-built trees, and golden files for three dedicated fixture laws —
is here to keep that invariant honest rather than vacuous.

To regenerate the golden files after an intentional change::

    python -m tests.test_units --write     # from packages/md2akn
"""

import json
import unittest
from pathlib import Path

import pytest
import spacy

from md2akn import parse_markdown
from md2akn.model import AknNode, EIdAllocator
from md2akn.units import (
    DEFAULT_SPLIT_CAP,
    coverage,
    leaf_map,
    normalize,
    text_units,
)

FIXTURES = Path(__file__).parent / "fixtures"
UNIT_FIXTURES = Path(__file__).parent / "fixtures_units"
ALL_LAW_FIXTURES = sorted(FIXTURES.glob("*.md")) + sorted(UNIT_FIXTURES.glob("*.md"))


def _real_leaves(tree: AknNode):
    return [n for n in tree.walk() if not n.children and n.akn_type not in ("act", "body")]


class TestCoverageInvariant(unittest.TestCase):
    """The property #218 Fase 1 is built around: every non-whitespace
    character is either frontmatter, an annotation, or embedded in some
    unit's text — never none of the three."""

    def test_cero_no_cubiertos_en_cada_fixture(self):
        for path in ALL_LAW_FIXTURES:
            with self.subTest(fixture=path.name):
                text = path.read_text(encoding="utf-8")
                tree = parse_markdown(text)
                units = text_units(text)
                cov = coverage(tree, units)
                self.assertEqual(cov.uncovered_chars, 0, cov)
                self.assertEqual(
                    cov.total_chars,
                    cov.frontmatter_chars + cov.annotation_chars + cov.covered_chars,
                )

    def test_las_anotaciones_contadas_coinciden_con_notas_reales(self):
        # Every character coverage() sets aside as "annotation" comes from an
        # Annotation.raw somewhere on the tree -- not a bucket for whatever
        # the rules above happened to miss.
        for path in ALL_LAW_FIXTURES:
            with self.subTest(fixture=path.name):
                text = path.read_text(encoding="utf-8")
                tree = parse_markdown(text)
                cov = coverage(tree, text_units(text))
                raw_notes = [n.raw for node in tree.walk() if node is not tree for n in node.notes]
                raw_notes += [n.raw for n in tree.notes]
                annotation_chars_from_notes = sum(
                    sum(1 for c in raw if not c.isspace()) for raw in raw_notes
                )
                self.assertEqual(cov.annotation_chars, annotation_chars_from_notes)


class TestUnicidadYLeafMap(unittest.TestCase):
    def test_eid_piece_unico_y_cada_hoja_en_una_unidad(self):
        for path in ALL_LAW_FIXTURES:
            with self.subTest(fixture=path.name):
                text = path.read_text(encoding="utf-8")
                tree = parse_markdown(text)
                units = text_units(text)

                claves = [(u.eId, u.piece) for u in units]
                self.assertEqual(len(claves), len(set(claves)))

                refs = leaf_map(tree, units)
                hojas = _real_leaves(tree)
                self.assertEqual(len(refs), len(hojas))
                eids_referenciados = {r.eId for r in refs}
                self.assertEqual(eids_referenciados, {h.eId for h in hojas})


class TestDeterminismo(unittest.TestCase):
    def test_mismo_markdown_mismas_unidades(self):
        text = (FIXTURES / "containers.md").read_text(encoding="utf-8")
        primero = text_units(text)
        segundo = text_units(text)
        self.assertEqual(
            [(u.unit_type, u.eId, u.piece, u.text_sha1) for u in primero],
            [(u.unit_type, u.eId, u.piece, u.text_sha1) for u in segundo],
        )

    def test_el_hash_es_del_texto_normalizado(self):
        for path in ALL_LAW_FIXTURES:
            with self.subTest(fixture=path.name):
                for unit in text_units(path.read_text(encoding="utf-8")):
                    self.assertEqual(unit.text, normalize(unit.text))


class TestNormalize(unittest.TestCase):
    def test_pliega_espacios_sin_tocar_nada_mas(self):
        self.assertEqual(normalize("  Uno   Dos\n\nTres  "), "Uno Dos Tres")
        self.assertEqual(normalize("Único."), "Único.")


class TestTemplate(unittest.TestCase):
    def test_bare_no_lleva_nombre_ni_num_de_articulo(self):
        text = (
            "---\nnombre_buscado: LEY DE PRUEBA\n---\n\n"
            "**Articulo 1o.** Contenido.\n"
        )
        units = text_units(text, template="bare")
        articulo = next(u for u in units if u.unit_type == "article")
        self.assertNotIn("LEY DE PRUEBA", articulo.text)

    def test_contextual_antepone_nombre_y_num(self):
        text = (
            "---\nnombre_buscado: LEY DE PRUEBA\n---\n\n"
            "**Articulo 1o.** Contenido.\n"
        )
        units = text_units(text, template="contextual")
        articulo = next(u for u in units if u.unit_type == "article")
        self.assertIn("LEY DE PRUEBA", articulo.text)
        self.assertIn("1o", articulo.text)

    def test_heading_y_loose_siempre_llevan_el_camino(self):
        # Rule 7: unconditional for heading/loose, regardless of template.
        text = "**CAPITULO I**\n\nUna fila suelta.\n\n**Articulo 1o.** Uno.\n"
        for template in ("bare", "contextual"):
            units = text_units(text, template=template)
            loose = next(u for u in units if u.unit_type == "loose")
            self.assertIn("CAPÍTULO I", loose.text)

    def test_template_desconocido_es_un_error(self):
        with self.assertRaises(ValueError):
            text_units("**Articulo 1o.** Uno.\n", template="unknown")


# -- the split rule on hand-built trees --------------------------------- #

def _tree_with_article_children(children_specs, article_num="4o"):
    """A minimal `act` -> `body` -> `article` tree, with `article`'s direct
    children built exactly as specified -- so the split rule (#218 Fase 1,
    rule 3) is exercised directly, with no dependency on what the segmenter's
    own block classifier would decide for a given piece of Markdown.

    `children_specs`: a list of `(text, is_chapeau, is_tail)`, concatenated
    with a blank line between them to build the document the spans are cut
    from.
    """
    parts = [text for text, _, _ in children_specs]
    full_text = "\n\n".join(parts)
    nlp = spacy.blank("es")
    doc = nlp(full_text)

    def span(start, end, label):
        s = doc.char_span(start, end, label=label, alignment_mode="expand")
        assert s is not None, (start, end, full_text)
        return s

    eids = EIdAllocator()
    act = AknNode("act", span(0, len(full_text), "act"), eId=eids.allocate("act"))
    body = act.add(AknNode("body", span(0, len(full_text), "body"), eId=eids.allocate("body")))
    article = body.add(
        AknNode(
            "article", span(0, len(full_text), "article"),
            eId=eids.allocate(f"art_{article_num}"), num=article_num,
        )
    )

    offset = 0
    for i, (text, is_chapeau, is_tail) in enumerate(children_specs, 1):
        start = offset
        end = offset + len(text)
        node = AknNode(
            "content", span(start, end, f"c{i}"), eId=eids.allocate(f"art_{article_num}_c{i}"),
        )
        node.is_chapeau = is_chapeau
        node.is_tail = is_tail
        article.add(node)
        offset = end + 2  # the "\n\n" joiner

    return act


class TestReglaDePartida(unittest.TestCase):
    """`text_units` also accepts an already-parsed `AknNode`, which is what
    lets these trees -- built by hand, bypassing the segmenter entirely --
    exercise the split rule in isolation."""

    def test_sin_chapeau(self):
        # No child is flagged is_chapeau: the article opened directly on a
        # list marker, with no introductory text at all.
        tree = _tree_with_article_children(
            [("Primer parrafo suelto, bastante largo para forzar el corte.", False, False),
             ("Segundo parrafo suelto, tambien largo, para que haya dos piezas.", False, False)],
        )
        units = text_units(tree, cap=10)
        self.assertEqual([u.piece for u in units], [1, 2])
        self.assertTrue(all(u.unit_type == "article_piece" for u in units))
        # No chapeau text is prefixed onto either piece.
        self.assertNotIn("Segundo", units[0].text)

    def test_solo_chapeau(self):
        tree = _tree_with_article_children([("El chapeau, y nada mas.", True, False)])
        units = text_units(tree, cap=1)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].piece, 1)
        self.assertIn("chapeau", units[0].text)

    def test_una_sola_fraccion(self):
        tree = _tree_with_article_children(
            [("El chapeau introductorio.", True, False),
             ("La unica fraccion de este articulo.", False, False)],
        )
        units = text_units(tree, cap=5)
        self.assertEqual([u.piece for u in units], [1, 2])
        # Piece 2 is prefixed with the chapeau (rule 3).
        self.assertIn("chapeau introductorio", units[1].text)
        self.assertIn("unica fraccion", units[1].text)

    def test_fraccion_mas_larga_que_el_tope_por_si_sola(self):
        larga = "Fraccion " + ("muy larga " * 50) + "final."
        tree = _tree_with_article_children(
            [("Chapeau breve.", True, False), (larga, False, False)],
        )
        units = text_units(tree, cap=20)
        self.assertEqual(len(units), 2)
        # Not further split: one piece, even though it alone exceeds cap.
        self.assertGreater(len(units[1].text), 20)
        self.assertIn("Fraccion", units[1].text)

    def test_chapeau_y_cola(self):
        tree = _tree_with_article_children(
            [("Chapeau.", True, False),
             ("Fraccion I.", False, False),
             ("Parrafo de cola.", False, True)],
        )
        units = text_units(tree, cap=1)
        self.assertEqual([u.piece for u in units], [1, 2, 3])
        self.assertIn("cola", units[2].text)
        self.assertIn("Chapeau", units[2].text)


# -- golden files --------------------------------------------------------- #

def _serialize(units) -> list[dict]:
    return [
        {
            "unit_type": u.unit_type,
            "eId": u.eId,
            "piece": u.piece,
            "piece_eId": u.piece_eId,
            "akn_type": u.akn_type,
            "num": u.num,
            "path": list(u.path),
            "text": u.text,
            "text_sha1": u.text_sha1,
        }
        for u in units
    ]


def _golden(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    tree = parse_markdown(text)
    units = text_units(text, cap=200)
    cov = coverage(tree, units)
    return {
        "coverage": {
            "total_chars": cov.total_chars,
            "frontmatter_chars": cov.frontmatter_chars,
            "annotation_chars": cov.annotation_chars,
            "covered_chars": cov.covered_chars,
            "uncovered_chars": cov.uncovered_chars,
        },
        "units": _serialize(units),
    }


UNIT_FIXTURE_NAMES = sorted(p.stem for p in UNIT_FIXTURES.glob("*.md"))


@pytest.mark.parametrize("nombre", UNIT_FIXTURE_NAMES)
def test_unidades_de_fixture_coinciden_con_lo_esperado(nombre):
    esperado = json.loads((UNIT_FIXTURES / f"{nombre}.json").read_text(encoding="utf-8"))
    assert _golden(UNIT_FIXTURES / f"{nombre}.md") == esperado


def test_hay_fixtures_de_unidades():
    assert len(UNIT_FIXTURE_NAMES) >= 2


if __name__ == "__main__":  # pragma: no cover
    import sys

    if "--write" not in sys.argv:
        raise SystemExit("usage: python -m tests.test_units --write")
    for nombre in UNIT_FIXTURE_NAMES:
        destino = UNIT_FIXTURES / f"{nombre}.json"
        destino.write_text(
            json.dumps(_golden(UNIT_FIXTURES / f"{nombre}.md"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("wrote", destino)
