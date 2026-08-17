import datetime as dt
import gzip
import io
import json
import tarfile
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

import dofjson
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


class TestDownloadDofAssets(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_downloads_every_asset_into_cache_dir(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        mock_get.side_effect = [
            Mock(content=b"contenido-1980", raise_for_status=Mock()),
            Mock(content=b"contenido-1981", raise_for_status=Mock()),
        ]

        cache_dir = Path(self.tmpdir.name) / "cache"
        rutas = titulos.download_dof_assets(cache_dir, log=lambda *_: None)

        self.assertEqual(rutas, [cache_dir / "notas-1980.tgz", cache_dir / "notas-1981.tgz"])
        self.assertEqual((cache_dir / "notas-1980.tgz").read_bytes(), b"contenido-1980")
        self.assertEqual((cache_dir / "notas-1981.tgz").read_bytes(), b"contenido-1981")
        self.assertEqual(mock_get.call_count, 2)

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_skips_assets_already_in_cache_dir(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        cache_dir = Path(self.tmpdir.name) / "cache"
        cache_dir.mkdir()
        (cache_dir / "notas-1980.tgz").write_bytes(b"ya-en-cache")
        mock_get.return_value = Mock(content=b"contenido-1981", raise_for_status=Mock())

        rutas = titulos.download_dof_assets(cache_dir, log=lambda *_: None)

        self.assertEqual(rutas, [cache_dir / "notas-1980.tgz", cache_dir / "notas-1981.tgz"])
        self.assertEqual((cache_dir / "notas-1980.tgz").read_bytes(), b"ya-en-cache")
        mock_get.assert_called_once_with(
            "https://x/1981.tgz", headers=titulos._HEADERS, timeout=60
        )


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
        resultado = titulos.download_legal_provisions_titles(dest, log=lambda *_: None)

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
        titulos.download_legal_provisions_titles(dest, log=lambda *_: None)

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
        titulos.download_legal_provisions_titles(dest, log=lambda *_: None)

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
        titulos.download_legal_provisions_titles(dest, log=lambda *_: None)

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
        titulos.download_legal_provisions_titles(dest, organigrama_dest, log=lambda *_: None)

        self.assertTrue(organigrama_dest.exists())
        with open(organigrama_dest, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"PEJ": "PODER EJECUTIVO"})
        # The default location next to `dest` is untouched.
        self.assertFalse((Path(self.tmpdir.name) / "organigrama.json").exists())


class TestDownloadTitulosConCacheDir(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_uses_download_dof_assets_and_reads_from_disk(self, mock_listar_assets, mock_get):
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
        cache_dir = Path(self.tmpdir.name) / "cache"
        titulos.download_legal_provisions_titles(dest, cache_dir=cache_dir, log=lambda *_: None)

        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue((cache_dir / "notas-1980.tgz").exists())
        self.assertTrue((cache_dir / "notas-1981.tgz").exists())
        with gzip.open(dest, "rt", encoding="utf-8") as f:
            registros = [json.loads(l) for l in f]
        self.assertEqual(
            registros,
            [
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": None},
                {"codNota": 2, "titulo": "B", "fecha": "02-01-1981", "codOrgaUno": None},
            ],
        )

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_does_not_redownload_assets_already_in_cache_dir(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
        ]
        cache_dir = Path(self.tmpdir.name) / "cache"
        cache_dir.mkdir()
        tgz = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"})
        })
        (cache_dir / "notas-1980.tgz").write_bytes(tgz)

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        titulos.download_legal_provisions_titles(dest, cache_dir=cache_dir, log=lambda *_: None)

        mock_get.assert_not_called()
        with gzip.open(dest, "rt", encoding="utf-8") as f:
            registros = [json.loads(l) for l in f]
        self.assertEqual(
            registros, [{"codNota": 1, "titulo": "A", "fecha": "02-01-1980", "codOrgaUno": None}]
        )


class TestLeeTitulos(unittest.TestCase):
    """El lector de lo que escribe download_legal_provisions_titles. Antes leyesmx usaba
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

    def test_lee_lo_que_download_legal_provisions_titles_escribe(self):
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

    def test_ida_y_vuelta_con_download_legal_provisions_titles(self):
        contenido = hacer_tgz({"1980/02011980-notas.json": dia(
            {"codNota": 7, "titulo": "DECRETO", "fecha": "02-01-1980",
             "codOrgaUno": "PE"})})
        with patch("dofjson.titulos.listar_assets",
                   return_value=[{"name": "notas-1980.tgz", "url": "https://x/a.tgz"}]), \
             patch("dofjson.titulos.requests.get",
                   return_value=Mock(content=contenido, raise_for_status=Mock())):
            titulos.download_legal_provisions_titles(self.dest, log=lambda *_: None)

        self.assertEqual(list(titulos.lee_titulos(self.dest)),
                         [{"codNota": 7, "titulo": "DECRETO", "fecha": "02-01-1980",
                           "codOrgaUno": "PE"}])


class TestNotasDeTgz(unittest.TestCase):
    """The general counterpart of _titulos_de_tgz: every field, every note
    (title-less ones included), ordered by day then codNota."""

    def test_yields_every_field_a_note_carries(self):
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
            ),
        })

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertEqual(resultado, [{
            "codNota": 1,
            "titulo": "DECRETO uno",
            "fecha": "02-01-1980",
            "codOrgaUno": "PEJ",
            "nombreCodOrgaUno": "PODER EJECUTIVO",
            "pagina": 3,
            "codDiario": 99,
        }])

    def test_keeps_title_less_notes(self):
        """Unlike _titulos_de_tgz, this is meant to reconstruct a whole day
        -- e.g. for nota_del_dia_en_cache() -- so stub entries stay in."""
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {"codNota": 1, "titulo": "DECRETO uno", "fecha": "02-01-1980"},
                {"codNota": 2, "fecha": "02-01-1980"},
            ),
        })

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertEqual([n["codNota"] for n in resultado], [1, 2])

    def test_orders_by_day_even_when_the_tar_does_not(self):
        """DDMMYYYY sorts as text by day first: a naive filename sort would
        put 01-02-1980 before 31-01-1980."""
        contenido = hacer_tgz({
            "1980/01021980-notas.json": dia({"codNota": 2, "fecha": "01-02-1980"}),
            "1980/31011980-notas.json": dia({"codNota": 1, "fecha": "31-01-1980"}),
        })

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertEqual([n["codNota"] for n in resultado], [1, 2])

    def test_orders_by_codnota_within_a_day(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {"codNota": 3, "fecha": "02-01-1980"},
                {"codNota": 1, "fecha": "02-01-1980"},
                {"codNota": 2, "fecha": "02-01-1980"},
            ),
        })

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertEqual([n["codNota"] for n in resultado], [1, 2, 3])

    def test_marks_notas_from_a_day_recovered_from_the_web(self):
        recuperado = dia({"codNota": 1, "fecha": "08-03-1999"})
        recuperado["fuente"] = "dof.gob.mx"
        contenido = hacer_tgz({"1999/08031999-notas.json": recuperado})

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertEqual(resultado[0]["fuente"], "dof.gob.mx")

    def test_does_not_add_fuente_for_a_plain_sidof_day(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "fecha": "02-01-1980"}),
        })

        resultado = list(titulos.notas_de_tgz(contenido))

        self.assertNotIn("fuente", resultado[0])

    def test_builds_organigrama_from_every_note_seen(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {"codNota": 1, "codOrgaUno": "PEJ", "nombreCodOrgaUno": "PODER EJECUTIVO"},
                {"codNota": 2, "codOrgaUno": "PJU", "nombreCodOrgaUno": "PODER JUDICIAL"},
            ),
        })

        organigrama = {}
        list(titulos.notas_de_tgz(contenido, organigrama))

        self.assertEqual(organigrama, {"PEJ": "PODER EJECUTIVO", "PJU": "PODER JUDICIAL"})

    def test_is_the_dofjson_public_entry_point(self):
        self.assertIs(dofjson.notas_de_tgz, titulos.notas_de_tgz)


class TestIteradorDeAssets(unittest.TestCase):
    """The whole-archive counterpart of notas_de_tgz: iterates every asset
    instead of a single one already in hand."""

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_yields_every_note_across_every_asset_in_memory(self, mock_listar, mock_get):
        mock_listar.return_value = [
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

        resultado = list(titulos.iterador_de_assets(log=lambda *_: None))

        self.assertEqual([n["codNota"] for n in resultado], [1, 2])
        # Nothing downloaded touches disk when cache_dir is left as None.
        mock_get.assert_called()

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_reads_from_cache_dir_when_given(self, mock_listar, mock_get):
        mock_listar.return_value = [{"name": "notas-1980.tgz", "url": "https://x/1980.tgz"}]
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "A", "fecha": "02-01-1980"})
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            resultado = list(
                titulos.iterador_de_assets(cache_dir, log=lambda *_: None)
            )
            self.assertTrue((cache_dir / "notas-1980.tgz").exists())

        self.assertEqual([n["codNota"] for n in resultado], [1])

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_builds_organigrama_across_assets(self, mock_listar, mock_get):
        mock_listar.return_value = [
            {"name": "notas-1980.tgz", "url": "https://x/1980.tgz"},
            {"name": "notas-1981.tgz", "url": "https://x/1981.tgz"},
        ]
        tgz_1980 = hacer_tgz({
            "1980/02011980-notas.json": dia({
                "codNota": 1, "codOrgaUno": "PEJ", "nombreCodOrgaUno": "PODER EJECUTIVO",
            })
        })
        tgz_1981 = hacer_tgz({
            "1981/02011981-notas.json": dia({
                "codNota": 2, "codOrgaUno": "PJU", "nombreCodOrgaUno": "PODER JUDICIAL",
            })
        })
        mock_get.side_effect = [
            Mock(content=tgz_1980, raise_for_status=Mock()),
            Mock(content=tgz_1981, raise_for_status=Mock()),
        ]

        organigrama = {}
        list(titulos.iterador_de_assets(log=lambda *_: None, organigrama=organigrama))

        self.assertEqual(organigrama, {"PEJ": "PODER EJECUTIVO", "PJU": "PODER JUDICIAL"})

    def test_is_the_dofjson_public_entry_point(self):
        self.assertIs(dofjson.iterador_de_assets, titulos.iterador_de_assets)


class TestDownloadLegalProvisionsTitlesUsesIteradorDeAssets(unittest.TestCase):
    """download_legal_provisions_titles is built on iterador_de_assets +
    _proyectar_titulo now -- these lock in that the public contract (the
    JSONL it writes) did not change with that refactor."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.titulos.requests.get")
    @patch("dofjson.titulos.listar_assets")
    def test_drops_title_less_notes_that_iterador_de_assets_keeps(self, mock_listar, mock_get):
        mock_listar.return_value = [{"name": "notas-1980.tgz", "url": "https://x/1980.tgz"}]
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia(
                {"codNota": 1, "titulo": "A", "fecha": "02-01-1980"},
                {"codNota": 2, "fecha": "02-01-1980"},  # title-less: dropped
            )
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        dest = Path(self.tmpdir.name) / "titulos.jsonl.gz"
        titulos.download_legal_provisions_titles(dest, log=lambda *_: None)

        with gzip.open(dest, "rt", encoding="utf-8") as f:
            registros = [json.loads(l) for l in f]
        self.assertEqual([r["codNota"] for r in registros], [1])


