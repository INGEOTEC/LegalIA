import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from dofjson import archivo, dofweb
from dofjson.cli import main


def respuesta_notas(titulos):
    return {
        "messageCode": 200,
        "response": "OK",
        "NotasMatutinas": [
            {"codNota": i, "titulo": titulo} for i, titulo in enumerate(titulos, 1)
        ],
        "NotasVespertinas": [],
        "NotasExtraordinarias": [],
    }


def registro(root):
    """The completed-days registry, as (fecha, fuente) pairs."""
    texto = (root / ".completados").read_text(encoding="utf-8")
    return [tuple(linea.split("\t")) for linea in texto.splitlines() if linea]


class TestArchivo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def correr(self, desde, hasta, *extra):
        main([
            "--archivo", "--desde", desde, "--hasta", hasta,
            "--pausa", "0", "--outdir", self.tmpdir.name, *extra,
        ])

    @patch("dofjson.client.get_notas")
    def test_saves_one_json_per_day_and_marks_completed(self, mock_get_notas):
        mock_get_notas.return_value = respuesta_notas(["DECRETO uno", "AVISO dos"])

        self.correr("1980-01-02", "1980-01-03")

        for name in ("02011980-notas.json", "03011980-notas.json"):
            dest = self.root / "1980" / name
            self.assertTrue(dest.exists(), name)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(len(data["NotasMatutinas"]), 2)
        self.assertEqual(
            registro(self.root), [("1980-01-02", "sidof"), ("1980-01-03", "sidof")]
        )

    @patch("dofjson.client.get_notas")
    def test_resumes_skipping_already_completed_days(self, mock_get_notas):
        mock_get_notas.return_value = respuesta_notas(["ACUERDO"])
        (self.root / ".completados").write_text("1980-01-02\n", encoding="utf-8")

        self.correr("1980-01-02", "1980-01-03")

        mock_get_notas.assert_called_once_with(dt.date(1980, 1, 3))

    @patch("dofjson.client.get_notas")
    def test_404_days_complete_without_file_but_errors_retry(self, mock_get_notas):
        mock_get_notas.side_effect = [
            requests.exceptions.HTTPError(response=Mock(status_code=404)),
            requests.exceptions.ConnectionError(),
        ]

        self.correr("1980-01-02", "1980-01-03", "--respaldo", "nunca")

        self.assertFalse((self.root / "1980").exists())
        # The 404 day is marked as done; the network-error day is left to retry.
        self.assertEqual(registro(self.root), [("1980-01-02", "sin-edicion")])

    @patch("dofjson.client.get_notas")
    def test_reads_a_registry_written_before_provenance_was_recorded(self, mock_get_notas):
        mock_get_notas.return_value = respuesta_notas(["ACUERDO"])
        (self.root / ".completados").write_text("1980-01-02\n", encoding="utf-8")

        self.correr("1980-01-02", "1980-01-03", "--respaldo", "nunca")

        mock_get_notas.assert_called_once_with(dt.date(1980, 1, 3))

    @patch("dofjson.client.get_notas")
    def test_today_is_never_marked_completed(self, mock_get_notas):
        mock_get_notas.return_value = respuesta_notas(["AVISO"])
        hoy = dt.date.today().isoformat()

        self.correr(hoy, hoy)

        self.assertFalse((self.root / ".completados").exists())

    def test_rejects_inverted_range(self):
        with self.assertRaises(SystemExit):
            self.correr("1980-01-03", "1980-01-02")


def respuesta_web(titulos, sin_indice=()):
    return {
        "messageCode": 200,
        "response": "OK",
        "fuente": "dof.gob.mx",
        "NotasMatutinas": [
            {"codNota": 900 + i, "titulo": t, "fuente": "dof.gob.mx"}
            for i, t in enumerate(titulos, 1)
        ],
        "NotasVespertinas": [],
        "NotasExtraordinarias": [],
        "edicionesSinIndice": list(sin_indice),
    }


