import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dofjson.client import (
    BASE_URL,
    download_imagen,
    download_nota,
    download_nota_imagen_o_pdf,
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


def _write_pdf_with_page_numbers(path, numeros):
    """Write a real PDF to `path`, one page per entry of `numeros`, each
    page's extracted text reading "{numero} DIARIO OFICIAL" -- or blank, for
    a `None` entry (reproduces an old edition's unnumbered cover page)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_ref = writer._add_object(font)

    for numero in numeros:
        page = writer.add_blank_page(width=200, height=100)
        if numero is not None:
            resources = DictionaryObject()
            fonts = DictionaryObject()
            fonts[NameObject("/F1")] = font_ref
            resources[NameObject("/Font")] = fonts
            page[NameObject("/Resources")] = resources

            stream = DecodedStreamObject()
            stream.set_data(
                f"BT /F1 12 Tf 10 50 Td ({numero} DIARIO OFICIAL) Tj ET".encode("latin-1")
            )
            page[NameObject("/Contents")] = writer._add_object(stream)

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
            "codNota": 5793641, "cadenaContenido": "", "codDiario": 328506,
            "fecha": "15-07-2026", "pagina": 9, "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        # min(paginas_conocidas)=5 -> even the metadata-based fallback offset
        # (used when the PDF's own text yields no votes, see
        # test_download_nota_pdf_falls_back_to_metadata_offset_when_pdf_has_no_text)
        # still puts pagina 9 at índice 4, past this edition's 3 pages.
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 5793639, "pagina": 5},
                {"codNota": 5793641, "pagina": 9},
            ]
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: _write_pdf(dest, 3)

        with self.assertRaises(ValueError):
            download_nota_pdf(5793641, self.outdir)

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_handles_running_volume_page_numbers(
        self, mock_get_nota, mock_get_notas, mock_download_pdf
    ):
        # Reproduces issue #95 (codNota=4535455, 31-08-1934): unlike a modern
        # edition, this bound-volume PDF's printed numbering doesn't restart
        # at 1 -- it carries on from earlier in the "tomo" (1137, 1138, ...),
        # and the note's own `pagina` (1142) is a printed number, not a
        # physical PDF page index. `pagina - 1` would point far past the
        # edition PDF's few physical pages; the real physical index has to
        # be worked out from what the PDF's own pages actually print.
        from pypdf import PdfReader

        nota = {
            "codNota": 4535455,
            "cadenaContenido": "",
            "codDiario": 193549,
            "fecha": "31-08-1934",
            "pagina": 1142,
            "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 4535406, "pagina": 1138},
                {"codNota": 4535455, "pagina": 1142},
                {"codNota": 4535505, "pagina": 1143},
            ]
        }
        # Physical page 0 is the edition's cover, with no printed number.
        numeros = [None, 1138, 1139, 1140, 1141, 1142, 1143, 1144]
        mock_download_pdf.side_effect = (
            lambda cod_diario, dest, **kw: _write_pdf_with_page_numbers(dest, numeros)
        )

        dest = download_nota_pdf(4535455, self.outdir)

        # pages 1142,1143 -> physical indices 5,6
        self.assertEqual(len(PdfReader(str(dest)).pages), 2)
        self.assertIn("1142", PdfReader(str(dest)).pages[0].extract_text())
        self.assertIn("1143", PdfReader(str(dest)).pages[1].extract_text())

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_falls_back_to_metadata_offset_when_pdf_has_no_text(
        self, mock_get_nota, mock_get_notas, mock_download_pdf
    ):
        # The edition PDF carries no extractable text at all (a purely
        # scanned, un-OCR'd volume) -- _detectar_offset_paginacion finds no
        # votes. offset=1 (the modern-edition default) would be wrong here
        # and point past the PDF entirely; the smallest pagina the day's
        # own notes report (50) is the edition's real first physical page.
        nota = {
            "codNota": 52, "cadenaContenido": "", "codDiario": 900001,
            "fecha": "01-01-1930", "pagina": 52, "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 50, "pagina": 50},
                {"codNota": 51, "pagina": 51},
                {"codNota": 52, "pagina": 52},  # last of the day -> single page
            ]
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: _write_pdf(dest, 3)

        dest = download_nota_pdf(52, self.outdir)

        from pypdf import PdfReader

        self.assertEqual(len(PdfReader(str(dest)).pages), 1)  # pagina 52 -> indice 2, in range

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

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_imagenes_skips_page_already_on_disk(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        nota = {
            "codNota": 5793655, "cadenaContenido": "", "codDiario": 208439,
            "fecha": "15-07-2026", "pagina": 80, "codEdicion": "MAT",
        }
        mock_get_nota.return_value = {"Nota": nota}
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 5793655, "pagina": 80}]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [{"pagina": 80, "nombreArchivo": "20260715-080-U-000"}]
        }
        existente = self.outdir / "nota-5793655-20260715-080-U-000.jpg"
        existente.write_bytes(b"\xff\xd8\xff ya estaba aqui")

        dests = download_nota_imagenes(5793655, self.outdir)

        mock_download_imagen.assert_not_called()
        self.assertEqual(dests, [existente])
        self.assertEqual(existente.read_bytes(), b"\xff\xd8\xff ya estaba aqui")

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_reuses_cached_edicion_across_notas(
        self, mock_get_nota, mock_get_notas, mock_download_pdf
    ):
        notas = {
            5793639: {
                "codNota": 5793639, "cadenaContenido": "", "codDiario": 328506,
                "fecha": "15-07-2026", "pagina": 5, "codEdicion": "MAT",
            },
            5793641: {
                "codNota": 5793641, "cadenaContenido": "", "codDiario": 328506,
                "fecha": "15-07-2026", "pagina": 9, "codEdicion": "MAT",
            },
        }
        mock_get_nota.side_effect = lambda cod: {"Nota": notas[cod]}
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 5793639, "pagina": 5},
                {"codNota": 5793641, "pagina": 9},
            ]
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: _write_pdf(dest, 12)

        download_nota_pdf(5793639, self.outdir)
        download_nota_pdf(5793641, self.outdir)

        mock_download_pdf.assert_called_once()  # same codDiario -> edition fetched only once
        self.assertTrue((self.outdir / "edicion-328506.pdf").exists())

    @patch("dofjson.client.get_nota")
    def test_download_nota_pdf_returns_cached_file_without_any_network_call(
        self, mock_get_nota
    ):
        existente = self.outdir / "nota-5793639.pdf"
        existente.write_bytes(b"%PDF- ya estaba aqui")

        dest = download_nota_pdf(5793639, self.outdir)

        mock_get_nota.assert_not_called()
        self.assertEqual(dest, existente)
        self.assertEqual(existente.read_bytes(), b"%PDF- ya estaba aqui")


class TestDownloadNotaImagenOPdf(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    def test_prefers_the_page_imagen_when_sidof_has_one(
        self, mock_get_notas, mock_get_imagenes, mock_download_imagen, mock_download_pdf
    ):
        nota = {
            "codNota": 5793655, "cadenaContenido": "", "codDiario": 208439,
            "fecha": "15-07-2026", "pagina": 80, "codEdicion": "MAT",
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 5793655, "pagina": 80}]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [{"pagina": 80, "nombreArchivo": "20260715-080-U-000"}]
        }

        dests = download_nota_imagen_o_pdf(5793655, self.outdir, nota=nota)

        self.assertEqual(dests, [self.outdir / "nota-5793655-20260715-080-U-000.jpg"])
        mock_download_pdf.assert_not_called()

    @patch("dofjson.client.download_pdf")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    def test_falls_back_to_the_whole_uncut_edicion_when_no_matching_page_imagen(
        self, mock_get_notas, mock_get_imagenes, mock_download_pdf
    ):
        # Reproduces the reported bug: a note's `pagina` can be a running,
        # multi-edition tomo count (issue #95) that simply never matches
        # this single day's own local image index — download_nota_pdf()'s
        # own page-position work (which that mismatch would also break) is
        # never even reached, because this function must not need it at
        # download time in the first place.
        nota = {
            "codNota": 4456687, "cadenaContenido": "", "codDiario": 188437,
            "fecha": "27-04-1933", "pagina": 677, "codEdicion": "MAT",
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 4456687, "pagina": 677}]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [{"pagina": p, "nombreArchivo": f"x-{p}"} for p in range(1, 9)]
        }  # local page numbers 1..8 -- pagina=677 never matches any of them
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: dest.write_bytes(b"%PDF-edicion")

        dests = download_nota_imagen_o_pdf(4456687, self.outdir, nota=nota)

        self.assertEqual(dests, [self.outdir / "edicion-188437.pdf"])
        mock_download_pdf.assert_called_once()

    @patch("dofjson.client.get_nota")
    def test_returns_cached_imagenes_without_any_network_call(self, mock_get_nota):
        existente = self.outdir / "nota-5793655-20260715-080-U-000.jpg"
        existente.write_bytes(b"\xff\xd8\xff ya estaba aqui")

        dests = download_nota_imagen_o_pdf(5793655, self.outdir)

        mock_get_nota.assert_not_called()
        self.assertEqual(dests, [existente])


if __name__ == "__main__":
    unittest.main()
