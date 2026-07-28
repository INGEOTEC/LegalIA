import unittest

from leyesmx import dof
from leyesmx.diputados import Reforma


def nota(codNota, titulo, fecha):
    return {"codNota": codNota, "titulo": titulo, "fecha": fecha}


class TestNormaliza(unittest.TestCase):
    def test_quita_acentos_mayusculas_y_puntuacion(self):
        self.assertEqual(dof.normaliza("DECRETO que reforma el artículo 45."),
                         "decreto que reforma el articulo 45")


class TestSimilitud(unittest.TestCase):
    def test_es_1_cuando_el_titulo_del_dof_esta_dentro_del_decreto(self):
        """Diputados añade un resumen; el título del DOF es su prefijo."""
        decreto = ("DECRETO que adiciona el artículo 45 constitucional. Determina "
                   "las islas y cayos sujetos a la jurisdicción de Yucatán.")
        dof_titulo = "DECRETO que adiciona el artículo 45 constitucional"

        self.assertEqual(dof.similitud(decreto, dof_titulo), 1.0)

    def test_tolera_variantes_de_redaccion(self):
        s = dof.similitud(
            "DECRETO por lo cual se reforma la fracción I del artículo 104 de la Constitución",
            "DECRETO que reforma la fracción I del artículo 104 de la Constitución")
        self.assertGreater(s, 0.80)

    def test_es_baja_entre_decretos_distintos(self):
        s = dof.similitud("DECRETO que reforma el artículo 45 constitucional",
                          "ACUERDO por el que se dan a conocer las cuotas de peaje")
        self.assertLess(s, 0.5)

    def test_titulo_vacio_no_coincide(self):
        self.assertEqual(dof.similitud("DECRETO que reforma algo", ""), 0.0)


class TestEnlaza(unittest.TestCase):
    def test_elige_la_nota_correcta_entre_las_del_mismo_dia(self):
        reformas = [Reforma(no=19, fecha="22-03-1934",
                            decreto="DECRETO que adiciona el artículo 45 constitucional. "
                                    "Determina las islas y cayos.", ley="cpeum")]
        notas = [
            nota(1, "ACUERDO sobre tarifas ferroviarias", "22-03-1934"),
            nota(2, "DECRETO que adiciona el artículo 45 constitucional", "22-03-1934"),
            nota(3, "AVISO de la Secretaría de Hacienda", "22-03-1934"),
        ]

        e, = dof.enlaza(reformas, notas)

        self.assertEqual(e.codNota, 2)
        self.assertEqual(e.confianza, 1.0)
        self.assertTrue(e.enlazada)

    def test_ignora_notas_de_otras_fechas(self):
        reformas = [Reforma(no=1, fecha="08-07-1921",
                            decreto="DECRETO reformando el artículo 14 transitorio")]
        notas = [nota(9, "DECRETO reformando el artículo 14 transitorio", "09-07-1921")]

        e, = dof.enlaza(reformas, notas)

        self.assertIsNone(e.codNota)

    def test_conserva_la_reforma_cuando_el_dof_no_tiene_la_nota(self):
        """El 08-03-1999 falta en el servicio del DOF: es un hecho de la
        fuente, así que la reforma se reporta sin codNota en lugar de omitirse."""
        reformas = [Reforma(no=139, fecha="08-03-1999",
                            decreto="DECRETO por el que se declaran reformados los "
                                    "artículos 16, 19, 22 y 123")]

        e, = dof.enlaza(reformas, [])

        self.assertFalse(e.enlazada)
        self.assertIsNone(e.codNota)
        self.assertEqual(e.no, 139)
        self.assertEqual(e.confianza, 0.0)

    def test_no_reparte_la_misma_nota_a_dos_reformas(self):
        """El 17-05-2021 se publicaron dos reformas al artículo 43 que sólo se
        distinguen por el paréntesis final; cada una tiene su propia nota."""
        reformas = [
            Reforma(no=248, fecha="17-05-2021",
                    decreto="DECRETO por el que se reforma el artículo 43 de la "
                            "Constitución Política de los Estados Unidos Mexicanos "
                            "(Michoacán de Ocampo)"),
            Reforma(no=249, fecha="17-05-2021",
                    decreto="DECRETO por el que se reforma el artículo 43 de la "
                            "Constitución Política de los Estados Unidos Mexicanos "
                            "(Veracruz de Ignacio de la Llave)"),
        ]
        notas = [
            nota(100, "Decreto por el que se reforma el artículo 43 de la Constitución "
                      "Política de los Estados Unidos Mexicanos (Michoacán de Ocampo)",
                 "17-05-2021"),
            nota(101, "Decreto por el que se reforma el artículo 43 de la Constitución "
                      "Política de los Estados Unidos Mexicanos (Veracruz de Ignacio "
                      "de la Llave)", "17-05-2021"),
        ]

        a, b = dof.enlaza(reformas, notas)

        self.assertEqual((a.codNota, b.codNota), (100, 101))

    def test_dos_reformas_el_mismo_dia_reciben_notas_distintas(self):
        """El 02-06-2026 se publicaron dos reformas constitucionales."""
        reformas = [
            Reforma(no=283, fecha="02-06-2026",
                    decreto="DECRETO por el que se reforman y adicionan diversas "
                            "disposiciones en materia de reforma al Poder Judicial"),
            Reforma(no=284, fecha="02-06-2026",
                    decreto="DECRETO por el que se adiciona un inciso a la base VI "
                            "del artículo 41, nueva causal de nulidad"),
        ]
        notas = [
            nota(10, "DECRETO por el que se reforman y adicionan diversas disposiciones "
                     "en materia de reforma al Poder Judicial", "02-06-2026"),
            nota(11, "DECRETO por el que se adiciona un inciso a la base VI del "
                     "artículo 41, nueva causal de nulidad", "02-06-2026"),
        ]

        a, b = dof.enlaza(reformas, notas)

        self.assertEqual((a.codNota, b.codNota), (10, 11))