class TestDirectorioCachePredeterminado(unittest.TestCase):
    def test_names_dofjson_in_a_platform_cache_directory(self):
        directorio = titulos.directorio_cache_predeterminado()

        self.assertIsInstance(directorio, Path)
        self.assertIn("dofjson", str(directorio).lower())

    @patch("dofjson.titulos.listar_assets")
    @patch("dofjson.titulos.requests.get")
    def test_download_dof_assets_uses_cache_dir_global_when_omitted(self, mock_get, mock_listar):
        mock_listar.return_value = [{"name": "notas-1980.tgz", "url": "https://x/1980.tgz"}]
        mock_get.return_value = Mock(content=b"contenido", raise_for_status=Mock())

        with tempfile.TemporaryDirectory() as tmp:
            predeterminado = Path(tmp) / "cache-predeterminado"
            with patch("dofjson.titulos.CACHE_DIR", predeterminado):
                rutas = titulos.download_dof_assets(log=lambda *_: None)

            self.assertEqual(rutas, [predeterminado / "notas-1980.tgz"])
            self.assertTrue((predeterminado / "notas-1980.tgz").exists())


class TestAssetParaFecha(unittest.TestCase):
    HOY = dt.date(2026, 8, 17)

    def test_a_closed_year_uses_the_yearly_asset(self):
        self.assertEqual(
            titulos._asset_para_fecha(dt.date(1980, 1, 2), self.HOY), "notas-1980.tgz"
        )

    def test_the_current_year_uses_the_monthly_asset(self):
        self.assertEqual(
            titulos._asset_para_fecha(dt.date(2026, 3, 15), self.HOY), "notas-2026-03.tgz"
        )


