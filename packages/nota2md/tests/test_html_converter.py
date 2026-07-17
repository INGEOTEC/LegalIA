import unittest

from nota2md.html_converter import html_to_markdown


class TestHtmlToMarkdown(unittest.TestCase):
    def test_titulo_1_becomes_h1(self):
        html = "<body><div><h1 class='Titulo_1'><span>DECRETO por el que se reforma</span></h1></div></body>"
        self.assertEqual(html_to_markdown(html), "# DECRETO por el que se reforma")

    def test_titulo_2_and_anotacion_become_h2(self):
        html = (
            "<body><div>"
            "<h2 class='Titulo_2'><span>Al margen un sello</span></h2>"
            "<div class='ANOTACION'><span>CLÁUSULAS</span></div>"
            "</div></body>"
        )
        self.assertEqual(
            html_to_markdown(html), "## Al margen un sello\n\n## CLÁUSULAS"
        )

    def test_texto_div_becomes_paragraph(self):
        html = "<body><div><div class='Texto'><span>Un párrafo normal.</span></div></div></body>"
        self.assertEqual(html_to_markdown(html), "Un párrafo normal.")

    def test_bold_span_becomes_double_star(self):
        html = (
            "<body><div><div class='Texto'>"
            "<span>Lo firman </span>"
            "<span style='font-weight:bold;'>Nombre Apellido</span>"
            "<span>.- Rúbrica.</span>"
            "</div></div></body>"
        )
        self.assertEqual(
            html_to_markdown(html), "Lo firman **Nombre Apellido**.- Rúbrica."
        )

    def test_italic_span_becomes_single_star(self):
        html = (
            "<body><div><div class='Texto'>"
            "<span style='font-style:italic;'>Este Programa es público</span>"
            "</div></div></body>"
        )
        self.assertEqual(html_to_markdown(html), "*Este Programa es público*")

    def test_bold_italic_combines_markers(self):
        html = (
            "<body><div><div class='Texto'>"
            "<span style='font-weight:bold;font-style:italic;'>importante</span>"
            "</div></div></body>"
        )
        self.assertEqual(html_to_markdown(html), "***importante***")

    def test_adjacent_bold_spans_merge_into_one_run(self):
        # DOF fragments a bold phrase across several spans (plus a space span);
        # they must render as a single **…** run, not one per span.
        html = (
            "<body><div><div class='Texto'>"
            "<span style='font-weight:bold;'>Abril Cristina Sabido</span>"
            "<span style='font-weight:bold;'> </span>"
            "<span style='font-weight:bold;'>Alcérreca</span>"
            "</div></div></body>"
        )
        self.assertEqual(html_to_markdown(html), "**Abril Cristina Sabido Alcérreca**")

    def test_br_is_a_line_break_within_paragraph(self):
        html = "<body><div><div class='Texto'><span>Línea uno</span><br><span>Línea dos</span></div></div></body>"
        self.assertEqual(html_to_markdown(html), "Línea uno\nLínea dos")

    def test_entities_are_decoded(self):
        html = "<body><div><div class='Texto'><span>&quot;LAS PARTES&quot;</span></div></div></body>"
        self.assertEqual(html_to_markdown(html), '"LAS PARTES"')

    def test_nbsp_only_paragraph_is_dropped(self):
        html = (
            "<body><div>"
            "<div class='Texto'><span>Contenido</span></div>"
            "<div class='Texto'>&nbsp;</div>"
            "</div></body>"
        )
        self.assertEqual(html_to_markdown(html), "Contenido")

    def test_table_becomes_github_table(self):
        html = (
            "<body><div><table>"
            "<tr><td><span>SNDIF</span></td><td><span>Nombre</span></td></tr>"
            "<tr><td><span>SEDIF</span></td><td><span>Otro</span></td></tr>"
            "</table></div></body>"
        )
        self.assertEqual(
            html_to_markdown(html),
            "| SNDIF | Nombre |\n|---|---|\n| SEDIF | Otro |",
        )

    def test_table_cell_escapes_pipe_and_flattens_breaks(self):
        html = (
            "<body><div><table>"
            "<tr><td><span>a</span><br><span>b|c</span></td><td><span>d</span></td></tr>"
            "</table></div></body>"
        )
        self.assertEqual(
            html_to_markdown(html), "| a b\\|c | d |\n|---|---|"
        )

    def test_style_and_head_are_ignored(self):
        html = (
            "<html><head><title>T</title><style>.Texto{color:red}</style></head>"
            "<body><div><div class='Texto'><span>Solo el cuerpo</span></div></div></body></html>"
        )
        self.assertEqual(html_to_markdown(html), "Solo el cuerpo")

    def test_empty_html_is_empty_string(self):
        self.assertEqual(html_to_markdown(""), "")


if __name__ == "__main__":
    unittest.main()
