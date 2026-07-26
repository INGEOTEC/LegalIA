import unittest
from unittest.mock import Mock, patch

from leyesmx import diputados


def tabla(*filas: str) -> str:
    return "<table>" + "".join(filas) + "</table>"


def fila(no: str, decreto: str, fecha: str) -> str:
    return (f"<tr><td><b>{no}</b></td><td><font>{decreto}</font></td>"
            f"<td>{fecha}</td><td>PDF Word</td></tr>")


ENCABEZADO = "<tr><td><b>No.</b></td><td><b>Decreto</b></td><td><b>Fecha</b></td></tr>"


class TestPaginaDeReformas(unittest.TestCase):
    def test_cpeum_usa_su_pagina_cronologica(self):
        self.assertTrue(
            diputados.pagina_de_reformas("cpeum").endswith("ref/cpeum_crono.htm"))

    def test_otras_leyes_usan_ref_abreviatura(self):
        self.assertTrue(diputados.pagina_de_reformas("lft").endswith("ref/lft.htm"))


class TestParseReformas(unittest.TestCase):
    def test_extrae_numero_fecha_y_decreto(self):
        html = tabla(ENCABEZADO,
                     fila("2", "DECRETO que reforma el art&iacute;culo 45", "22/03/1934"))

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertEqual(r.no, 2)
        self.assertEqual(r.fecha, "22-03-1934")            # DD-MM-YYYY, como dofjson
        self.assertEqual(r.decreto, "DECRETO que reforma el artículo 45")
        self.assertEqual(r.ley, "cpeum")

    def test_omite_encabezado_y_filas_sin_fecha(self):
        html = tabla(ENCABEZADO,
                     "<tr><td>decoración</td></tr>",
                     fila("1", "DECRETO que reforma algo importante", "08/07/1921"))

        self.assertEqual(len(diputados.parse_reformas(html)), 1)

    def test_descarta_el_resumen_editorial_del_decreto(self):
        """Diputados agrega tras 'Nota:' un resumen que no es parte del título."""
        html = tabla(fila("3", "DECRETO que reforma el 82 y 83. Nota: Establece la "
                               "no reelecci&oacute;n absoluta.", "22/01/1927"))

        r, = diputados.parse_reformas(html)

        self.assertEqual(r.decreto, "DECRETO que reforma el 82 y 83.")

    def test_ordena_de_la_mas_antigua_a_la_mas_reciente(self):
        html = tabla(fila("2", "DECRETO segundo con texto suficiente", "24/11/1923"),
                     fila("1", "DECRETO primero con texto suficiente", "08/07/1921"))

        fechas = [r.fecha for r in diputados.parse_reformas(html)]

        self.assertEqual(fechas, ["08-07-1921", "24-11-1923"])

    def test_la_publicacion_original_no_lleva_numero(self):
        """La primera fila de la CPEUM es el texto de 1917, no una reforma."""
        html = tabla("<tr><td></td><td><font>CONSTITUCIÓN Política de los Estados "
                     "Unidos Mexicanos, que reforma la de 1857</font></td>"
                     "<td>05/02/1917</td></tr>",
                     fila("1", "DECRETO reformando el 14 transitorio", "08/07/1921"))

        original, primera = diputados.parse_reformas(html)

        self.assertIsNone(original.no)
        self.assertEqual(original.fecha, "05-02-1917")
        self.assertEqual(primera.no, 1)


class TestDescarga(unittest.TestCase):
    @patch("leyesmx.diputados.requests.get")
    def test_decodifica_el_cp1252_de_leyesbiblio(self, mock_get):
        """LeyesBiblio sirve Windows-1252 sin declararlo siempre."""
        mock_get.return_value = Mock(
            content="<td>artículo 45 constitucional</td>".encode("cp1252"),
            raise_for_status=Mock())

        html = diputados.descarga("cpeum")

        self.assertIn("artículo 45 constitucional", html)


if __name__ == "__main__":
    unittest.main()
