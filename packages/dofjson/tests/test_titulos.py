import gzip
import io
import json
import tarfile
import tempfile
import unittest
from collections import Counter
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
    def test_extracts_only_codnota_titulo_fecha_y_codorgauno(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "DECRETO uno",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
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
            [{"codNota": 1, "titulo": "DECRETO uno", "fecha": "02-01-1980", "codOrgaUno": "PEJ"}],
        )

    def test_reads_multiple_days(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": "PEJ"}
            ),
            "1980/03011980-notas.json": dia(
                {"codNota": 2, "titulo": "B", "fecha": "03-01-1980", "codOrgaUno": "PJU"}
            ),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(
            resultado,
            [
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": "PEJ"},
                {"codNota": 2, "titulo": "B", "fecha": "03-01-1980", "codOrgaUno": "PJU"},
            ],
        )

    def test_marks_notas_from_a_day_that_did_not_come_from_sidof(self):
        """Days the archive recovered from the DOF website (dofweb.py) carry a
        `fuente`; it rides along so a recovered note stays identifiable."""
        recuperado = dia({"codNota": 1, "titulo": "A", "fecha": "08-03-1999", "codOrgaUno": "PE"})
        recuperado["fuente"] = "dof.gob.mx"
        contenido = hacer_tgz({"1999/08031999-notas.json": recuperado})

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(resultado[0]["fuente"], "dof.gob.mx")

    def test_does_not_mark_sidof_notas(self):
        """"sidof" on every one of ~1.2 million rows would cost more than it
        says, so only the exceptions are marked."""
        desde_sidof = dia({"codNota": 1, "titulo": "A", "fecha": "09-03-1999", "codOrgaUno": "PE"})
        desde_sidof["fuente"] = "sidof"
        contenido = hacer_tgz({
            "1999/09031999-notas.json": desde_sidof,
            # A day stored before the marker existed is SIDOF's too.
            "1999/10031999-notas.json": dia(
                {"codNota": 2, "titulo": "B", "fecha": "10-03-1999", "codOrgaUno": "PE"}
            ),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(len(resultado), 2)
        self.assertTrue(all("fuente" not in t for t in resultado))

    def test_fecha_y_codorgauno_default_to_none_when_missing(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A"}),
        })

        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(
            resultado, [{"codNota": 1, "titulo": "A", "fecha": None, "codOrgaUno": None}]
        )

    def test_builds_organigrama_from_notas(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "A",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
                {
                    "codNota": 2,
                    "titulo": "B",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PJU",
                    "nombreCodOrgaUno": "PODER JUDICIAL",
                },
                {
                    "codNota": 3,
                    "titulo": "C",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
            ),
        })

        organigrama = {}
        list(titulos._titulos_de_tgz(contenido, organigrama))

        self.assertEqual(organigrama, {"PEJ": "PODER EJECUTIVO", "PJU": "PODER JUDICIAL"})

    def test_organigrama_keeps_first_name_seen_for_a_code(self):
        """A code's name is settled once; later, differently-cased/renamed
        occurrences of the same codOrgaUno do not overwrite it."""
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "A",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
                {
                    "codNota": 2,
                    "titulo": "B",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "OTRO NOMBRE",
                },
            ),
        })

        organigrama = {}
        list(titulos._titulos_de_tgz(contenido, organigrama))

        self.assertEqual(organigrama, {"PEJ": "PODER EJECUTIVO"})

    def test_organigrama_ignored_when_not_provided(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "A",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
            ),
        })

        # Should not raise when the caller does not care about the mapping.
        resultado = list(titulos._titulos_de_tgz(contenido))

        self.assertEqual(
            resultado, [{"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": "PEJ"}]
        )


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
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": None},
                {"codNota": 2, "titulo": "B", "fecha": "02-01-1981", "codOrgaUno": None},
            ],
        )

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_fecha_carries_the_year_for_grouping(self, mock_listar_assets, mock_get):
        """Every record keeps its fecha, so the flat output can be grouped by
        the note's own publication year downstream (see titles_by_year)."""
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        tgz_1980 = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"}),
            "1980/15061980-notas.json": dia({"codNota": 2, "titulo": "B", "fecha": "15-06-1980"}),
        })
        tgz_1981 = hacer_tgz({
            "1981/03031981-notas.json": dia({"codNota": 3, "titulo": "C", "fecha": "03-03-1981"}),
        })
        mock_get.side_effect = [
            Mock(content=tgz_1980, raise_for_status=Mock()),
            Mock(content=tgz_1981, raise_for_status=Mock()),
        ]

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        titulos.download_titulos(dest, log=lambda *_: None)

        with gzip.open(dest, "rt", encoding="utf-8") as f:
            registros = [json.loads(l) for l in f]

        # Each record's year comes from its own fecha (DD-MM-YYYY).
        self.assertTrue(all(r["fecha"] for r in registros))
        por_anio = Counter(int(r["fecha"].split("-")[-1]) for r in registros)
        self.assertEqual(por_anio, Counter({1980: 2, 1981: 1}))

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
        self.assertEqual(archivos, ["organigrama.json", "titulos.jsonl.gz"])

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_writes_organigrama_json_next_to_dest_by_default(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        tgz_1980 = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "A",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
                {
                    "codNota": 2,
                    "titulo": "B",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PJU",
                    "nombreCodOrgaUno": "PODER JUDICIAL",
                },
            ),
        })
        tgz_1981 = hacer_tgz({
            "1981/03031981-notas.json": dia(
                {
                    "codNota": 3,
                    "titulo": "C",
                    "fecha": "03-03-1981",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
            ),
        })
        mock_get.side_effect = [
            Mock(content=tgz_1980, raise_for_status=Mock()),
            Mock(content=tgz_1981, raise_for_status=Mock()),
        ]

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        titulos.download_titulos(dest, log=lambda *_: None)

        organigrama_dest = Path(self.tmpdir.name) / "organigrama.json"
        self.assertTrue(organigrama_dest.exists())
        with open(organigrama_dest, encoding="utf-8") as f:
            organigrama = json.load(f)
        self.assertEqual(organigrama, {"PEJ": "PODER EJECUTIVO", "PJU": "PODER JUDICIAL"})

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_organigrama_dest_can_be_overridden(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
        ]
        tgz = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {
                    "codNota": 1,
                    "titulo": "A",
                    "fecha": "02-01-1980",
                    "codOrgaUno": "PEJ",
                    "nombreCodOrgaUno": "PODER EJECUTIVO",
                },
            ),
        })
        mock_get.return_value = Mock(content=tgz, raise_for_status=Mock())

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        organigrama_dest = Path(self.tmpdir.name) / "otro" / "mapa.json"
        titulos.download_titulos(dest, organigrama_dest, log=lambda *_: None)

        self.assertTrue(organigrama_dest.exists())
        with open(organigrama_dest, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"PEJ": "PODER EJECUTIVO"})
        # The default location next to `dest` is untouched.
        self.assertFalse((Path(self.tmpdir.name) / "organigrama.json").exists())


