import unittest

from nota2md.cutter import cut_markdown_by_titles, locate_titles


class TestCutMarkdownByTitles(unittest.TestCase):
    def setUp(self):
        self.markdown = (
            "resto de la nota anterior que termina en esta página.\n"
            "\n"
            "## AVISO de deslinde del predio SUP 033, superficie 31.51 metros, Palenque, Chis.\n"
            "\n"
            "El presente aviso hace constar la medición del predio.\n"
            "Cuerpo de la nota objetivo.\n"
            "\n"
            "## AVISO de deslinde del predio SUP 036, superficie 25.64 metros, Palenque, Chis.\n"
            "\n"
            "Comienza la siguiente nota, que no se debe incluir.\n"
        )
        self.titulo = "Aviso de deslinde del predio SUP 033, superficie 31.51 metros, Palenque, Chis."
        self.siguiente = "Aviso de deslinde del predio SUP 036, superficie 25.64 metros, Palenque, Chis."

    def test_drops_previous_tail_and_next_note(self):
        cut = cut_markdown_by_titles(self.markdown, self.titulo, self.siguiente)

        self.assertTrue(cut.startswith("## AVISO de deslinde del predio SUP 033"))
        self.assertIn("Cuerpo de la nota objetivo.", cut)
        self.assertNotIn("nota anterior", cut)
        self.assertNotIn("SUP 036", cut)
        self.assertNotIn("siguiente nota", cut)

    def test_near_duplicate_next_title_is_found_after_the_start(self):
        # SUP 033 and SUP 036 titles are near-identical; the end boundary must
        # land on the SUP 036 heading, not back on the SUP 033 one.
        located = locate_titles(self.markdown, self.titulo, self.siguiente)
        self.assertGreater(located["end"], located["start"])
        self.assertGreaterEqual(located["start_confidence"], 0.9)
        self.assertGreaterEqual(located["end_confidence"], 0.9)

    def test_trims_org_header_preceding_next_title(self):
        # The DOF prints the next note's organism header ABOVE its title; that
        # header (and the blank lines around it) must not be left dangling at
        # the tail of this note. Reproduces codNota=5793639 -> 5793641.
        markdown = (
            "## ACUERDO por el que se establecen acciones de regularización\n"
            "\n"
            "Cuerpo del acuerdo.\n"
            "Ciudad de México.- Director General.- Rúbrica.\n"
            "\n"
            "# SECRETARIA DE ENERGIA\n"
            "\n"
            "## NORMA Oficial Mexicana NOM-042-NUCL-2026, Categorización de sustancias fisionables\n"
            "\n"
            "Cuerpo de la norma, excluir.\n"
        )
        titulo = "Acuerdo por el que se establecen acciones de regularización"
        siguiente = "Norma Oficial Mexicana NOM-042-NUCL-2026, Categorización de sustancias fisionables"

        cut = cut_markdown_by_titles(markdown, titulo, siguiente)

        self.assertTrue(cut.rstrip().endswith("Rúbrica."))
        self.assertNotIn("SECRETARIA DE ENERGIA", cut)
        self.assertNotIn("NOM-042-NUCL", cut)

    def test_no_next_title_keeps_to_end(self):
        cut = cut_markdown_by_titles(self.markdown, self.titulo, None)

        self.assertTrue(cut.startswith("## AVISO de deslinde del predio SUP 033"))
        self.assertIn("SUP 036", cut)
        self.assertIn("siguiente nota", cut)
        self.assertNotIn("nota anterior", cut)

    def test_matches_despite_ocr_noise_and_accents(self):
        # OCR flattens accents/case and drops the trailing period.
        markdown = (
            "cola de la anterior\n\n"
            "AVISO DE DESLINDE DEL PREDIO SUP 033 SUPERFICIE 31.51 METROS PALENQUE CHIS\n\n"
            "contenido de interes\n"
        )
        cut = cut_markdown_by_titles(markdown, self.titulo, None)
        self.assertTrue(cut.startswith("AVISO DE DESLINDE DEL PREDIO SUP 033"))
        self.assertNotIn("cola de la anterior", cut)

    def test_low_confidence_start_falls_back_to_full_text(self):
        markdown = "contenido totalmente ajeno sin ningún parecido al título buscado aquí.\n"
        cut = cut_markdown_by_titles(
            markdown, "Un título que no aparece en absoluto", None, min_confidence=0.9
        )
        self.assertEqual(cut, markdown.strip())

    def test_empty_markdown_returns_empty(self):
        self.assertEqual(cut_markdown_by_titles("", self.titulo, self.siguiente), "")


if __name__ == "__main__":
    unittest.main()