class TestNotaDelDiaEnCache(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_reads_the_day_straight_off_a_yearly_asset(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "DECRETO"}),
        })
        (self.cache_dir / "notas-1980.tgz").write_bytes(contenido)

        resultado = titulos.nota_del_dia_en_cache(
            dt.date(1980, 1, 2), self.cache_dir, hoy=dt.date(2026, 8, 17)
        )

        self.assertEqual(resultado["NotasMatutinas"], [{"codNota": 1, "titulo": "DECRETO"}])

    def test_reads_the_day_off_the_current_years_monthly_asset(self):
        contenido = hacer_tgz({
            "2026/15032026-notas.json": dia({"codNota": 1, "titulo": "DECRETO"}),
        })
        (self.cache_dir / "notas-2026-03.tgz").write_bytes(contenido)

        resultado = titulos.nota_del_dia_en_cache(
            dt.date(2026, 3, 15), self.cache_dir, hoy=dt.date(2026, 8, 17)
        )

        self.assertEqual(resultado["NotasMatutinas"], [{"codNota": 1, "titulo": "DECRETO"}])

    def test_returns_none_when_the_asset_is_not_cached(self):
        resultado = titulos.nota_del_dia_en_cache(
            dt.date(1980, 1, 2), self.cache_dir, hoy=dt.date(2026, 8, 17)
        )

        self.assertIsNone(resultado)

    def test_returns_none_when_the_day_is_not_inside_the_cached_asset(self):
        contenido = hacer_tgz({
            "1980/02011980-notas.json": dia({"codNota": 1, "titulo": "DECRETO"}),
        })
        (self.cache_dir / "notas-1980.tgz").write_bytes(contenido)

        resultado = titulos.nota_del_dia_en_cache(
            dt.date(1980, 1, 3), self.cache_dir, hoy=dt.date(2026, 8, 17)
        )

        self.assertIsNone(resultado)

    def test_preserves_fuente_and_every_other_top_level_key(self):
        recuperado = dia({"codNota": 1, "titulo": "DECRETO"})
        recuperado["fuente"] = "dof.gob.mx"
        contenido = hacer_tgz({"1999/08031999-notas.json": recuperado})
        (self.cache_dir / "notas-1999.tgz").write_bytes(contenido)

        resultado = titulos.nota_del_dia_en_cache(
            dt.date(1999, 3, 8), self.cache_dir, hoy=dt.date(2026, 8, 17)
        )

        self.assertEqual(resultado["fuente"], "dof.gob.mx")


if __name__ == "__main__":
    unittest.main()