if __name__ == "__main__":
    unittest.main()


class TestSimilitudNombre(unittest.TestCase):
    """Para los reglamentos no hay título de decreto que comparar: LeyesBiblio
    sólo da el nombre del reglamento, así que la pregunta es la inversa —
    ¿este título del DOF nombra a este instrumento?"""

    def test_uno_cuando_el_titulo_contiene_el_nombre(self):
        s = dof.similitud_nombre(
            "REGLAMENTO de la Ley de Aguas Nacionales",
            "DECRETO que reforma el Reglamento de la Ley de Aguas Nacionales.",
        )

        self.assertEqual(s, 1.0)

    def test_cero_cuando_el_titulo_no_tiene_nada_que_ver(self):
        """El caso que delató la métrica anterior: la reforma de 08-08-2000 al
        Reglamento de la Ley de Aeropuertos quedaba ligada a esta nota."""
        s = dof.similitud_nombre(
            "REGLAMENTO de la Ley de Aeropuertos",
            "Relación de declaratorias de libertad de terreno número 63/2000",
        )

        self.assertEqual(s, 0.0)

    def test_ignora_las_palabras_demasiado_cortas(self):
        """"de" y "la" aparecen en casi cualquier título; contarlas inflaría
        el parecido de notas sin relación."""
        s = dof.similitud_nombre("LEY de Minería", "ACUERDO por el que se de la")

        self.assertEqual(s, 0.0)


class TestPuntuaEntrada(unittest.TestCase):
    def test_una_reforma_numerada_se_compara_por_el_titulo_del_decreto(self):
        reforma = Reforma(no=5, fecha="01-01-2020",
                          decreto="DECRETO por el que se reforma el artículo 1")

        s = dof.puntua_entrada(reforma, "DECRETO por el que se reforma el artículo 1")

        self.assertEqual(s, 1.0)

    def test_una_publicacion_original_se_compara_por_el_nombre(self):
        """`similitud` esperaría que el título del DOF fuera prefijo de un texto
        más largo; con sólo el nombre eso da puntajes altos a notas ajenas —
        ligó la Ley Federal del Trabajo de 1970 a un reglamento de tránsito."""
        original = Reforma(no=None, fecha="01-04-1970",
                           decreto="LEY Federal del Trabajo")

        acertado = dof.puntua_entrada(original, "LEY Federal del Trabajo.")
        ajeno = dof.puntua_entrada(
            original, "DECRETO que reforma el Reglamento de Tránsito en el D.F.")

        self.assertEqual(acertado, 1.0)
        self.assertLess(ajeno, 0.6)


class TestMinimoPorNombre(unittest.TestCase):
    def test_no_enlaza_por_debajo_del_minimo(self):
        """Un día cargado trae cien notas; que coincida la mitad de las
        palabras es tan probable por azar como por acierto, y dejar la entrada
        sin enlazar dice menos que enlazarla mal."""
        original = Reforma(no=None, fecha="01-01-2020", decreto="LEY de Minería")
        porf = {"01-01-2020": [{"codNota": 1, "titulo": "ACUERDO sobre otra cosa"}]}

        enlazadas = dof.enlaza_agrupadas([original], porf)

        self.assertFalse(enlazadas[0].enlazada)

    def test_una_reforma_numerada_no_esta_sujeta_a_ese_minimo(self):
        reforma = Reforma(no=1, fecha="01-01-2020", decreto="DECRETO que reforma algo")
        porf = {"01-01-2020": [{"codNota": 7, "titulo": "DECRETO que reforma algo"}]}

        enlazadas = dof.enlaza_agrupadas([reforma], porf)

        self.assertEqual(enlazadas[0].codNota, 7)

    def test_por_nombre_aplica_el_minimo_a_todas_las_entradas(self):
        """Modo de los reglamentos: ninguna entrada trae título de decreto."""
        reforma = Reforma(no=1, fecha="01-01-2020",
                          decreto="REGLAMENTO de la Ley de Aeropuertos")
        porf = {"01-01-2020": [{"codNota": 9, "titulo": "Relación de declaratorias"}]}

        enlazadas = dof.enlaza_agrupadas([reforma], porf, por_nombre=True)

        self.assertFalse(enlazadas[0].enlazada)
