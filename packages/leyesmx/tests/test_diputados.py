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


class TestPdfDelDecreto(unittest.TestCase):
    def fila_con(self, *hrefs):
        enlaces = "".join(f'<a href="{h}">x</a>' for h in hrefs)
        return tabla(f"<tr><td>139</td><td><font>DECRETO con texto suficiente para "
                     f"pasar el umbral</font></td><td>08/03/1999</td>"
                     f"<td>{enlaces}</td></tr>")

    def test_resuelve_la_url_relativa_contra_leyesbiblio(self):
        html = self.fila_con("dof/CPEUM_ref_139_08mar99.pdf")

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertEqual(
            r.pdf,
            "https://www.diputados.gob.mx/LeyesBiblio/ref/dof/CPEUM_ref_139_08mar99.pdf")

    def test_prefiere_el_pdf_con_texto_sobre_el_escaneo_ima(self):
        html = self.fila_con("dof/CPEUM_ref_139_08mar99_ima.pdf",
                             "dof/CPEUM_ref_139_08mar99.pdf")

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertTrue(r.pdf.endswith("08mar99.pdf"))

    def test_usa_el_escaneo_cuando_es_el_unico(self):
        html = self.fila_con("dof/CPEUM_ref_139_08mar99_ima.pdf")

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertTrue(r.pdf.endswith("_ima.pdf"))

    def test_ignora_enlaces_que_no_son_pdf(self):
        html = self.fila_con("dof/CPEUM_ref_139_08mar99.doc")

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertEqual(r.pdf, "")

    def test_respeta_una_url_absoluta(self):
        html = self.fila_con("https://otro.gob.mx/decreto.pdf")

        r, = diputados.parse_reformas(html, "cpeum")

        self.assertEqual(r.pdf, "https://otro.gob.mx/decreto.pdf")


class TestDescargaDecreto(unittest.TestCase):
    @patch("leyesmx.diputados.requests.get")
    def test_guarda_el_pdf_y_crea_el_directorio(self, mock_get):
        import tempfile
        from pathlib import Path
        mock_get.return_value = Mock(content=b"%PDF-1.4 x", raise_for_status=Mock())
        r = diputados.Reforma(no=139, fecha="08-03-1999", decreto="d",
                              pdf="https://x/CPEUM_ref_139.pdf")
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "nuevo" / "r139.pdf"

            diputados.descarga_decreto(r, destino)

            self.assertEqual(destino.read_bytes(), b"%PDF-1.4 x")

    def test_falla_si_la_reforma_no_trae_pdf(self):
        r = diputados.Reforma(no=1, fecha="08-07-1921", decreto="d", pdf="")

        with self.assertRaises(ValueError):
            diputados.descarga_decreto(r, "x.pdf")


def fila_reforma(no, titulo, fecha_texto, archivo):
    """Una fila de la tabla "Decretos de Reforma" de una ley ordinaria: el
    número en su celda, y el título junto con la fecha en la siguiente."""
    return (
        "<tr>"
        f"<td><p>{no}</p></td>"
        f'<td><p><b>DECRETO</b> {titulo}</p>'
        f'<p>| <a href="lan/{archivo}">{fecha_texto}</a> | '
        f'<a href="lan/{archivo.replace(".pdf", ".doc")}">Word</a> |</p></td>'
        "</tr>"
    )


def pagina_ordinaria(filas, original="01-12-1992"):
    cabeza = (
        f"<p><b>Publicación Original:</b></p><p>| DOF {original} | "
        '<a href="lan/LAN_orig_01dic92_ima.pdf">Imagen</a> |</p>'
        if original else ""
    )
    return (f"<html><body>{cabeza}<p>Decretos de Reforma:</p>"
            f"<table>{''.join(filas)}</table></body></html>")


