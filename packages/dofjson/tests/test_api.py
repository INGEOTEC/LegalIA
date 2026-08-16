import datetime as dt
import unittest
from unittest.mock import Mock, patch

import requests

from dofjson import dofweb
from dofjson.api import FUENTE_SIDOF, RESPALDO_OPCIONES, consultar_respaldo, get_nota, get_notas

WEB_NOTA = {
    "codNota": 4997808,
    "fecha": "03-03-1999",
    "titulo": "DECRETO por el que se concede permiso...",
    "cadenaContenido": "<HTML><BODY><p>Cuerpo del decreto.</p></BODY></HTML>",
    "existeHtml": "S",
    "fuente": dofweb.FUENTE,
}


class TestGetNota(unittest.TestCase):
    """SIDOF is missing whole days (see dofjson.dofweb), and the notes on
    them have no SIDOF record at all — the DOF's website is the only
    source."""

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.client.get_nota")
    def test_prefers_sidof_when_it_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": {"codNota": 1, "cadenaContenido": "<p>x</p>"}}

        self.assertEqual(get_nota(1)["codNota"], 1)
        mock_web.assert_not_called()

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.client.get_nota")
    def test_falls_back_to_the_website_when_sidof_lacks_the_note(self, mock_sidof, mock_web):
        # SIDOF answers an empty list, not an error, for a codNota it lacks.
        mock_sidof.return_value = {"messageCode": 200, "response": "OK", "Nota": []}
        mock_web.return_value = {"Nota": WEB_NOTA}

        nota = get_nota(4997808)

        mock_web.assert_called_once_with(4997808)
        self.assertEqual(nota["fuente"], dofweb.FUENTE)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.client.get_nota")
    def test_raises_when_neither_source_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": []}
        mock_web.return_value = {"Nota": []}

        with self.assertRaises(ValueError):
            get_nota(999999999)


class TestGetNotas(unittest.TestCase):
    FECHA = dt.date(2026, 7, 15)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_prefers_sidof_when_it_has_notes(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 1, "titulo": "Nota A"}],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA)

        self.assertEqual(notas["NotasMatutinas"], [{"codNota": 1, "titulo": "Nota A"}])
        self.assertEqual(notas["fuente"], FUENTE_SIDOF)
        mock_web.assert_not_called()

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_drops_titleless_stub_entries(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [
                {"codNota": 1, "titulo": "Nota A"},
                {"codNota": 2},  # title-less stub/twin
            ],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA)

        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [1])

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_falls_back_to_the_website_when_sidof_has_nothing(self, mock_sidof, mock_web):
        # SIDOF answers every list empty, not an error, for a day it lacks.
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }
        mock_web.return_value = {
            "NotasMatutinas": [{"codNota": 3, "titulo": "Nota recuperada"}],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
            "fuente": dofweb.FUENTE,
        }

        notas = get_notas(self.FECHA)

        mock_web.assert_called_once_with(self.FECHA)
        self.assertEqual(notas["fuente"], dofweb.FUENTE)
        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [3])

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_returns_empty_when_neither_source_published_that_day(self, mock_sidof, mock_web):
        vacio = {"NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": []}
        mock_sidof.return_value = dict(vacio)
        mock_web.return_value = {**vacio, "edicionesSinIndice": []}

        notas = get_notas(self.FECHA)

        self.assertEqual(notas["NotasMatutinas"], [])
        self.assertEqual(notas["NotasVespertinas"], [])
        self.assertEqual(notas["NotasExtraordinarias"], [])
        # Tagged "sidof" even though empty: `fuente` names which source the
        # returned answer actually is, not just which one had notes.
        self.assertEqual(notas["fuente"], FUENTE_SIDOF)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_respaldo_nunca_never_checks_the_website(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA, respaldo="nunca")

        mock_web.assert_not_called()
        self.assertEqual(notas["fuente"], FUENTE_SIDOF)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_respaldo_habiles_skips_a_weekend_day(self, mock_sidof, mock_web):
        sabado = dt.date(2026, 7, 18)
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        get_notas(sabado, respaldo="habiles")

        mock_web.assert_not_called()

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_default_respaldo_checks_even_on_a_weekend(self, mock_sidof, mock_web):
        # get_notas()'s own default ("todos") is not archivo's ("habiles") --
        # an ordinary single-day caller always gets a second opinion.
        sabado = dt.date(2026, 7, 18)
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }
        mock_web.return_value = {
            "NotasMatutinas": [{"codNota": 9, "titulo": "Extraordinaria"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
            "fuente": dofweb.FUENTE,
        }

        notas = get_notas(sabado)

        mock_web.assert_called_once_with(sabado)
        self.assertEqual(notas["fuente"], dofweb.FUENTE)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_a_404_from_sidof_is_treated_like_an_empty_answer(self, mock_sidof, mock_web):
        response = Mock(status_code=404)
        mock_sidof.side_effect = requests.exceptions.HTTPError(response=response)
        mock_web.return_value = {
            "NotasMatutinas": [{"codNota": 5, "titulo": "Recuperada"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
            "fuente": dofweb.FUENTE,
        }

        notas = get_notas(self.FECHA)

        mock_web.assert_called_once_with(self.FECHA)
        self.assertEqual(notas["fuente"], dofweb.FUENTE)

    @patch("dofjson.api.client.get_notas")
    def test_a_non_404_http_error_from_sidof_propagates(self, mock_sidof):
        response = Mock(status_code=500)
        mock_sidof.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaises(requests.exceptions.HTTPError):
            get_notas(self.FECHA)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.client.get_notas")
    def test_a_page_served_for_another_date_propagates(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }
        mock_web.side_effect = dofweb.PaginaDeOtroDia("otro día")

        with self.assertRaises(dofweb.PaginaDeOtroDia):
            get_notas(self.FECHA)

    def test_rejects_an_unknown_respaldo(self):
        with self.assertRaises(ValueError):
            get_notas(self.FECHA, respaldo="quizas")


class TestConsultarRespaldo(unittest.TestCase):
    def test_nunca_is_always_false(self):
        self.assertFalse(consultar_respaldo(dt.date(2026, 7, 15), "nunca"))
        self.assertFalse(consultar_respaldo(dt.date(2026, 7, 18), "nunca"))

    def test_todos_is_always_true(self):
        self.assertTrue(consultar_respaldo(dt.date(2026, 7, 15), "todos"))
        self.assertTrue(consultar_respaldo(dt.date(2026, 7, 18), "todos"))

    def test_habiles_is_true_on_weekdays_only(self):
        lunes = dt.date(2026, 7, 13)
        sabado = dt.date(2026, 7, 18)
        self.assertTrue(consultar_respaldo(lunes, "habiles"))
        self.assertFalse(consultar_respaldo(sabado, "habiles"))

    def test_rejects_an_unknown_value(self):
        with self.assertRaises(ValueError):
            consultar_respaldo(dt.date(2026, 7, 15), "quizas")

    def test_options_are_exactly_the_three_documented(self):
        self.assertEqual(RESPALDO_OPCIONES, ("habiles", "todos", "nunca"))


if __name__ == "__main__":
    unittest.main()
