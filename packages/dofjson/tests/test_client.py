import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dofjson.client import (
    BASE_URL,
    download_imagen,
    download_nota,
    download_nota_imagenes,
    download_nota_pdf,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
    get_nota,
    get_notas,
    infer_paginas,
    quita_notas_sin_titulo,
)


def _write_pdf(path, n_pages):
    """Write a real N-page PDF to `path` (blank pages) so page counts and
    slicing can be checked without mocking pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as f:
        writer.write(f)


class TestClient(unittest.TestCase):
    def _mock_response(self, payload):
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status = MagicMock()
        return mock_response

    @patch("dofjson.client.requests.get")
    def test_get_diario_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        result = get_diario(dt.date(2026, 7, 16))

        mock_get.assert_called_once()
        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/diarios/porFecha/16-07-2026")
        self.assertEqual(result, {"messageCode": 200})

    @patch("dofjson.client.requests.get")
    def test_get_notas_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_notas(dt.date(2026, 1, 5))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/05-01-2026")

    @patch("dofjson.client.requests.get")
    def test_get_nota_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_nota(5793717)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/nota/5793717")

    @patch("dofjson.client.requests.get")
    def test_get_indicadores_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_indicadores(dt.date(2026, 7, 16))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/indicadores/16-07-2026")

    @patch("dofjson.client.requests.get")
    def test_propagates_http_errors(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_get.return_value = mock_response

        with self.assertRaises(Exception):
            get_diario(dt.date(2026, 7, 16))

    @patch("dofjson.client.requests.get")
    def test_get_imagenes_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_imagenes(208439)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/imagenesFsRecurso/obtieneImagenesFS/208439")


class TestDownloadPdf(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmpdir.name) / "edicion.pdf"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.requests.get")
    def test_download_pdf_writes_valid_pdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_pdf(208439, self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/documentos/pdf/208439")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.client.requests.get")
    def test_download_pdf_rejects_non_pdf_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_pdf(208439, self.dest)

        self.assertFalse(self.dest.exists())


class TestDownloadImagen(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmpdir.name) / "pagina.jpg"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.requests.get")
    def test_download_imagen_writes_valid_jpeg(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8\xff\xe0 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_imagen("19800102-02-U-000", "MAT", self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/copiaCertificada/MAT/19800102-02-U-000.jpg")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.client.requests.get")
    def test_download_imagen_rejects_non_jpeg_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_imagen("19800102-02-U-000", "MAT", self.dest)

        self.assertFalse(self.dest.exists())


class TestInferPaginas(unittest.TestCase):
    def _notas_del_dia(self, paginas_ordenadas):
        return {
            "NotasMatutinas": [
                {"codNota": 1000 + i, "pagina": pagina}
                for i, pagina in enumerate(paginas_ordenadas)
            ]
        }

    def test_single_page_when_next_nota_same_page(self):
        notas_del_dia = self._notas_del_dia([20, 20, 21, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21])

    def test_spans_two_pages_when_next_nota_is_next_page(self):
        # Reproduces codNota=4845455 (pagina 21) followed by codNota=4845457
        # (pagina 22) on 02-01-1980.
        notas_del_dia = self._notas_del_dia([20, 20, 21, 22, 23])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])

    def test_last_nota_of_the_day_is_a_single_page(self):
        notas_del_dia = self._notas_del_dia([20, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 22}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [22])

    def test_resorts_by_codnota_when_raw_list_and_orden_dont_match_page_order(self):
        # Real API response shape for 02-01-1980: raw list order and `orden`
        # do not match page order, but codNota ascending does — infer_paginas
        # must re-sort by codNota, not rely on list order or `orden`.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 4845457, "pagina": 22, "orden": 0.0},
                {"codNota": 4845424, "pagina": 2, "orden": 2.0},
                {"codNota": 4845455, "pagina": 21, "orden": 0.0},
            ]
        }
        nota = {"codNota": 4845455, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])

    def test_skips_digital_twin_sharing_the_same_starting_page(self):
        # Reproduces codNota=5793654 (pagina 80, existeDoc "N") on 15-07-2026:
        # its immediate next note by codNota is 5793655, a digital-text twin
        # of the same content (existeDoc "S") also starting on page 80. The
        # true next distinct note (5793656) starts on page 89, but doesn't
        # share it with 5793654 — unlike a genuine same-page neighbor, a
        # skipped twin's page is excluded from the range.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793653, "pagina": 78},
                {"codNota": 5793654, "pagina": 80},
                {"codNota": 5793655, "pagina": 80},
                {"codNota": 5793656, "pagina": 89},
            ]
        }
        nota = {"codNota": 5793654, "codEdicion": "MAT", "pagina": 80, "existeDoc": "N"}

        self.assertEqual(infer_paginas(nota, notas_del_dia), list(range(80, 81)))

    def test_skips_multiple_consecutive_digital_twins(self):
        # Some notes are split into several digital "section" entries, all
        # sharing the image-only note's starting page — infer_paginas must
        # skip all of them, not just the first.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 100, "pagina": 30},
                {"codNota": 101, "pagina": 30},
                {"codNota": 102, "pagina": 30},
                {"codNota": 103, "pagina": 30},
                {"codNota": 104, "pagina": 40},
            ]
        }
        nota = {"codNota": 100, "codEdicion": "MAT", "pagina": 30, "existeDoc": "N"}

        self.assertEqual(infer_paginas(nota, notas_del_dia), list(range(30, 31)))

    def test_same_page_neighbor_without_existdoc_field_stays_single_page(self):
        # A same-page neighbor is only treated as a digital twin when it is
        # explicitly marked existeDoc "S" — synthetic/legacy data lacking
        # that field must keep the original single-page confinement.
        notas_del_dia = self._notas_del_dia([20, 20, 21, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21])


class TestQuitaNotasSinTitulo(unittest.TestCase):
    def test_drops_titleless_stub_note(self):
        # Reproduces codNota=5793456/5793457 on 14-07-2026: 5793456 is a stub
        # duplicate (no titulo, existeHtml "S") of 5793457, which carries the
        # real title on the same page.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793456, "pagina": 5, "existeHtml": "S", "existeDoc": "N"},
                {"codNota": 5793457, "pagina": 5, "existeHtml": "S", "existeDoc": "S", "titulo": "Decreto..."},
            ],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual([n["codNota"] for n in resultado["NotasMatutinas"]], [5793457])

    def test_drops_titleless_image_only_note_too(self):
        # A genuine image-only note (existeHtml "N") has no digital twin at
        # all, but is still dropped: this function's only criterion is
        # whether `titulo` is present.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793654, "pagina": 80, "existeHtml": "N", "existeDoc": "N"},
                {"codNota": 5793655, "pagina": 80, "existeHtml": "S", "existeDoc": "S", "titulo": "Convenio..."},
            ],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual([n["codNota"] for n in resultado["NotasMatutinas"]], [5793655])

    def test_keeps_titled_notes_untouched(self):
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 1, "pagina": 1, "existeHtml": "S", "existeDoc": "S", "titulo": "Aviso A"},
                {"codNota": 2, "pagina": 1, "existeHtml": "S", "existeDoc": "S", "titulo": "Aviso B"},
            ],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual(resultado, notas_del_dia)


class TestDownloadNota(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_nota")
    def test_saves_json_when_content_exists(self, mock_get_nota, mock_get_imagenes):
        mock_get_nota.return_value = {
            "Nota": {"codNota": 5793719, "cadenaContenido": "<HTML>...</HTML>"}
        }

        dests = download_nota(5793719, self.outdir)

        self.assertEqual(dests, [self.outdir / "nota-5793719.json"])
        self.assertTrue(dests[0].exists())
        mock_get_imagenes.assert_not_called()

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_falls_back_to_single_imagen_when_no_content(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845424,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 2,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 4845424, "pagina": 2},
                {"codNota": 4845426, "pagina": 2},
            ]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [
                {"pagina": 2, "nombreArchivo": "19800102-02-U-000"},
            ]
        }

        dests = download_nota(4845424, self.outdir)

        mock_get_notas.assert_called_once_with(dt.date(1980, 1, 2))
        mock_get_imagenes.assert_called_once_with(208439)
        mock_download_imagen.assert_called_once_with(
            "19800102-02-U-000", "MAT", self.outdir / "nota-4845424-19800102-02-U-000.jpg"
        )
        self.assertEqual(dests, [self.outdir / "nota-4845424-19800102-02-U-000.jpg"])

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_falls_back_to_two_imagenes_when_nota_spans_two_pages(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845455,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 21,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 4845455, "pagina": 21},
                {"codNota": 4845457, "pagina": 22},
            ]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [
                {"pagina": 21, "nombreArchivo": "19800102-21-U-000"},
                {"pagina": 22, "nombreArchivo": "19800102-22-U-000"},
            ]
        }

        dests = download_nota(4845455, self.outdir)

        self.assertEqual(mock_download_imagen.call_count, 2)
        self.assertEqual(
            dests,
            [
                self.outdir / "nota-4845455-19800102-21-U-000.jpg",
                self.outdir / "nota-4845455-19800102-22-U-000.jpg",
            ],
        )

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_imagenes_ignores_cadena_contenido(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        # A note WITH HTML content still gets its scanned page image(s)
        # downloaded — that is the whole point of download_nota_imagenes vs
        # download_nota, which would have short-circuited to JSON here.
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 5793655,
                "cadenaContenido": "<HTML>lots of digital text</HTML>",
                "codDiario": 328506,
                "fecha": "15-07-2026",
                "pagina": 80,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 5793655, "pagina": 80},
                {"codNota": 5793656, "pagina": 80},
            ]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [{"pagina": 80, "nombreArchivo": "20260715-080-U-000"}]
        }

        dests = download_nota_imagenes(5793655, self.outdir)

        mock_download_imagen.assert_called_once_with(
            "20260715-080-U-000", "MAT", self.outdir / "nota-5793655-20260715-080-U-000.jpg"
        )
        self.assertEqual(dests, [self.outdir / "nota-5793655-20260715-080-U-000.jpg"])

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_slices_note_pages(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_pdf
    ):
        from pypdf import PdfReader

        nota = {
            "codNota": 5793639,
            "cadenaContenido": "<HTML>tiene texto, pero pedimos PDF</HTML>",
            "codDiario": 328506,
            "fecha": "15-07-2026",
            "pagina": 5,
            "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        # next distinct note starts on page 9 -> this note spans pages 5..9
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 5793639, "pagina": 5},
                {"codNota": 5793641, "pagina": 9},
            ]
        }
        # the edition PDF has plenty of pages
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: _write_pdf(dest, 12)

        dest = download_nota_pdf(5793639, self.outdir)

        self.assertEqual(dest, self.outdir / "nota-5793639.pdf")
        (cod_diario_arg, _pdf_path), _ = mock_download_pdf.call_args
        self.assertEqual(cod_diario_arg, 328506)
        self.assertEqual(len(PdfReader(str(dest)).pages), 5)  # pages 5,6,7,8,9
        mock_get_imagenes.assert_not_called()

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_raises_when_page_out_of_range(
        self, mock_get_nota, mock_get_notas, mock_download_pdf
    ):
        nota = {
            "codNota": 5793639, "cadenaContenido": "", "codDiario": 328506,
            "fecha": "15-07-2026", "pagina": 5, "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 5793639, "pagina": 5}]
        }
        # the edition PDF has fewer pages than the note's page number
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: _write_pdf(dest, 3)

        with self.assertRaises(ValueError):
            download_nota_pdf(5793639, self.outdir)

    @patch("dofjson.client.download_nota_imagenes")
    @patch("dofjson.client.get_nota")
    def test_download_nota_delegates_to_imagenes_when_no_content(
        self, mock_get_nota, mock_download_nota_imagenes
    ):
        nota = {"codNota": 4845424, "cadenaContenido": "", "codEdicion": "MAT"}
        mock_get_nota.return_value = {"Nota": nota}
        mock_download_nota_imagenes.return_value = [self.outdir / "nota-4845424-x.jpg"]

        dests = download_nota(4845424, self.outdir)

        mock_download_nota_imagenes.assert_called_once_with(
            4845424, self.outdir, nota=nota
        )
        self.assertEqual(dests, [self.outdir / "nota-4845424-x.jpg"])

    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_raises_when_no_matching_page(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845424,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 2,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 4845424, "pagina": 2}]
        }
        mock_get_imagenes.return_value = {"imagenesFS": []}

        with self.assertRaises(ValueError):
            download_nota(4845424, self.outdir)


if __name__ == "__main__":
    unittest.main()