class TestLeyOrdinaria(unittest.TestCase):
    """Las leyes ordinarias no usan el formato de la tabla cronológica de la
    Constitución: la fecha va dentro de la misma celda que el título."""

    def test_lee_numero_fecha_y_titulo(self):
        html = pagina_ordinaria([
            fila_reforma("11", "por el que se expide la Ley General de Aguas",
                         "DOF 11-12-2025", "LAN_ref11_11dic25.pdf"),
        ])

        rs = diputados.parse_reformas(html, "lan")

        self.assertEqual([r.no for r in rs], [None, 11])
        self.assertEqual(rs[0].fecha, "01-12-1992")
        self.assertEqual(rs[1].fecha, "11-12-2025")
        self.assertIn("Ley General de Aguas", rs[1].decreto)

    def test_acepta_la_fecha_sin_el_prefijo_dof(self):
        """Ambas grafías están en uso, a veces en la misma página."""
        html = pagina_ordinaria([
            fila_reforma("4", "por el que se expide la Ley de Disciplina Financiera",
                         "27-04-2016", "LGCG_ref04_27abr16.pdf"),
        ])

        rs = diputados.parse_reformas(html, "lgcg")

        self.assertEqual(rs[-1].fecha, "27-04-2016")

    def test_toma_la_fecha_del_archivo_cuando_el_texto_esta_mal(self):
        """`lgpgir` escribe "DOF 04-06-214", con el año a tres dígitos; el
        archivo que enlaza dice 04jun14."""
        html = pagina_ordinaria([
            fila_reforma("8", "por el que se reforman diversas disposiciones",
                         "DOF 04-06-214", "LGPGIR_ref08_04jun14.pdf"),
        ])

        rs = diputados.parse_reformas(html, "lgpgir")

        self.assertEqual(rs[-1].fecha, "04-06-2014")

    def test_ignora_lo_que_no_es_una_reforma(self):
        """La misma tabla lleva otros instrumentos, cada uno numerado desde 1:
        tomarlos por reformas es lo que hacía aparecer números duplicados."""
        html = pagina_ordinaria([
            fila_reforma("1", "por el que se reforman diversas disposiciones",
                         "DOF 29-12-2014", "CNPP_ref01_29dic14.pdf"),
            fila_reforma("1", "de entrada en vigor", "DOF 24-09-2014",
                         "CNPP_decla01_24sep14.pdf"),
            fila_reforma("2", "Actualización de cantidades . ANEXOS 4 y 5",
                         "DOF 28-12-2025", "CFF_cant02_28dic25.pdf"),
        ])

        rs = diputados.parse_reformas(html, "cnpp")

        self.assertEqual([r.no for r in rs], [None, 1])

    def test_la_columna_manda_sobre_el_nombre_del_archivo(self):
        """`loapf` enlaza en su reforma 47 un archivo de otra ley
        (`LOPJF_ref25_`); la columna es la que acierta."""
        html = pagina_ordinaria([
            fila_reforma("47", "por el que se reforman diversas disposiciones",
                         "DOF 24-12-2014", "LOPJF_ref25_24dic14.pdf"),
        ])

        rs = diputados.parse_reformas(html, "loapf")

        self.assertEqual(rs[-1].no, 47)

    def test_recupera_el_numero_del_archivo_si_la_columna_esta_vacia(self):
        """`reg_senado` la deja vacía en las reformas 23 a 29."""
        html = pagina_ordinaria([
            fila_reforma("", "por el que se reforman diversos artículos",
                         "DOF 06-12-2024", "REG_SENADO_ref23_06dic24.pdf"),
        ])

        rs = diputados.parse_reformas(html, "reg_senado")

        self.assertEqual(rs[-1].no, 23)

    def test_una_ley_sin_publicacion_original(self):
        html = pagina_ordinaria([
            fila_reforma("1", "por el que se reforman diversas disposiciones",
                         "DOF 11-12-2025", "CCOM_ref01_11dic25.pdf"),
        ], original="")

        rs = diputados.parse_reformas(html, "ccom")

        self.assertEqual([r.no for r in rs], [1])


class TestListaLeyes(unittest.TestCase):
    def test_lee_el_catalogo_del_indice(self):
        html = (
            "<table>"
            '<tr><td>001</td><td><a href="ref/cpeum.htm">CONSTITUCIÓN Política</a>'
            "<p>DOF 05/02/1917</p></td></tr>"
            '<tr><td>017</td><td><a href="ref/lan.htm">LEY de Aguas Nacionales</a>'
            "<p>DOF 01/12/1992</p></td></tr>"
            "<tr><td>encabezado</td><td>sin enlace</td></tr>"
            "</table>"
        )

        leyes = diputados.lista_leyes(html)

        self.assertEqual([(l.no, l.abrev) for l in leyes], [(1, "cpeum"), (17, "lan")])
        self.assertEqual(leyes[1].nombre, "LEY de Aguas Nacionales")


