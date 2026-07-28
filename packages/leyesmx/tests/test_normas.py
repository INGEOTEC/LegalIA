import unittest

from leyesmx import normas


def nota(codNota, fecha, titulo):
    return {"codNota": codNota, "fecha": fecha, "titulo": titulo}


class TestCodigosDelTitulo(unittest.TestCase):
    def test_lee_el_codigo_moderno(self):
        c = normas.codigos_del_titulo(
            "NORMA Oficial Mexicana NOM-007-SSA1-1993, Atención de la mujer")

        self.assertEqual(c, {"NOM-007-SSA1-1993"})

    def test_un_proyecto_se_atribuye_a_la_norma_que_proyecta(self):
        c = normas.codigos_del_titulo(
            "Proyecto de Norma Oficial Mexicana PROY-NOM-001-SCFI-2017, Aparatos")

        self.assertEqual(c, {"NOM-001-SCFI-2017"})

    def test_lee_las_generaciones_antiguas_de_codigo(self):
        """Sesenta años de gaceta han dejado 253 formas distintas; descomponer
        el código en número, dependencia y año malinterpreta cientos de ellas."""
        for titulo, esperado in [
            ("NORMA Oficial Mexicana NOM-150-1979 Método de prueba", "NOM-150-1979"),
            ("DECLARATORIA de vigencia de la norma NOM-C-247-1978", "NOM-C-247-1978"),
            ("NORMA Oficial Mexicana NOM-EM-002-SSA2-1993", "NOM-EM-002-SSA2-1993"),
            ("NORMA Oficial Mexicana NOM-015-SCT-2-1993, para el", "NOM-015-SCT-2-1993"),
        ]:
            with self.subTest(titulo=titulo):
                self.assertEqual(normas.codigos_del_titulo(titulo), {esperado})

    def test_una_nota_puede_citar_varias_normas(self):
        """Es común y correcto: la nota que expide una revisión suele cancelar
        la edición que sustituye."""
        c = normas.codigos_del_titulo(
            "ACLARACIONES a las Normas Oficiales Mexicanas NOM-001-EDIF-1994 y "
            "NOM-002-EDIF-1994")

        self.assertEqual(c, {"NOM-001-EDIF-1994", "NOM-002-EDIF-1994"})

    def test_ignora_un_titulo_sin_codigo(self):
        self.assertEqual(normas.codigos_del_titulo("DECRETO por el que se reforma"), set())


class TestAgrupa(unittest.TestCase):
    def test_una_nota_entra_en_cada_norma_que_cita(self):
        notas = [nota(1, "01-01-2000", "ACLARACION a las NOM-001-AAA-1999 y NOM-002-AAA-1999")]

        g = normas.agrupa(notas)

        self.assertEqual(sorted(g), ["NOM-001-AAA-1999", "NOM-002-AAA-1999"])


class TestCitasParciales(unittest.TestCase):
    def test_pliega_una_cita_corta_en_su_unica_extension(self):
        """Los títulos citan a menudo por parte del código: `NOM-186-SSA1` por
        `NOM-186-SSA1-2000`. Como clave propia parecería un instrumento sin
        serlo, y sus notas faltarían en el que sí lo es."""
        g = {
            "NOM-186-SSA1": [nota(1, "01-01-2001", "Aclaración a la NOM-186-SSA1")],
            "NOM-186-SSA1-2000": [nota(2, "01-01-2000", "NORMA Oficial Mexicana NOM-186-SSA1-2000")],
        }

        inst, amb = normas.resuelve_citas_parciales(g)

        self.assertEqual(sorted(inst), ["NOM-186-SSA1-2000"])
        self.assertEqual(amb, {})
        self.assertEqual(sorted(n["codNota"] for n in inst["NOM-186-SSA1-2000"]), [1, 2])

    def test_deja_aparte_una_cita_que_admite_varias_normas(self):
        """`NOM-021` lo mismo puede ser la de ASEA, la de SAG o la de SCT4; no
        hay con qué decidir, así que no se adivina."""
        g = {
            "NOM-021": [nota(1, "01-01-2020", "Aviso sobre la NOM-021")],
            "NOM-021-SAG-2017": [nota(2, "01-01-2017", "NORMA Oficial Mexicana NOM-021-SAG-2017")],
            "NOM-021-SCT4-1995": [nota(3, "01-01-1995", "NORMA Oficial Mexicana NOM-021-SCT4-1995")],
        }

        inst, amb = normas.resuelve_citas_parciales(g)

        self.assertEqual(sorted(inst), ["NOM-021-SAG-2017", "NOM-021-SCT4-1995"])
        self.assertEqual(sorted(amb), ["NOM-021"])

    def test_no_duplica_una_nota_que_cita_el_codigo_corto_y_el_largo(self):
        completa = nota(1, "01-01-2000", "NORMA NOM-186-SSA1-2000 y la NOM-186-SSA1")
        g = {"NOM-186-SSA1": [completa], "NOM-186-SSA1-2000": [completa]}

        inst, _ = normas.resuelve_citas_parciales(g)

        self.assertEqual(normas.historia(inst["NOM-186-SSA1-2000"]), [1])


class TestHistoria(unittest.TestCase):
    def test_ordena_de_lo_mas_antiguo_a_lo_mas_reciente(self):
        notas = [nota(3, "13-10-1993", "NORMA"), nota(1, "03-05-1993", "PROYECTO"),
                 nota(2, "11-10-1993", "RESPUESTA")]

        self.assertEqual(normas.historia(notas), [1, 2, 3])

    def test_ordena_por_año_no_alfabeticamente(self):
        notas = [nota(2, "01-01-2010", "b"), nota(1, "31-12-1999", "a")]

        self.assertEqual(normas.historia(notas), [1, 2])


class TestCatalogo(unittest.TestCase):
    def test_etiqueta_con_la_publicacion_definitiva(self):
        """La nota más reciente es a menudo un aviso de consulta pública, que
        no dice nada de la materia que regula la norma."""
        notas = [
            nota(1, "13-10-1993", "NORMA Oficial Mexicana NOM-001-SCFI-1993, aparatos electrónicos"),
            nota(2, "05-12-2025", "Aviso de consulta pública del Proyecto de Modificación"),
        ]

        fila = normas.catalogo({"NOM-001-SCFI-1993": notas})[0]

        self.assertIn("aparatos electrónicos", fila["titulo"])
        self.assertEqual((fila["desde"], fila["hasta"]), ("13-10-1993", "05-12-2025"))
        self.assertEqual(fila["notas"], 2)

    def test_usa_la_ultima_nota_si_ninguna_es_la_norma_misma(self):
        notas = [nota(1, "01-01-2020", "Aviso sobre la NOM-999-XXX-2020")]

        fila = normas.catalogo({"NOM-999-XXX-2020": notas})[0]

        self.assertTrue(fila["titulo"].startswith("Aviso"))


if __name__ == "__main__":
    unittest.main()
