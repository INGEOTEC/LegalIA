import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nota2md import scjn


def _hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory tarball from {member_name: raw_bytes_or_str},
    same helper shape as packages/nota2md/tests/test_utils.py's own."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestDownloadScjnLeyesCorpus(unittest.TestCase):
    @staticmethod
    def _respuestas(asset: str, contenido: bytes) -> list:
        return [
            Mock(json=lambda: {"assets": [
                {"name": asset, "browser_download_url": f"https://x/{asset}"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]

    @patch("nota2md.scjn.requests.get")
    def test_lanza_key_error_cuando_el_release_no_tiene_el_asset_de_esa_ley(self, mock_get):
        mock_get.return_value = Mock(json=lambda: {"assets": []}, raise_for_status=Mock())

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_corpus("cpeum")

    @patch("nota2md.scjn.requests.get")
    def test_une_indice_con_el_markdown_de_cada_snapshot(self, mock_get):
        indice = [
            {"archivo": "22-01-1994.md", "codNota": 100, "ratio_similitud": 0.9,
             "sospechoso": False, "title_candidates": [100], "title_link_status": "linked",
             "content_diff_confirmed_codNota": None, "content_diff_score": None},
        ]
        contenido = _hacer_tgz({
            "cpeum/indice.json": json.dumps(indice),
            "cpeum/22-01-1994.md": "**TEXTO ORIGINAL.**",
        })
        mock_get.side_effect = self._respuestas("cpeum.tgz", contenido)

        resultado = scjn.download_scjn_leyes_corpus("cpeum")

        self.assertEqual(resultado["slug"], "cpeum")
        self.assertEqual(len(resultado["snapshots"]), 1)
        snap = resultado["snapshots"][0]
        self.assertEqual(snap["codNota"], 100)
        self.assertEqual(snap["title_link_status"], "linked")
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")

    @patch("nota2md.scjn.requests.get")
    def test_cada_snapshot_trae_el_texto_dof_de_los_candidatos_considerados(self, mock_get):
        # Lo que hace auditable el enlace de #126/#127 sin volver a la red:
        # el snapshot llega con el texto de cada candidato que se comparo,
        # no solo con el codNota ganador.
        indice = [
            {"archivo": "22-01-1994.md", "codNota": 100, "title_candidates": [100, 101],
             "content_diff_confirmed_codNota": 100, "content_diff_score": 0.8},
            {"archivo": "01-01-1995.md", "codNota": None, "title_candidates": []},
        ]
        contenido = _hacer_tgz({
            "lft/indice.json": json.dumps(indice),
            "lft/22-01-1994.md": "**TEXTO ORIGINAL.**",
            "lft/01-01-1995.md": "**REFORMA.**",
            "lft/notas/nota-100.md": "DECRETO uno.",
            "lft/notas/nota-101.md": "DECRETO dos.",
        })
        mock_get.side_effect = self._respuestas("lft.tgz", contenido)

        snapshots = scjn.download_scjn_leyes_corpus("lft")["snapshots"]

        self.assertEqual(snapshots[0]["notas"], {100: "DECRETO uno.", 101: "DECRETO dos."})
        self.assertEqual(snapshots[1]["notas"], {})

    @patch("nota2md.scjn.requests.get")
    def test_instrumento_sin_indice_json_regresa_snapshots_sin_enlace_en_vez_de_omitirse(
        self, mock_get
    ):
        # Fase 2 (issue #105) pendiente para este instrumento: hay
        # snapshots pero enlaza_scjn_legislacion.py no ha corrido para el.
        contenido = _hacer_tgz({"lfea/01-01-2012.md": "**TEXTO ORIGINAL.**"})
        mock_get.side_effect = self._respuestas("lfea.tgz", contenido)

        resultado = scjn.download_scjn_leyes_corpus("lfea")

        self.assertEqual(resultado["slug"], "lfea")
        snap = resultado["snapshots"][0]
        self.assertEqual(snap["archivo"], "01-01-2012.md")
        self.assertIsNone(snap["codNota"])
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")


if __name__ == "__main__":
    unittest.main()


class TestDescargaAssetsScjnLeyes(unittest.TestCase):
    """`download_scjn_leyes_assets` (issue #155): the release materialized on
    disk, and idempotent — a second run costs no download at all."""

    URLS = {
        "indice-global.json.gz": "https://x/indice-global.json.gz",
        "lfca.tgz": "https://x/lfca.tgz",
        "lft.tgz": "https://x/lft.tgz",
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_sin_slugs_baja_el_indice_y_todos_los_tgz(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lfca.tgz", "lft.tgz"],
        )
        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_slugs_acota_pero_el_indice_siempre_viene(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = scjn.download_scjn_leyes_assets(["lft"], cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lft.tgz"],
        )

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_la_segunda_corrida_no_baja_nada(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        scjn.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertFalse(any(descargado for _, descargado in resultados))
        mock_descarga.assert_not_called()

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_refrescar_vuelve_a_bajar(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        scjn.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp, refrescar=True)

        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_cache_dir_none_no_tiene_donde_escribir(self, mock_assets):
        mock_assets.return_value = dict(self.URLS)

        with self.assertRaises(ValueError):
            scjn.download_scjn_leyes_assets(cache_dir=None)

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_un_slug_que_el_release_no_publica_es_un_error(self, mock_assets, _):
        mock_assets.return_value = dict(self.URLS)

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_assets(["no-existe"], cache_dir=self.tmp)

    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_los_slugs_del_release_salen_de_sus_propios_assets(self, mock_assets):
        mock_assets.return_value = dict(self.URLS)

        self.assertEqual(scjn.scjn_leyes_slugs(), ["lfca", "lft"])


