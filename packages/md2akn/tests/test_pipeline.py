"""The spaCy layer: the component, the extensions, and the two efficiency
decisions of issue #158 that a test has to hold in place."""

import importlib
import tempfile
import unittest
from pathlib import Path

import spacy

import md2akn
from md2akn import pipeline
from md2akn.pipeline import MAX_LENGTH, SPAN_GROUP, get_nlp, parse_legal_provisions

DOCUMENTO = """---
fuente: scjn
ordenamiento: LEY X
---

**CAPITULO I.**

Un parrafo.
"""


class TestComponente(unittest.TestCase):
    def test_add_pipe_funciona_sobre_cualquier_language(self):
        nlp = spacy.blank("es")
        nlp.add_pipe("akn_segmenter")
        doc = nlp(DOCUMENTO)

        self.assertEqual(doc._.akn_tree.akn_type, "act")
        self.assertEqual(doc._.akn_meta["ordenamiento"], "LEY X")

    def test_el_span_group_lleva_todos_los_nodos_en_orden(self):
        doc = get_nlp()(DOCUMENTO)
        nodos = list(doc._.akn_tree.walk())

        self.assertEqual(len(doc.spans[SPAN_GROUP]), len(nodos))
        # Identity within the group travels on the span's label, not on the
        # extensions: two nodes can cover the same characters, and the group
        # is a list, so it keeps both.
        self.assertEqual(
            [s.label_ for s in doc.spans[SPAN_GROUP]], [n.eId for n in nodos]
        )
        self.assertEqual(
            [(s.start_char, s.end_char) for s in doc.spans[SPAN_GROUP]],
            [(n.start_char, n.end_char) for n in nodos],
        )

    def test_un_span_regresa_a_su_nodo(self):
        doc = get_nlp()(DOCUMENTO)
        capitulo = doc._.akn_tree.find("cap_I")

        self.assertIs(capitulo.span._.node, capitulo)
        self.assertEqual(capitulo.span._.akn_type, "chapter")
        self.assertEqual(capitulo.span._.eId, "cap_I")

    def test_en_un_rango_compartido_gana_el_nodo_mas_interno(self):
        # spaCy keys a Span's extensions by (name, start_char, end_char) --
        # the label is not part of the key -- so `act` and `body`, which
        # cover the same characters, cannot both be reachable. The innermost
        # is the one kept, deliberately: asked what a range is, "the body"
        # beats "the act that contains it".
        doc = get_nlp()(DOCUMENTO)
        act = doc._.akn_tree
        body, capitulo = act.children[0], act.find("cap_I")
        self.assertEqual(
            (act.start_char, act.end_char), (capitulo.start_char, capitulo.end_char)
        )

        self.assertIs(act.span._.node, capitulo)
        self.assertEqual(act.span._.akn_type, "chapter")
        self.assertEqual(body.span._.akn_type, "chapter")

    def test_el_componente_no_guarda_estado_entre_documentos(self):
        nlp = get_nlp()
        uno = nlp("Uno.")
        dos = nlp("Dos.\n\nTres.")

        self.assertEqual(uno._.akn_tree.text, "Uno.")
        self.assertEqual(dos._.akn_tree.text, "Dos.\n\nTres.")
        self.assertIsNot(uno._.akn_tree, dos._.akn_tree)


class TestExtensiones(unittest.TestCase):
    def test_recargar_el_modulo_no_relanza_por_extension_repetida(self):
        # `Span.set_extension` raises "extension already exists" on a second
        # call; every registration is guarded, so a second import is a no-op
        # rather than an error.
        importlib.reload(pipeline)
        self.assertTrue(spacy.tokens.Doc.has_extension("akn_tree"))


class TestEficiencia(unittest.TestCase):
    def test_el_pipeline_trae_solo_el_tokenizer_mas_el_segmentador(self):
        # No statistical model: the rules are deterministic, and a tagger or
        # parser nothing consults would cost per document for nothing.
        self.assertEqual(get_nlp().pipe_names, ["akn_segmenter"])

    def test_max_length_cubre_la_ley_mas_grande_del_corpus(self):
        # `ligie-2022` is 1.89 MB; spaCy's own default is 1,000,000.
        self.assertGreater(MAX_LENGTH, 1_900_000)
        self.assertEqual(get_nlp().max_length, MAX_LENGTH)

    def test_un_documento_de_mas_de_un_mega_no_revienta(self):
        # Long articles rather than many of them: what is under test is
        # `nlp.max_length`, and a wide document exercises it at a fraction of
        # the tree-building cost of a deep one.
        cuerpo = "palabras de relleno " * 250
        grande = f"**Artículo 1o.** {cuerpo}\n\n" * 250
        self.assertGreater(len(grande), 1_000_000)

        tree = md2akn.parse_markdown(grande)

        self.assertEqual(sum(1 for n in tree.walk() if n.akn_type == "article"), 250)


class TestParseLegalProvisions(unittest.TestCase):
    def test_lee_el_archivo_y_devuelve_la_raiz(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "01-04-2025.md"
            ruta.write_text(DOCUMENTO, encoding="utf-8")

            tree = parse_legal_provisions(ruta)

        self.assertEqual(tree.akn_type, "act")
        self.assertEqual(tree.meta["fuente"], "scjn")
        self.assertEqual(tree.find("cap_I").num, "I")

    def test_acepta_una_ruta_como_cadena(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "x.md"
            ruta.write_text("Uno.\n", encoding="utf-8")

            self.assertEqual(parse_legal_provisions(str(ruta)).akn_type, "act")


class TestApiPublica(unittest.TestCase):
    def test_todo_lo_publico_se_alcanza_desde_el_paquete(self):
        for nombre in md2akn.__all__:
            self.assertTrue(hasattr(md2akn, nombre), nombre)

    def test_el_paquete_tiene_version_dinamica(self):
        self.assertRegex(md2akn.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