def fila_reglamento(no, nombre, entradas, vigente="Reg_X.pdf"):
    """Una fila de `regla.htm`: el historial va en línea, un <a> por entrada,
    y `entradas` es [(rótulo|None, archivo, fecha)]."""
    cuerpo = f"<p><b>{nombre}</b></p>"
    for etiqueta, archivo, fecha in entradas:
        rotulo = f"<i>{etiqueta}</i>" if etiqueta else ""
        cuerpo += f'<p>{rotulo} <a href="regley/{archivo}">{fecha}</a></p>'
    return (f"<tr><td>{no}</td><td>{cuerpo}</td>"
            f'<td>01/01/1990</td><td><a href="regley/{vigente}">PDF</a></td></tr>')


class TestReglamentos(unittest.TestCase):
    def test_lee_original_y_reformas(self):
        html = "<table>" + fila_reglamento("01", "REGLAMENTO de la Ley Aduanera", [
            ("Original", "Reg_LAdua_orig_20abr15.doc", "DOF 20/04/2015"),
            ("Reforma", "Reg_LAdua_ref01_23feb26.doc", "DOF 23/02/2026"),
        ]) + "</table>"

        rs = diputados.parse_reglamentos(html)

        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].abrev, "reg_ladua")
        self.assertEqual(rs[0].nombre, "REGLAMENTO de la Ley Aduanera")
        self.assertEqual([r.no for r in rs[0].reformas], [None, 1])
        self.assertEqual(rs[0].reformas[0].fecha, "20-04-2015")

    def test_numera_por_fecha_los_archivos_sin_numero(self):
        """Conviven tres generaciones de nombre: `_ref03_29sep17` trae el
        número, `_ref080800` sólo la fecha pegada y `Reg_LGP_29nov06` ninguno
        de los dos."""
        html = "<table>" + fila_reglamento("06", "REGLAMENTO de la Ley de Aeropuertos", [
            ("Original", "Reg_LAero_orig_17feb00.doc", "DOF 17/02/2000"),
            ("Reformas", "Reg_LAero_ref080800.doc", "DOF 08/08/2000"),
            (None, "Reg_LAero_ref090903.doc", "09/09/2003"),
            (None, "Reg_LAero_ref03_29sep17.doc", "29/09/2017"),
        ]) + "</table>"

        rs = diputados.parse_reglamentos(html)

        # El número declarado (03) coincide con la posición cronológica.
        self.assertEqual([r.no for r in rs[0].reformas], [None, 1, 2, 3])
        self.assertEqual(diputados.numeracion_declarada(rs), [])

    def test_un_rotulo_a_media_lista_cambia_de_tipo(self):
        """"Reformas <a>a</a>, <a>b</a>, Fe de E. <a>c</a>" en un solo párrafo:
        la fe de erratas no es una reforma."""
        html = ("<table><tr><td>02</td><td>"
                "<p><b>REGLAMENTO de prueba</b></p>"
                '<p><i>Reformas</i> <a href="regley/Reg_P_ref01_01ene10.doc">DOF 01/01/2010</a>,'
                ' <a href="regley/Reg_P_ref02_02feb11.doc">02/02/2011</a>,'
                ' <i>Fe de E.</i> <a href="regley/Reg_P_fe_03mar12.doc">03/03/2012</a></p>'
                "</td><td>01/01/2009</td></tr></table>")

        rs = diputados.parse_reglamentos(html)

        self.assertEqual([r.fecha for r in rs[0].reformas],
                         ["01-01-2010", "02-02-2011"])

    def test_un_reglamento_sin_historial_conserva_su_publicacion(self):
        """49 de las 137 filas no enlazan historial; la fila sigue declarando
        la fecha de publicación en su propia columna."""
        html = ("<table><tr><td>02</td>"
                "<td><p><b>REGLAMENTO de la Ley Agraria en Materia de Certificación</b></p></td>"
                "<td>06/01/1993</td>"
                '<td><a href="regley/Reg_LAgra_MCDETS.pdf">PDF</a></td></tr></table>')

        rs = diputados.parse_reglamentos(html)

        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].abrev, "reg_lagra_mcdets")
        self.assertEqual([(r.no, r.fecha) for r in rs[0].reformas],
                         [(None, "06-01-1993")])

    def test_la_publicacion_original_se_nombra_como_el_reglamento(self):
        """Para poder emparejarla con el DOF hay que compararla contra el
        nombre; el literal "Publicación original" no coincide con nada."""
        html = "<table>" + fila_reglamento("01", "REGLAMENTO de la Ley Aduanera", [
            ("Original", "Reg_LAdua_orig_20abr15.doc", "DOF 20/04/2015"),
        ]) + "</table>"

        rs = diputados.parse_reglamentos(html)

        self.assertEqual(rs[0].reformas[0].decreto, "REGLAMENTO de la Ley Aduanera")
