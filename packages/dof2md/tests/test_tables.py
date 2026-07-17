import unittest

from dof2md.tables import html_tables_to_markdown


class TestHtmlTablesToMarkdown(unittest.TestCase):
    def test_converts_mineru_style_table_to_markdown(self):
        # mineru emits tables as raw HTML with explicit rowspan/colspan=1.
        text = (
            "Texto antes.\n\n"
            "<table><tr><td rowspan=1 colspan=1>Apartado</td>"
            "<td rowspan=1 colspan=1>Págs.</td></tr>"
            "<tr><td rowspan=1 colspan=1>COMPETENCIA</td>"
            "<td rowspan=1 colspan=1>13-14</td></tr></table>\n\n"
            "Texto después."
        )
        out = html_tables_to_markdown(text)

        self.assertIn("| Apartado | Págs. |", out)
        self.assertIn("|---|---|", out)
        self.assertIn("| COMPETENCIA | 13-14 |", out)
        self.assertIn("Texto antes.", out)
        self.assertIn("Texto después.", out)
        self.assertNotIn("<table>", out)
        self.assertNotIn("rowspan", out)

    def test_colspan_expands_into_empty_columns(self):
        text = (
            "<table><tr><td colspan=2>Encabezado ancho</td></tr>"
            "<tr><td>A</td><td>B</td></tr></table>"
        )
        out = html_tables_to_markdown(text).strip()
        lines = out.splitlines()
        self.assertEqual(lines[0], "| Encabezado ancho |  |")
        self.assertEqual(lines[1], "|---|---|")
        self.assertEqual(lines[2], "| A | B |")

    def test_rowspan_leaves_empty_continuation_cell(self):
        text = (
            "<table>"
            "<tr><td rowspan=2>Grupo</td><td>fila 1</td></tr>"
            "<tr><td>fila 2</td></tr>"
            "</table>"
        )
        out = html_tables_to_markdown(text).strip()
        lines = out.splitlines()
        self.assertEqual(lines[0], "| Grupo | fila 1 |")
        self.assertEqual(lines[1], "|---|---|")
        self.assertEqual(lines[2], "|  | fila 2 |")

    def test_entities_in_cells_are_decoded(self):
        text = '<table><tr><td>&quot;SNDIF&quot;</td><td>a&nbsp;b</td></tr></table>'
        out = html_tables_to_markdown(text).strip()
        self.assertEqual(out.splitlines()[0], '| "SNDIF" | a b |')

    def test_pipe_in_cell_is_escaped(self):
        text = "<table><tr><td>a|b</td><td>c</td></tr></table>"
        out = html_tables_to_markdown(text).strip()
        self.assertEqual(out.splitlines()[0], r"| a\|b | c |")

    def test_leaves_text_without_tables_untouched(self):
        text = "Sólo texto, sin tablas.\n\nOtro párrafo."
        self.assertEqual(html_tables_to_markdown(text), text)

    def test_preserves_existing_markdown_table(self):
        text = "| a | b |\n|---|---|\n| 1 | 2 |"
        self.assertEqual(html_tables_to_markdown(text), text)


if __name__ == "__main__":
    unittest.main()
