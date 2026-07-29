import unittest

from nota2md.texto_vigente import limpia_texto_ley


class TestLimpiaTextoLey(unittest.TestCase):
    def test_quita_encabezado_pie_de_pagina_y_portada_de_diputados(self):
        paginas = [
            "\nLEY DE PRUEBA\n\nCÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN\n"
            "Secretaría General\nSecretaría de Servicios Parlamentarios\n"
            "Última Reforma DOF 01-01-2020\n\n1 de 2\nLEY DE PRUEBA\n\n"
            "Nueva Ley publicada en el Diario Oficial de la Federación el 1 de enero de 2019\n\n"
            "TEXTO VIGENTE\nÚltima reforma publicada DOF 01-01-2020\n\n"
            "Al margen un sello.\n\nArtículo 1. Texto del artículo.\n",
            "\nLEY DE PRUEBA\n\nCÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN\n"
            "Secretaría General\nSecretaría de Servicios Parlamentarios\n"
            "Última Reforma DOF 01-01-2020\n\n2 de 2\nARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA\n\n"
            "DECRETO por el que se reforma...\n",
        ]

        texto = limpia_texto_ley(paginas)

        self.assertIn("## Al margen un sello.", texto)
        self.assertIn("**Artículo 1.** Texto del artículo.", texto)
        self.assertNotIn("CÁMARA DE DIPUTADOS", texto)
        self.assertNotIn("TEXTO VIGENTE", texto)
        self.assertNotIn("ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA", texto)
        self.assertNotIn("DECRETO por el que se reforma", texto)

    def test_reflows_paragraphs_and_bolds_headings_and_list_markers(self):
        paginas = [
            "\n1 de 1\nAl margen un sello.\n\n"
            "DECRETO\n\n"
            "Artículo 1. Texto del\nartículo envuelto en dos líneas.\n\n"
            "I. Primera fracción.\n\n"
            "a) Primer inciso.\n\n"
            "Transitorios\n\n"
            "Primero. Entra en vigor de inmediato.\n",
        ]

        texto = limpia_texto_ley(paginas)

        self.assertEqual(
            texto,
            "## Al margen un sello.\n\n"
            "**DECRETO**\n\n"
            "**Artículo 1.** Texto del artículo envuelto en dos líneas.\n\n"
            "**I.** Primera fracción.\n\n"
            "**a)** Primer inciso.\n\n"
            "## Transitorios\n\n"
            "**Primero.** Entra en vigor de inmediato.\n",
        )
