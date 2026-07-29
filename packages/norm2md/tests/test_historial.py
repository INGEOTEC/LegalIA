import io
import json
import tarfile
import unittest
from unittest.mock import Mock, patch

from norm2md import historial


def hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory tarball from {member_name: contenido}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = json.dumps(contenido).encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestListarAssets(unittest.TestCase):
    @patch("norm2md.historial.requests.get")
    def test_keeps_only_tgz_assets_as_a_name_to_url_map(self, mock_get):
        mock_get.return_value = Mock(
            json=lambda: {
                "assets": [
                    {"name": "leyes.tgz", "browser_download_url": "https://x/leyes.tgz"},
                    {"name": "SHA256SUMS.txt", "browser_download_url": "https://x/sums.txt"},
                ]
            }
        )

        assets = historial.listar_assets()

        self.assertEqual(assets, {"leyes.tgz": "https://x/leyes.tgz"})
        mock_get.return_value.raise_for_status.assert_called_once()


class TestDownloadNormativeHistory(unittest.TestCase):
    def test_rejects_an_unknown_coleccion_without_any_network_call(self):
        with patch("norm2md.historial.requests.get") as mock_get:
            with self.assertRaises(ValueError):
                historial.download_normative_history("reformas")
            mock_get.assert_not_called()

    @patch("norm2md.historial.requests.get")
    @patch("norm2md.historial.listar_assets")
    def test_raises_when_the_release_lacks_the_asset(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = {}

        with self.assertRaises(KeyError):
            historial.download_normative_history("leyes")
        mock_get.assert_not_called()

    @patch("norm2md.historial.requests.get")
    @patch("norm2md.historial.listar_assets")
    def test_leyes_merges_the_index_with_each_laws_own_file(self, mock_listar_assets, mock_get):
        mock_listar_assets.return_value = {"leyes.tgz": "https://x/leyes.tgz"}
        contenido = hacer_tgz({
            "leyes.json": [
                {"no": 1, "abrev": "cpeum", "nombre": "CONSTITUCIÓN", "reformas": 2, "conNota": 2},
                {"no": 2, "abrev": "ccf", "nombre": "CÓDIGO Civil Federal",
                 "reformas": 1, "conNota": 1},
            ],
            "cpeum.json": [100, 111, 222],
            "ccf.json": [333],
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        resultado = historial.download_normative_history("leyes")

        self.assertEqual(
            resultado,
            [
                {"no": 1, "abrev": "cpeum", "nombre": "CONSTITUCIÓN", "reformas": 2,
                 "conNota": 2, "historial": [100, 111, 222]},
                {"no": 2, "abrev": "ccf", "nombre": "CÓDIGO Civil Federal", "reformas": 1,
                 "conNota": 1, "historial": [333]},
            ],
        )
        mock_get.assert_called_once_with("https://x/leyes.tgz", headers=historial._HEADERS,
                                         timeout=60)

    @patch("norm2md.historial.requests.get")
    @patch("norm2md.historial.listar_assets")
    def test_reglamentos_merges_the_index_with_each_regulations_own_file(
        self, mock_listar_assets, mock_get
    ):
        mock_listar_assets.return_value = {"reglamentos.tgz": "https://x/reglamentos.tgz"}
        contenido = hacer_tgz({
            "reglamentos.json": [
                {"no": 1, "abrev": "reg_ladua", "nombre": "REGLAMENTO de la Ley Aduanera",
                 "reformas": 2, "conNota": 2},
            ],
            "reg_ladua.json": [10, 20],
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        resultado = historial.download_normative_history("reglamentos")

        self.assertEqual(
            resultado,
            [{"no": 1, "abrev": "reg_ladua", "nombre": "REGLAMENTO de la Ley Aduanera",
             "reformas": 2, "conNota": 2, "historial": [10, 20]}],
        )

    @patch("norm2md.historial.requests.get")
    @patch("norm2md.historial.listar_assets")
    def test_normas_merges_the_catalog_with_the_shared_noms_lookup(
        self, mock_listar_assets, mock_get
    ):
        mock_listar_assets.return_value = {"normas.tgz": "https://x/normas.tgz"}
        contenido = hacer_tgz({
            "catalogo.json": [
                {"codigo": "NOM-001-SCFI-1993", "notas": 2, "desde": "03-05-1993",
                 "hasta": "13-10-1993", "titulo": "NORMA Oficial Mexicana NOM-001-SCFI-1993"},
            ],
            "noms.json": {"NOM-001-SCFI-1993": [4798699, 4798700]},
            "citas-ambiguas.json": {},
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        resultado = historial.download_normative_history("normas")

        self.assertEqual(
            resultado,
            [{"codigo": "NOM-001-SCFI-1993", "notas": 2, "desde": "03-05-1993",
             "hasta": "13-10-1993", "titulo": "NORMA Oficial Mexicana NOM-001-SCFI-1993",
             "historial": [4798699, 4798700]}],
        )

    @patch("norm2md.historial.requests.get")
    @patch("norm2md.historial.listar_assets")
    def test_tratados_merges_the_catalog_with_the_parallel_tratados_list(
        self, mock_listar_assets, mock_get
    ):
        mock_listar_assets.return_value = {"tratados.tgz": "https://x/tratados.tgz"}
        contenido = hacer_tgz({
            "catalogo.json": [
                {"nombre": "Tratado de comercio", "notas": 1, "desde": "31-12-1942",
                 "hasta": "31-12-1942", "certeza": None},
                {"nombre": "Convenio 107 OIT", "notas": 1, "desde": "07-07-1960",
                 "hasta": "07-07-1960", "certeza": None},
            ],
            "tratados.json": [[4551478], [4647793]],
        })
        mock_get.return_value = Mock(content=contenido, raise_for_status=Mock())

        resultado = historial.download_normative_history("tratados")

        self.assertEqual(
            resultado,
            [
                {"nombre": "Tratado de comercio", "notas": 1, "desde": "31-12-1942",
                 "hasta": "31-12-1942", "certeza": None, "historial": [4551478]},
                {"nombre": "Convenio 107 OIT", "notas": 1, "desde": "07-07-1960",
                 "hasta": "07-07-1960", "certeza": None, "historial": [4647793]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
