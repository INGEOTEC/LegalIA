import unittest

import fitz

from dof2md.converter import body_font_size, doc_to_markdown


def _build_doc():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "TITULO DE PRUEBA", fontsize=20, fontname="helv")
    page.insert_text((50, 90), "AVISO IMPORTANTE", fontsize=10, fontname="hebo")
    page.insert_text(
        (50, 120),
        "Este es un parrafo de texto normal que sirve como cuerpo del documento "
        "de prueba y debe dominar el conteo de tamanos de fuente en la pagina.",
        fontsize=10,
        fontname="helv",
    )
    return doc


class TestBodyFontSize(unittest.TestCase):
    def test_detects_most_common_size_as_body(self):
        doc = _build_doc()
        self.assertEqual(body_font_size(doc), 10)


class TestDocToMarkdown(unittest.TestCase):
    def setUp(self):
        self.doc = _build_doc()
        self.markdown = doc_to_markdown(self.doc)

    def test_includes_page_marker(self):
        self.assertIn("#### Page 1", self.markdown)

    def test_large_font_becomes_heading(self):
        self.assertIn("## TITULO DE PRUEBA", self.markdown)

    def test_bold_body_size_becomes_bold(self):
        self.assertIn("**AVISO IMPORTANTE**", self.markdown)

    def test_plain_text_is_not_wrapped(self):
        self.assertIn("Este es un parrafo de texto normal", self.markdown)
        self.assertNotIn("**Este es un parrafo", self.markdown)
        self.assertNotIn("## Este es un parrafo", self.markdown)

    def test_multi_page_documents_number_each_page(self):
        doc = fitz.open()
        for _ in range(3):
            page = doc.new_page()
            page.insert_text((50, 50), "contenido de pagina", fontsize=10, fontname="helv")
        markdown = doc_to_markdown(doc)
        self.assertIn("#### Page 1", markdown)
        self.assertIn("#### Page 2", markdown)
        self.assertIn("#### Page 3", markdown)


if __name__ == "__main__":
    unittest.main()
