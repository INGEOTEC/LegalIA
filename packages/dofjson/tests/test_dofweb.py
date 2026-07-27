import datetime as dt
import unittest
from unittest.mock import Mock, patch

import requests

from dofjson import dofweb


def pagina(cuerpo: str, cod_diario: str = "147514") -> str:
    """A stand-in for the DOF site's daily index, in its real shape."""
    return (
        '<html><body><table>'
        f'<a href="nota_to_imagen_fs.php?cod_diario={cod_diario}&amp;pagina=-1"></a>'
        f"{cuerpo}"
        "</table></body></html>"
    )


def nota(codigo: str, titulo: str) -> str:
    return (
        f'<td><a href="/nota_detalle.php?codigo={codigo}&amp;fecha=08/03/1999" '
        f'class="enlaces">{titulo} &#160;&#160;</a></td>'
    )


UNA_SECCION = pagina(
    '<td class="txt_blanco">&nbsp; PRIMERA SECCION</td>'
    '<td class="txt_blanco2">&nbsp;PODER EJECUTIVO</td>'
    # The real page leaves commented-out markup wrapped around the heading.
    '<td class="subtitle_azul"><!-- <img src="b.gif" alt="."> -->'
    "&nbsp;SECRETARIA DE GOBERNACION<!-- </img> --></td>"
    + nota("4997854", "Decreto por el que se declaran reformados los art&iacute;culos 16, 19, 22 y 123")
    + nota("4997855", "Extracto de la solicitud de registro")
    + '<td class="txt_blanco2">&nbsp;PODER JUDICIAL</td>'
    '<td class="subtitle_azul">&nbsp;SUPREMA CORTE DE JUSTICIA DE LA NACION</td>'
    + nota("4997860", "Sentencia dictada en la acci&oacute;n de inconstitucionalidad")
)

SIN_DATOS = pagina('<td class="txt_blanco">&nbsp; No hay datos para la fecha seleccionada</td>')

# A pre-digital day: the edition exists, but only as scanned images, so the
# index carries section banners and no per-note links.
SOLO_IMAGEN = pagina(
    '<td class="txt_blanco">&nbsp; PRIMERA SECCION</td>'
    '<td class="txt_blanco">&nbsp; SEGUNDA SECCION</td>',
    cod_diario="189450",
)


def responder(por_edicion):
    """Serve a page per edition (MAT/VES/EXT), keyed off the request URL."""
    def _get(url, **kwargs):
        edicion = url.rsplit("edicion=", 1)[1]
        response = Mock()
        response.content = por_edicion.get(edicion, SIN_DATOS).encode("cp1252")
        response.raise_for_status = Mock()
        return response
    return _get


class TestParseo(unittest.TestCase):
    def notas_de(self, por_edicion, fecha=dt.date(1999, 3, 8)):
        with patch("dofjson.dofweb.requests.get", side_effect=responder(por_edicion)):
            return dofweb.get_notas(fecha)

    def test_reads_notes_under_their_headings(self):
        r = self.notas_de({"MAT": UNA_SECCION})
        notas = r["NotasMatutinas"]

        self.assertEqual(len(notas), 3)
        self.assertEqual(notas[0]["codNota"], 4997854)
        self.assertEqual(
            notas[0]["titulo"],
            "Decreto por el que se declaran reformados los artículos 16, 19, 22 y 123",
        )
        self.assertEqual(notas[0]["codSeccion"], "PRIMERA")
        self.assertEqual(notas[0]["codDiario"], 147514)
        self.assertEqual(notas[0]["codEdicion"], "MAT")

    def test_maps_headings_to_the_codOrgaUno_sidof_uses(self):
        notas = self.notas_de({"MAT": UNA_SECCION})["NotasMatutinas"]

        self.assertEqual(
            [(n["codOrgaUno"], n["nombreCodOrgaUno"]) for n in notas],
            [("PE", "PODER EJECUTIVO")] * 2 + [("PJ", "PODER JUDICIAL")],
        )
        # A new group under an existing one resets, rather than leaking down.
        self.assertEqual(notas[-1]["codOrgaDos"], "SUPREMA CORTE DE JUSTICIA DE LA NACION")

    def test_maps_the_headings_only_older_editions_use(self):
        """GDF and OTROS appear in the archive but not in a present-day
        edition; the site labels them with the same words SIDOF names them by."""
        pagina_gdf = pagina(
            '<td class="txt_blanco">&nbsp; PRIMERA SECCION</td>'
            '<td class="txt_blanco2">&nbsp;GOBIERNO DEL DISTRITO FEDERAL</td>'
            + nota("5001000", "Aviso del GDF")
            + '<td class="txt_blanco2">&nbsp;OTROS</td>'
            + nota("5001001", "Nota diversa")
        )
        notas = self.notas_de({"MAT": pagina_gdf})["NotasMatutinas"]

        self.assertEqual([n["codOrgaUno"] for n in notas], ["GDF", "OTROS"])

    def test_an_unknown_heading_leaves_the_code_unset_but_keeps_the_name(self):
        pagina_rara = pagina(
            '<td class="txt_blanco">&nbsp; PRIMERA SECCION</td>'
            '<td class="txt_blanco2">&nbsp;ORGANISMO NUEVO</td>' + nota("5001002", "X")
        )
        nota_rara = self.notas_de({"MAT": pagina_rara})["NotasMatutinas"][0]

        self.assertIsNone(nota_rara["codOrgaUno"])
        self.assertEqual(nota_rara["nombreCodOrgaUno"], "ORGANISMO NUEVO")

    def test_strips_the_comment_markup_wrapped_around_headings(self):
        notas = self.notas_de({"MAT": UNA_SECCION})["NotasMatutinas"]

        self.assertEqual(notas[0]["codOrgaDos"], "SECRETARIA DE GOBERNACION")

    def test_marks_the_source_and_the_groups_the_site_never_lists(self):
        r = self.notas_de({"MAT": UNA_SECCION})

        self.assertEqual(r["fuente"], "dof.gob.mx")
        self.assertTrue(all(n["fuente"] == "dof.gob.mx" for n in r["NotasMatutinas"]))
        # CV/VG/AV live behind the site's POST search, so a recovered day is
        # complete only with respect to the rest — and says so.
        self.assertEqual(r["notasIncompletas"], ["CV", "VG", "AV"])

    def test_reads_every_edition(self):
        r = self.notas_de({"MAT": UNA_SECCION, "VES": pagina(
            '<td class="txt_blanco">&nbsp; UNICA SECCION</td>'
            '<td class="txt_blanco2">&nbsp;PODER EJECUTIVO</td>'
            + nota("4997870", "Aviso vespertino"))})

        self.assertEqual(len(r["NotasMatutinas"]), 3)
        self.assertEqual(len(r["NotasVespertinas"]), 1)
        self.assertEqual(r["NotasVespertinas"][0]["codEdicion"], "VES")
        self.assertEqual(r["NotasExtraordinarias"], [])


