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
