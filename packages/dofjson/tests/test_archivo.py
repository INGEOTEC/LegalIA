import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

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


class TestArchivo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def correr(self, desde, hasta):
        main([
            "--archivo", "--desde", desde, "--hasta", hasta,
            "--pausa", "0", "--outdir", self.tmpdir.name,
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
        completados = (self.root / ".completados").read_text(encoding="utf-8").split()
        self.assertEqual(completados, ["1980-01-02", "1980-01-03"])

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

        self.correr("1980-01-02", "1980-01-03")

        self.assertFalse((self.root / "1980").exists())
        completados = (self.root / ".completados").read_text(encoding="utf-8").split()
        # The 404 day is marked as done; the network-error day is left to retry.
        self.assertEqual(completados, ["1980-01-02"])

    @patch("dofjson.client.get_notas")
    def test_today_is_never_marked_completed(self, mock_get_notas):
        mock_get_notas.return_value = respuesta_notas(["AVISO"])
        hoy = dt.date.today().isoformat()

        self.correr(hoy, hoy)

        self.assertFalse((self.root / ".completados").exists())

    def test_rejects_inverted_range(self):
        with self.assertRaises(SystemExit):
            self.correr("1980-01-03", "1980-01-02")


if __name__ == "__main__":
    unittest.main()