class TestLeeTitulos(unittest.TestCase):
    """El lector de lo que escribe download_titulos. Antes leyesmx usaba
    microtc.utils.tweet_iterator para esto: lee el mismo formato, pero importa
    numpy sin declararlo, así que una instalación sin numpy falla en
    `import microtc` y no en la llamada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "titulos.jsonl.gz"

    def tearDown(self):
        self.tmp.cleanup()

    def escribe(self, lineas):
        with gzip.open(self.dest, "wt", encoding="utf-8") as f:
            f.write(lineas)

    def test_lee_lo_que_download_titulos_escribe(self):
        self.escribe('{"codNota": 1, "titulo": "DECRETO"}\n'
                     '{"codNota": 2, "titulo": "ACUERDO"}\n')

        self.assertEqual(
            list(titulos.lee_titulos(self.dest)),
            [{"codNota": 1, "titulo": "DECRETO"}, {"codNota": 2, "titulo": "ACUERDO"}],
        )

    def test_conserva_los_acentos(self):
        self.escribe('{"codNota": 1, "titulo": "Aclaración al Acuerdo"}\n')

        self.assertEqual(next(titulos.lee_titulos(self.dest))["titulo"],
                         "Aclaración al Acuerdo")

    def test_ignora_las_lineas_en_blanco(self):
        self.escribe('{"codNota": 1}\n\n{"codNota": 2}\n')

        self.assertEqual([n["codNota"] for n in titulos.lee_titulos(self.dest)], [1, 2])

    def test_es_perezoso(self):
        """1.2 millones de notas: cargarlas todas de golpe no es opción."""
        self.escribe('{"codNota": 1}\n{"codNota": 2}\n')

        self.assertEqual(next(titulos.lee_titulos(self.dest)), {"codNota": 1})

    def test_ida_y_vuelta_con_download_titulos(self):
        contenido = hacer_tgz({"1980/02011980-notas.json": dia(
            {"codNota": 7, "titulo": "DECRETO", "fecha": "02-01-1980",
             "codOrgaUno": "PE"})})
        with patch("dofjson.titulos.listar_assets",
                   return_value=[{"name": "notas-1980.tgz", "url": "https://x/a.tgz"}]), \
             patch("dofjson.titulos.requests.get",
                   return_value=Mock(content=contenido, raise_for_status=Mock())):
            titulos.download_titulos(self.dest, log=lambda *_: None)

        self.assertEqual(list(titulos.lee_titulos(self.dest)),
                         [{"codNota": 7, "titulo": "DECRETO", "fecha": "02-01-1980",
                           "codOrgaUno": "PE"}])


if __name__ == "__main__":
    unittest.main()