class TestHuecos(unittest.TestCase):
    def notas_de(self, por_edicion, fecha=dt.date(1999, 3, 8)):
        with patch("dofjson.dofweb.requests.get", side_effect=responder(por_edicion)):
            return dofweb.get_notas(fecha)

    def test_a_day_with_no_edition_reports_no_publication(self):
        r = self.notas_de({})

        self.assertFalse(dofweb.hay_publicacion(r))
        self.assertEqual(dofweb.cuenta_notas(r), 0)
        self.assertEqual(r["edicionesSinIndice"], [])
        self.assertNotIn("notasIncompletas", r)

    def test_an_image_only_edition_still_counts_as_published(self):
        r = self.notas_de({"MAT": SOLO_IMAGEN}, fecha=dt.date(1930, 6, 10))

        # No titles can be listed, but the gazette did come out — which is what
        # keeps the day from being filed away as empty.
        self.assertEqual(dofweb.cuenta_notas(r), 0)
        self.assertTrue(dofweb.hay_publicacion(r))
        self.assertEqual(
            r["edicionesSinIndice"], [{"codEdicion": "MAT", "codDiario": 189450}]
        )

    def test_a_404_edition_is_not_an_error(self):
        def _get(url, **kwargs):
            response = Mock(status_code=404)
            response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=response
            )
            return response

        with patch("dofjson.dofweb.requests.get", side_effect=_get):
            self.assertFalse(dofweb.hay_publicacion(dofweb.get_notas(dt.date(1999, 3, 8))))

    def test_other_http_errors_propagate(self):
        def _get(url, **kwargs):
            response = Mock(status_code=500)
            response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=response
            )
            return response

        with patch("dofjson.dofweb.requests.get", side_effect=_get):
            with self.assertRaises(requests.exceptions.HTTPError):
                dofweb.get_notas(dt.date(1999, 3, 8))


class TestTLS(unittest.TestCase):
    def test_retries_with_the_bundled_chain_when_the_server_omits_it(self):
        """dof.gob.mx sends no intermediate, so a strict client cannot build a
        path to a root. The system store is tried first and the bundled chain
        only on failure — verification is never switched off."""
        ok = Mock(content=SIN_DATOS.encode("cp1252"), raise_for_status=Mock())
        llamadas = []

        def _get(url, **kwargs):
            llamadas.append(kwargs.get("verify"))
            if len(llamadas) % 2 == 1:
                raise requests.exceptions.SSLError("unable to get local issuer certificate")
            return ok

        with patch("dofjson.dofweb.requests.get", side_effect=_get):
            dofweb.get_notas(dt.date(1999, 3, 8))

        self.assertIsNone(llamadas[0])
        self.assertEqual(llamadas[1], str(dofweb._CADENA))
        self.assertTrue(dofweb._CADENA.exists(), "the chain must ship with the package")

    def test_the_bundled_chain_holds_the_two_godaddy_certificates(self):
        texto = dofweb._CADENA.read_text(encoding="utf-8")

        self.assertEqual(texto.count("BEGIN CERTIFICATE"), 2)


if __name__ == "__main__":
    unittest.main()
