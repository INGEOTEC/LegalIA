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
    def test_quita_la_nota_embebida_dentro_de_una_anotacion_de_reforma(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(REFORMADO [N. DE E. ESTE PÁRRAFO], D.O.F. 19 DE DICIEMBRE DE 2017)"
            ),
            "(REFORMADO, D.O.F. 19 DE DICIEMBRE DE 2017)",
        )

    def test_quita_la_nota_embebida_con_variante_n_de_punto_e(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(ADICIONADA [N. DE . E. REUBICADA], D.O.F. 15 DE JUNIO DE 2007)"
            ),
            "(ADICIONADA, D.O.F. 15 DE JUNIO DE 2007)",
        )

    def test_deja_vacio_un_parrafo_que_es_enteramente_nota_editorial(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE REFORMA".]'
            ),
            "",
        )

    def test_deja_vacio_un_parrafo_con_el_marcador_nota_n(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "[NOTA 1. DE CONFORMIDAD CON EL ACUERDO EMITIDO POR EL CONSEJO.]"
            ),
            "",
        )

    def test_quita_un_corchete_sin_marcador_pero_todo_en_mayusculas(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(REUBICADO [ANTES ARTICULO 57], D.O.F. 23 DE ENERO DE 2004)"
            ),
            "(REUBICADO, D.O.F. 23 DE ENERO DE 2004)",
        )

    def test_no_toca_una_formula_arancelaria_entre_corchetes(self):
        parrafo = "El resultado de la fórmula [(S/365)+V * (I + D)] se aplica."
        self.assertEqual(text.quita_notas_editoriales(parrafo), parrafo)

    def test_no_toca_un_nombre_quimico_entre_corchetes(self):
        parrafo = "Se entiende por [4-nitro-3-(trifluorometil)fenilo] la substancia."
        self.assertEqual(text.quita_notas_editoriales(parrafo), parrafo)

    def test_no_confunde_la_nota_n_con_una_cita_de_nota_arancelaria(self):
        parrafo = "Mezclas previstas en la Nota 1 b) de este Capítulo."
        self.assertEqual(text.quita_notas_editoriales(parrafo), parrafo)

    def test_quita_el_marcador_suelto_que_sigue_a_una_anotacion_ya_cerrada(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013) N. DE E. SÓLO EN "
                "CUANTO AL CONTENIDO, PORQUE DEL ANÁLISIS DEL TEXTO ORIGINAL "
                "PUBLICADO EL 2 DE AGOSTO DE 2006, SE APRECIA LA EXISTENCIA DE "
                "ESTA FRACCIÓN."
            ),
            "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013)",
        )

    def test_quita_el_marcador_suelto_embebido_antes_de_que_la_anotacion_reanude(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(REFORMADO N. DE E. ESTE PÁRRAFO, D.O.F. 21 DE FEBRERO DE 2018)"
            ),
            "(REFORMADO, D.O.F. 21 DE FEBRERO DE 2018)",
        )

    def test_deja_vacio_un_parrafo_envuelto_en_parentesis_sin_marcador_de_corchete(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "(NOTA 1: EL PLENO DE LA SUPREMA CORTE DECLARÓ LA INVALIDEZ.)"
            ),
            "",
        )

    def test_quita_la_nota_entre_parentesis_que_sigue_a_texto_real_en_el_mismo_parrafo(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "Artículo 58.- La música oficial del Himno Nacional es la "
                "siguiente: (N. DE E., VÉASE D.O.F. 8 DE FEBRERO DE 1984)"
            ),
            "Artículo 58.- La música oficial del Himno Nacional es la siguiente:",
        )

    def test_conserva_la_negrita_de_un_parrafo_ya_formateado_al_limpiarlo(self):
        self.assertEqual(
            text.quita_notas_editoriales(
                "**(REFORMADA [N. DE E. ADICIONADA], D.O.F. 15 DE ENERO DE 2026)**"
            ),
            "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**",
        )

    def test_es_un_no_op_sobre_un_parrafo_ya_limpio(self):
        parrafo = "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**"
        self.assertEqual(text.quita_notas_editoriales(parrafo), parrafo)
