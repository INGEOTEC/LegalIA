import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dofjson import titulos


def hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory notas-YYYY.tgz from {member_name: dict_contenido}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = json.dumps(contenido).encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def dia(*notas_matutinas):
    return {
        "messageCode": 200,
        "response": "OK",
        "NotasMatutinas": list(notas_matutinas),
        "NotasVespertinas": [],
        "NotasExtraordinarias": [],
    }


class TestListarAssets(unittest.TestCase):
    @patch("dofjson.titulos.requests.get")
    def test_keeps_only_tgz_assets(self, mock_get):
        mock_get.return_value = Mock(
            json=lambda: {
                "assets": [
                    {"name": "notas-1980.tgz", "browser_download_url": "https://x/1980.tgz"},
                    {"name": "notas-archivo.txt", "browser_download_url": "https://x/readme.txt"},
                ]
            }
        )

        assets = titulos.listar_assets()

        self.assertEqual(assets, [{"name": "notas-1980.tgz", "url": "https://x/1980.tgz"}])
        mock_get.return_value.raise_for_status.assert_called_once()


class TestTitulosDeTgz(unittest.TestCase):
    def test_extracts_only_codnota_titulo_y_fecha(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "DECRETO uno",
                    "fecha": "02-01-1980",
                    "pagina": 3,
                    "codDiario": 99,
                },
                {"codNota": 2, "titulo": "", "fecha": "02-01-1980"},
                {"codNota": 3, "fecha": "02-01-1980"},
            ),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(
            resultado,
            [{"codNota": 1, "titulo": "DECRETO uno", "fecha": "02-01-1980"}],
        )

    def test_reads_multiple_days(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"}),
            "1980/03011980-notas.json": dia({"codNota": 2, "titulo": "B", "fecha": "03-01-1980"}),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(
            resultado,
            [
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980"},
                {"codNota": 2, "titulo": "B", "fecha": "03-01-1980"},
            ],
        )

    def test_fecha_defaults_to_none_when_missing(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A"}),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(resultado, [{"codNota": 1, "titulo": "A", "fecha": None}])


class TestDownloadTitulos(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_writes_one_jsonl_line_per_titled_nota(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        tgz_1980 = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"})
        })
        tgz_1981 = hacer_tgz({
            "1981/02011981-notas.json": dia({"codNota": 2, "titulo": "B", "fecha": "02-01-1981"})
        })
        mock_get.side_effect = [
            Mock(content=tgz_1980, raise_for_status=Mock()),
            Mock(content=tgz_1981, raise_for_status=Mock()),
        ]

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        resultado = titulos.download_titulos(dest, log=lambda *_: None)

        self.assertEqual(resultado, dest)
        with gzip.open(dest, "rt", encoding="utf-8") as f:
            lineas = f.read().splitlines()
        self.assertEqual(
            [json.loads(l) for l in lineas],
            [
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980"},
                {"codNota": 2, "titulo": "B", "fecha": "02-01-1981"},
            ],
        )

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_downloaded_bytes_never_touch_disk(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
        ]
        tgz = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"})
        })
        mock_get.return_value = Mock(content=tgz, raise_for_status=Mock())

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        titulos.download_titulos(dest, log=lambda *_: None)

        archivos = sorted(p.name for p in Path(self.tmpdir.name).iterdir())
        self.assertEqual(archivos, ["titulos.jsonl.gz"])


if __name__ == "__main__":
    unittest.main()
