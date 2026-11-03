"""`scjn.markdown`, the plain-text-to-Markdown writer split out of `scjn.api`
(issue #303). The paragraph rules themselves are pinned in `test_api.py` (they
reach the writer through `scjn.api`, which re-exports it, and pass unchanged);
what is new here is the split itself: every name is still where it was, it is
the *same object*, and `scjn.markdown` does not import the client back."""

import ast
import unittest
from pathlib import Path

import scjn.api
import scjn.markdown

#: Every name the writer owned while it lived in `scjn.api`; tests and scripts reach
#: them through `scjn.api` (`@patch("scjn.api._...")` included), so all must stay.
WRITER_NAMES = (
    "_BLOCK_ANNOTATION", "_BLOCK_SEPARATOR", "_COMPONENTE", "_ETIQUETA_HTML", "_LATINO",
    "_LATINO_UNIDAD", "_LEAD_ARTICULO", "_LEAD_LISTA", "_LEAD_ORDINAL", "_MARGEN",
    "_NUM_ARTICULO", "_ORDINAL", "_ORDINAL_DECENA", "_ORDINAL_PALABRA", "_ORDINAL_UNIDAD",
    "_PARRAFOS", "_ROMANO", "_SALTO_HTML", "_TRANSITORIOS_PARRAFO", "_es_titular",
    "_formatea_parrafo", "_merge_row", "_normalize_cell", "_paragraphs_from_tabbed_block",
    "articulos_a_markdown",
)


class SplitTests(unittest.TestCase):
    def test_scjn_api_still_exposes_every_writer_name_as_the_same_object(self):
        for name in WRITER_NAMES:
            with self.subTest(name):
                self.assertTrue(hasattr(scjn.markdown, name), name)
                self.assertIs(getattr(scjn.api, name), getattr(scjn.markdown, name))

    def test_the_writer_does_not_import_the_client(self):
        source = Path(scjn.markdown.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        imported = {
            (n.module if isinstance(n, ast.ImportFrom) else a.name)
            for n in top_level for a in (n.names if isinstance(n, ast.Import) else [None])
        }
        self.assertNotIn("scjn.api", imported)

    def test_the_client_side_names_stay_in_the_client(self):
        for name in ("ScjnApi", "cabecera", "snapshot", "descarga_ordenamiento", "guarda_crudo",
                     "articulos_de_crudo", "completeness_defects"):
            with self.subTest(name):
                self.assertTrue(hasattr(scjn.api, name))
                self.assertFalse(hasattr(scjn.markdown, name))


class WriterTests(unittest.TestCase):
    def test_a_block_with_no_tab_converts_line_by_line(self):
        from scjn.api import Articulo

        markdown = scjn.markdown.articulos_a_markdown(
            [Articulo(1, 1, "ARTÍCULO 1", "Artículo 1.- Uno.\r\nSegunda línea.")]
        )
        self.assertEqual(markdown, "**Artículo 1.-** Uno.\n\nSegunda línea.\n")

    def test_a_tabbed_block_is_one_row_per_blank_line_separated_group(self):
        from scjn.api import Articulo

        markdown = scjn.markdown.articulos_a_markdown(
            [Articulo(0, 1, "TABLA", "a\tb\r\n\r\nc\td")]
        )
        self.assertEqual(markdown, "| a | b |\n\n| c | d |\n")

    def test_outline_adds_one_marker_and_strip_markers_takes_it_out(self):
        from scjn.api import Articulo
        from scjn.outline import strip_markers

        blocks = [Articulo(0, 1, "ENCABEZADO", "LEY"), Articulo(0, 2, "N", "[N. DE E. VÉASE]")]
        with_outline = scjn.markdown.articulos_a_markdown(blocks, outline=True)
        self.assertEqual(with_outline.count("scjn:block"), 1)
        self.assertEqual(strip_markers(with_outline), scjn.markdown.articulos_a_markdown(blocks))


if __name__ == "__main__":
    unittest.main()