class TestRespaldo(unittest.TestCase):
    """SIDOF reports a day it has lost exactly as it reports a Sunday: 200 OK
    with no notes. These cover not taking that at face value."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def correr(self, desde, hasta, *extra):
        main([
            "--archivo", "--desde", desde, "--hasta", hasta,
            "--pausa", "0", "--outdir", self.tmpdir.name, *extra,
        ])

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_recovers_a_weekday_sidof_reports_as_empty(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])
        mock_web.return_value = respuesta_web(["DECRETO recuperado"])

        self.correr("1999-03-08", "1999-03-08")  # a Monday

        mock_web.assert_called_once_with(dt.date(1999, 3, 8))
        guardado = json.loads(
            (self.root / "1999" / "08031999-notas.json").read_text(encoding="utf-8")
        )
        self.assertEqual(guardado["fuente"], "dof.gob.mx")
        self.assertEqual(len(guardado["NotasMatutinas"]), 1)
        self.assertEqual(registro(self.root), [("1999-03-08", "dof.gob.mx")])

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_does_not_ask_the_web_when_sidof_has_the_day(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas(["ACUERDO"])

        self.correr("1999-03-09", "1999-03-09")

        mock_web.assert_not_called()
        guardado = json.loads(
            (self.root / "1999" / "09031999-notas.json").read_text(encoding="utf-8")
        )
        self.assertEqual(guardado["fuente"], "sidof")

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_skips_weekends_by_default_but_checks_them_with_todos(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])
        mock_web.return_value = respuesta_web([])

        self.correr("1999-03-06", "1999-03-07")  # Saturday and Sunday
        mock_web.assert_not_called()

        # Both days are on record now, so clear it to re-walk the same range.
        (self.root / ".completados").unlink()
        self.correr("1999-03-06", "1999-03-07", "--respaldo", "todos")
        self.assertEqual(mock_web.call_count, 2)

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_respaldo_nunca_trusts_sidof_alone(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])

        self.correr("1999-03-08", "1999-03-08", "--respaldo", "nunca")

        mock_web.assert_not_called()
        self.assertEqual(registro(self.root), [("1999-03-08", "sin-edicion")])

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_both_sources_agreeing_on_empty_is_a_day_with_no_edition(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])
        mock_web.return_value = respuesta_web([])

        self.correr("1999-01-01", "1999-01-01")  # a Friday, but a holiday

        self.assertFalse((self.root / "1999").exists())
        self.assertEqual(registro(self.root), [("1999-01-01", "sin-edicion")])

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_an_image_only_edition_is_stored_as_published(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])
        mock_web.return_value = respuesta_web(
            [], sin_indice=[{"codEdicion": "MAT", "codDiario": 189450}]
        )

        self.correr("1930-06-10", "1930-06-10")

        # No titles exist to recover, but the day is on record as published
        # rather than filed away as empty.
        guardado = json.loads(
            (self.root / "1930" / "10061930-notas.json").read_text(encoding="utf-8")
        )
        self.assertEqual(guardado["edicionesSinIndice"][0]["codDiario"], 189450)
        self.assertEqual(registro(self.root), [("1930-06-10", "dof.gob.mx")])

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_a_page_served_for_another_date_leaves_the_day_to_retry(self, mock_sidof, mock_web):
        """Believing it would file real notes under this day; calling the day
        empty would bury it for good. Retrying is the only safe answer."""
        mock_sidof.return_value = respuesta_notas([])
        mock_web.side_effect = dofweb.PaginaDeOtroDia("se pidió 16/03/2015 y dice 24/07/2015")

        self.correr("2015-03-16", "2015-03-16")

        self.assertFalse((self.root / "2015").exists())
        self.assertFalse((self.root / ".completados").exists())

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.client.get_notas")
    def test_a_failing_web_lookup_leaves_the_day_to_retry(self, mock_sidof, mock_web):
        mock_sidof.return_value = respuesta_notas([])
        mock_web.side_effect = requests.exceptions.ConnectionError()

        self.correr("1999-03-08", "1999-03-08")

        # Not recorded as done, so a later run tries it again instead of
        # burying the day as empty on a transient failure.
        self.assertFalse((self.root / ".completados").exists())

    def test_rejects_an_unknown_respaldo(self):
        with self.assertRaises(SystemExit):
            self.correr("1999-03-08", "1999-03-08", "--respaldo", "quizas")


class TestConsultaRespaldoDofweb(unittest.TestCase):
    """The one piece of the SIDOF-then-dofweb fallback decision procesar_dia()
    and dofjson.cli's own single-day `notas` query both need identically —
    shared as archivo.consulta_respaldo_dofweb() instead of reimplemented in
    each place (procesar_dia()'s own use of it is covered by TestRespaldo
    above, through the full --archivo run)."""

    FECHA = dt.date(1999, 3, 8)

    @patch("dofjson.dofweb.get_notas")
    def test_returns_the_website_answer_when_it_published(self, mock_web):
        mock_web.return_value = respuesta_notas(["Decreto recuperado"])

        resultado = archivo.consulta_respaldo_dofweb(self.FECHA)

        mock_web.assert_called_once_with(self.FECHA)
        self.assertEqual(resultado, mock_web.return_value)

    @patch("dofjson.dofweb.get_notas")
    def test_returns_none_when_the_website_agrees_the_day_was_empty(self, mock_web):
        mock_web.return_value = {
            "messageCode": 200, "response": "OK", "fuente": dofweb.FUENTE,
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
            "edicionesSinIndice": [],
        }

        self.assertIsNone(archivo.consulta_respaldo_dofweb(self.FECHA))

    @patch("dofjson.dofweb.get_notas")
    def test_propagates_a_page_served_for_another_date(self, mock_web):
        mock_web.side_effect = dofweb.PaginaDeOtroDia("otro día")

        with self.assertRaises(dofweb.PaginaDeOtroDia):
            archivo.consulta_respaldo_dofweb(self.FECHA)

    @patch("dofjson.dofweb.get_notas")
    def test_propagates_network_errors(self, mock_web):
        mock_web.side_effect = requests.exceptions.ConnectionError("caído")

        with self.assertRaises(requests.exceptions.ConnectionError):
            archivo.consulta_respaldo_dofweb(self.FECHA)


if __name__ == "__main__":
    unittest.main()
