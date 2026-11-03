import unittest

import scjn.text as text


class TestRatioSimilitudYGuardas(unittest.TestCase):
    def test_es_acuerdo_interno_detecta_un_acuerdo_general_del_pleno(self):
        self.assertTrue(
            text.es_acuerdo_interno(
                "ACUERDO GENERAL NÚMERO 3/2018 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA "
                "DE LA NACIÓN"
            )
        )

    def test_es_acuerdo_interno_no_marca_una_ley_cualquiera(self):
        self.assertFalse(text.es_acuerdo_interno("LEY FEDERAL DEL TRABAJO"))

    def test_ratio_similitud_ignora_el_sufijo_de_nombre_anterior(self):
        titulo = (
            "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO FEDERAL EN "
            "MATERIA COMUN Y PARA TODA LA REPUBLICA EN MATERIA FEDERAL -"
        )
        self.assertEqual(text.ratio_similitud(titulo, "Código Civil Federal"), 1.0)

    def test_grupo_instrumento_reconoce_ley_codigo_y_reglamento(self):
        self.assertEqual(text.grupo_instrumento("LEY FEDERAL DEL TRABAJO"), "ley")
        self.assertEqual(text.grupo_instrumento("CÓDIGO Civil Federal"), "ley")
        self.assertEqual(
            text.grupo_instrumento("REGLAMENTO DE LA LEY ADUANERA"), "reglamento"
        )
        self.assertIsNone(text.grupo_instrumento("Convenio 107 OIT"))


class TestQuitaNotasEditoriales(unittest.TestCase):
    #: `(what the case pins, paragraph, what is left of it)` -- one case per shape of editorial note
    #: the SCJN prints (issue #305: a table instead of one test per case).
    QUITA = (
        ("a note embedded in a reform annotation",
         "(REFORMADO [N. DE E. ESTE PÁRRAFO], D.O.F. 19 DE DICIEMBRE DE 2017)",
         "(REFORMADO, D.O.F. 19 DE DICIEMBRE DE 2017)"),
        ("the `N. DE . E.` variant",
         "(ADICIONADA [N. DE . E. REUBICADA], D.O.F. 15 DE JUNIO DE 2007)",
         "(ADICIONADA, D.O.F. 15 DE JUNIO DE 2007)"),
        ("a paragraph that is only an editorial note",
         '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE REFORMA".]', ""),
        ("a paragraph with the `NOTA n` marker",
         "[NOTA 1. DE CONFORMIDAD CON EL ACUERDO EMITIDO POR EL CONSEJO.]", ""),
        ("an all-capitals bracket with no marker",
         "(REUBICADO [ANTES ARTICULO 57], D.O.F. 23 DE ENERO DE 2004)",
         "(REUBICADO, D.O.F. 23 DE ENERO DE 2004)"),
        ("a loose marker after an annotation already closed",
         "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013) N. DE E. SÓLO EN "
         "CUANTO AL CONTENIDO, PORQUE DEL ANÁLISIS DEL TEXTO ORIGINAL "
         "PUBLICADO EL 2 DE AGOSTO DE 2006, SE APRECIA LA EXISTENCIA DE "
         "ESTA FRACCIÓN.",
         "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013)"),
        ("a loose marker embedded before the annotation resumes",
         "(REFORMADO N. DE E. ESTE PÁRRAFO, D.O.F. 21 DE FEBRERO DE 2018)",
         "(REFORMADO, D.O.F. 21 DE FEBRERO DE 2018)"),
        ("a paragraph wrapped in parentheses with no bracket marker",
         "(NOTA 1: EL PLENO DE LA SUPREMA CORTE DECLARÓ LA INVALIDEZ.)", ""),
        ("a parenthesised note after real text in the same paragraph",
         "Artículo 58.- La música oficial del Himno Nacional es la "
         "siguiente: (N. DE E., VÉASE D.O.F. 8 DE FEBRERO DE 1984)",
         "Artículo 58.- La música oficial del Himno Nacional es la siguiente:"),
        ("the bold of an already formatted paragraph is kept",
         "**(REFORMADA [N. DE E. ADICIONADA], D.O.F. 15 DE ENERO DE 2026)**",
         "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**"),
    )

    #: Paragraphs that must come back untouched.
    INTACTOS = (
        ("a tariff formula between brackets", "El resultado de la fórmula [(S/365)+V * (I + D)] se aplica."),
        ("a chemical name between brackets", "Se entiende por [4-nitro-3-(trifluorometil)fenilo] la substancia."),
        ("`Nota 1` cited as a tariff note", "Mezclas previstas en la Nota 1 b) de este Capítulo."),
        ("an already clean paragraph", "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**"),
    )

    def test_quita_cada_forma_de_nota_editorial(self):
        for caso, parrafo, esperado in self.QUITA:
            with self.subTest(caso):
                self.assertEqual(text.quita_notas_editoriales(parrafo), esperado)

    def test_no_toca_lo_que_no_es_una_nota_editorial(self):
        for caso, parrafo in self.INTACTOS:
            with self.subTest(caso):
                self.assertEqual(text.quita_notas_editoriales(parrafo), parrafo)
