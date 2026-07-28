import unittest

from leyesmx import tratados


def nota(codNota, fecha, titulo):
    return {"codNota": codNota, "fecha": fecha, "titulo": titulo}


APR_COREA = ("DECRETO por el que se aprueba el Convenio entre los Estados Unidos "
             "Mexicanos y la República de Corea, para evitar la Doble Imposición")
PROM_COREA = ("DECRETO de promulgación del Convenio entre los Estados Unidos "
              "Mexicanos y la República de Corea para Evitar la Doble Imposición")


class TestClasifica(unittest.TestCase):
    def test_reconoce_la_promulgacion_y_le_quita_la_fórmula(self):
        tipo, nombre = tratados.clasifica(PROM_COREA)

        self.assertEqual(tipo, tratados.PROMULGACION)
        self.assertTrue(nombre.startswith("Convenio entre"))

    def test_reconoce_la_aprobacion(self):
        tipo, nombre = tratados.clasifica(APR_COREA)

        self.assertEqual(tipo, tratados.APROBACION)
        self.assertTrue(nombre.startswith("Convenio entre"))

    def test_una_aprobacion_que_no_es_de_un_instrumento_internacional(self):
        """El Senado aprueba otras cosas; sin nombrar un tratado, un convenio o
        similar, el decreto no entra."""
        self.assertIsNone(
            tratados.clasifica("DECRETO por el que se aprueba el Plan Nacional de Desarrollo"))

    def test_ignora_un_decreto_cualquiera(self):
        self.assertIsNone(
            tratados.clasifica("DECRETO por el que se reforma la Ley Aduanera"))


class TestPesos(unittest.TestCase):
    def test_pesa_menos_lo_que_aparece_en_todos_los_nombres(self):
        p = tratados.Pesos([
            "convenio entre los estados unidos mexicanos y corea",
            "convenio entre los estados unidos mexicanos y noruega",
            "convenio entre los estados unidos mexicanos y austria",
        ])

        self.assertLess(p.peso("convenio"), p.peso("corea"))

    def test_distingue_dos_tratados_con_la_misma_formula(self):
        """El parecido de cadena completa daba 0.88 a un par falso, por encima
        de lo que daba a pares reales: la fórmula compartida lo domina."""
        formula = ("acuerdo entre el gobierno de los estados unidos mexicanos y "
                   "el gobierno de la republica de ")
        p = tratados.Pesos([formula + pais for pais in
                            ("corea", "noruega", "austria", "india", "gabon")])

        mismo = p.similitud(formula + "corea", formula + "corea")
        distinto = p.similitud(formula + "corea", formula + "gabon")

        self.assertEqual(mismo, 1.0)
        self.assertLess(distinto, mismo)


class TestEmpareja(unittest.TestCase):
    def test_agrupa_los_nombres_identicos_como_ciertos(self):
        mismo = "Tratado de Libre Comercio de América del Norte"
        ds = tratados.decretos([
            nota(1, "08-12-1993", f"DECRETO de Promulgación del {mismo}"),
            nota(2, "08-12-1993", f"DECRETO por el que se aprueba el {mismo}"),
        ])

        gs = tratados.empareja(ds)

        self.assertEqual(len(gs), 1)
        self.assertEqual(gs[0]["certeza"], "exacta")
        self.assertEqual(sorted(tratados.historia(gs[0])), [1, 2])

    def test_empareja_dos_redacciones_del_mismo_tratado(self):
        """Caso real: el mismo acuerdo escribe el año en cifras en un decreto y
        en palabras en el otro, así que los nombres no coinciden literalmente."""
        ds = tratados.decretos([
            nota(1, "28-01-2010", "DECRETO por el que se aprueba el Acuerdo "
                 "Internacional del Café de 2007, adoptado en Londres el "
                 "veintiocho de septiembre de 2007"),
            nota(2, "07-04-2010", "DECRETO Promulgatorio del Acuerdo "
                 "Internacional del Café de 2007, adoptado en Londres el "
                 "veintiocho de septiembre de dos mil siete"),
        ])

        gs = tratados.empareja(ds)

        self.assertEqual(len(gs), 1)
        self.assertIsInstance(gs[0]["certeza"], float)
        self.assertEqual(tratados.historia(gs[0]), [1, 2])

    def test_no_empareja_una_promulgacion_anterior_a_su_aprobacion(self):
        # Redactada distinto, para que llegue al emparejamiento: dos decretos de
        # nombre idéntico se agrupan por el nombre, sin mirar el orden.
        prom_antes = PROM_COREA.replace("para Evitar", "a fin de evitar")
        ds = tratados.decretos([
            nota(1, "10-01-1995", APR_COREA),
            nota(2, "16-03-1990", prom_antes),
        ])

        gs = tratados.empareja(ds)

        self.assertEqual(len(gs), 2)
        self.assertTrue(all(g["certeza"] is None for g in gs))

    def test_un_decreto_solo_es_un_tratado_de_una_nota(self):
        """Publicar los dos decretos es práctica reciente: antes de los ochenta
        la gaceta corría uno solo, así que un tratado con una nota es lo normal
        y no una falla."""
        ds = tratados.decretos([
            nota(1, "31-12-1942", "DECRETO por el que se aprueba el Tratado de "
                                  "Comercio celebrado entre México y los Estados Unidos"),
        ])

        gs = tratados.empareja(ds)

        self.assertEqual(len(gs), 1)
        self.assertIsNone(gs[0]["certeza"])
        self.assertEqual(tratados.historia(gs[0]), [1])

    def test_ninguna_nota_se_pierde(self):
        ds = tratados.decretos([
            nota(1, "10-01-1995", APR_COREA),
            nota(2, "16-03-1995", PROM_COREA),
            nota(3, "31-12-1942", "DECRETO por el que se aprueba el Tratado de Comercio"),
        ])

        gs = tratados.empareja(ds)

        self.assertEqual(sorted(n for g in gs for n in tratados.historia(g)), [1, 2, 3])

    def test_cada_decreto_se_usa_una_sola_vez(self):
        ds = tratados.decretos([
            nota(1, "10-01-1995", APR_COREA),
            nota(2, "16-03-1995", PROM_COREA),
            nota(3, "20-03-1995", PROM_COREA.replace("Corea", "Corea")),
        ])

        gs = tratados.empareja(ds)
        todas = [n for g in gs for n in tratados.historia(g)]

        self.assertEqual(len(todas), len(set(todas)))


class TestCatalogo(unittest.TestCase):
    def test_registra_como_se_agrupo_cada_tratado(self):
        ds = tratados.decretos([
            nota(1, "10-01-1995", APR_COREA), nota(2, "16-03-1995", PROM_COREA),
        ])

        fila = tratados.catalogo(tratados.empareja(ds))[0]

        self.assertEqual((fila["desde"], fila["hasta"]), ("10-01-1995", "16-03-1995"))
        self.assertEqual(fila["notas"], 2)
        self.assertIsNotNone(fila["certeza"])


if __name__ == "__main__":
    unittest.main()
