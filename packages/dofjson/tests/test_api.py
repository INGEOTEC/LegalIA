import datetime as dt
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

from dofjson import dofweb
from dofjson.api import (
    FUENTE_SIDOF,
    RESPALDO_OPCIONES,
    consultar_respaldo,
    download_edicion_pdf,
    download_nota,
    download_nota_imagen_o_pdf,
    download_nota_imagenes,
    download_nota_pdf,
    get_nota,
    get_notas,
)


def hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory notas-YYYY[-MM].tgz from {member_name: dict_contenido}
    -- same shape tests/test_titulos.py's own helper builds."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = json.dumps(contenido).encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


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
    @patch("dofjson.api.sidof.get_nota")
    def test_prefers_sidof_when_it_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": {"codNota": 1, "cadenaContenido": "<p>x</p>"}}

        self.assertEqual(get_nota(1)["codNota"], 1)
        mock_web.assert_not_called()

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_falls_back_to_the_website_when_sidof_lacks_the_note(self, mock_sidof, mock_web):
        # SIDOF answers an empty list, not an error, for a codNota it lacks.
        mock_sidof.return_value = {"messageCode": 200, "response": "OK", "Nota": []}
        mock_web.return_value = {"Nota": WEB_NOTA}

        nota = get_nota(4997808)

        mock_web.assert_called_once_with(4997808, fecha=None)
        self.assertEqual(nota["fuente"], dofweb.FUENTE)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_raises_when_neither_source_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": []}
        mock_web.return_value = {"Nota": []}

        with self.assertRaises(ValueError):
            get_nota(999999999)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_passes_fecha_through_to_the_website_fallback(self, mock_sidof, mock_web):
        # Some codigos (1999-2000) only resolve on the website when their
        # date is given alongside them (see issue #109/#111).
        mock_sidof.return_value = {"messageCode": 200, "response": "OK", "Nota": []}
        mock_web.return_value = {"Nota": WEB_NOTA}

        get_nota(4920760, fecha=dt.date(2000, 2, 29))

        mock_web.assert_called_once_with(4920760, fecha=dt.date(2000, 2, 29))


class TestGetNotas(unittest.TestCase):
    FECHA = dt.date(2026, 7, 15)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
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
    @patch("dofjson.api.sidof.get_notas")
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
    @patch("dofjson.api.sidof.get_notas")
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
    @patch("dofjson.api.sidof.get_notas")
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
    @patch("dofjson.api.sidof.get_notas")
    def test_respaldo_nunca_never_checks_the_website(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA, respaldo="nunca")

        mock_web.assert_not_called()
        self.assertEqual(notas["fuente"], FUENTE_SIDOF)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_respaldo_habiles_skips_a_weekend_day(self, mock_sidof, mock_web):
        sabado = dt.date(2026, 7, 18)
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        get_notas(sabado, respaldo="habiles")

        mock_web.assert_not_called()

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
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
    @patch("dofjson.api.sidof.get_notas")
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

    @patch("dofjson.api.sidof.get_notas")
    def test_a_non_404_http_error_from_sidof_propagates(self, mock_sidof):
        response = Mock(status_code=500)
        mock_sidof.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaises(requests.exceptions.HTTPError):
            get_notas(self.FECHA)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
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


class TestGetNotasCacheDir(unittest.TestCase):
    """A cache_dir already holding the day's notas-archivo asset answers
    get_notas() straight off disk -- no SIDOF, no dofweb -- since that asset
    is exactly what get_notas() itself would return and store (see
    dofjson.archivo)."""

    FECHA = dt.date(1980, 1, 2)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _cachear(self, notas_del_dia):
        contenido = hacer_tgz({"1980/02011980-notas.json": notas_del_dia})
        (self.cache_dir / "notas-1980.tgz").write_bytes(contenido)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_a_cached_day_skips_sidof_and_dofweb_entirely(self, mock_sidof, mock_web):
        self._cachear({
            "messageCode": 200, "response": "OK", "fuente": "sidof",
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO cacheado"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        })

        notas = get_notas(self.FECHA, cache_dir=self.cache_dir)

        mock_sidof.assert_not_called()
        mock_web.assert_not_called()
        self.assertEqual(notas["NotasMatutinas"], [{"codNota": 1, "titulo": "DECRETO cacheado"}])
        self.assertEqual(notas["fuente"], "sidof")

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_a_day_not_in_the_cache_falls_back_to_the_ordinary_lookup(
        self, mock_sidof, mock_web
    ):
        # cache_dir exists but has nothing archived for this date at all.
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 9, "titulo": "Nota en vivo"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA, cache_dir=self.cache_dir)

        mock_sidof.assert_called_once_with(self.FECHA)
        self.assertEqual(notas["fuente"], FUENTE_SIDOF)

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_an_unrelated_cache_dir_is_not_consulted_when_omitted(self, mock_sidof, mock_web):
        """Omitting cache_dir falls back to the CACHE_DIR global (see below),
        not to whatever unrelated directory a test/caller happens to have
        lying around."""
        self._cachear({
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO cacheado"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        })
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 9, "titulo": "Nota en vivo"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        notas = get_notas(self.FECHA)

        mock_sidof.assert_called_once_with(self.FECHA)
        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [9])

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_omitting_cache_dir_uses_the_cache_dir_global(self, mock_sidof, mock_web):
        """Not passing cache_dir at all still gets the cache, via
        dofjson.titulos.CACHE_DIR -- the "best default for the end user" the
        package-wide global is for."""
        self._cachear({
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO cacheado"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        })

        from dofjson import titulos
        with patch.object(titulos, "CACHE_DIR", self.cache_dir):
            notas = get_notas(self.FECHA)

        mock_sidof.assert_not_called()
        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [1])

    @patch("dofjson.api.dofweb.get_notas")
    @patch("dofjson.api.sidof.get_notas")
    def test_explicit_none_skips_the_cache_dir_global(self, mock_sidof, mock_web):
        self._cachear({
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO cacheado"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        })
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 9, "titulo": "Nota en vivo"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        from dofjson import titulos
        with patch.object(titulos, "CACHE_DIR", self.cache_dir):
            notas = get_notas(self.FECHA, cache_dir=None)

        mock_sidof.assert_called_once_with(self.FECHA)
        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [9])


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


class TestDownloadEdicionPdf(unittest.TestCase):
    FECHA = dt.date(2026, 7, 16)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_diario")
    def test_downloads_the_requested_edition(self, mock_get_diario, mock_download_pdf):
        mock_get_diario.return_value = {
            "messageCode": 200, "response": "OK", "Extraordinaria": None, "Vespertina": None,
            "Matutina": [{"codDiario": 328525, "codSeccion": "UNICA"}],
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: dest.write_bytes(b"%PDF-edicion")

        dest = download_edicion_pdf(self.FECHA, "MAT", self.outdir)

        mock_get_diario.assert_called_once_with(self.FECHA)
        mock_download_pdf.assert_called_once_with(328525, self.outdir / "edicion-328525.pdf", timeout=60)
        self.assertEqual(dest, self.outdir / "edicion-328525.pdf")

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_diario")
    def test_resolves_the_codDiario_shared_by_a_multi_section_edition(
        self, mock_get_diario, mock_download_pdf
    ):
        mock_get_diario.return_value = {
            "messageCode": 200, "response": "OK",
            "Vespertina": [
                {"codDiario": 281500, "codSeccion": "PRIMERA"},
                {"codDiario": 281500, "codSeccion": "SEGUNDA"},
            ],
            "Matutina": None, "Extraordinaria": None,
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: dest.write_bytes(b"%PDF-edicion")

        dest = download_edicion_pdf(self.FECHA, "VES", self.outdir)

        mock_download_pdf.assert_called_once_with(281500, self.outdir / "edicion-281500.pdf", timeout=60)
        self.assertEqual(dest, self.outdir / "edicion-281500.pdf")

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_diario")
    def test_raises_when_the_requested_edition_was_not_published(
        self, mock_get_diario, mock_download_pdf
    ):
        mock_get_diario.return_value = {
            "messageCode": 200, "response": "OK", "Extraordinaria": None, "Vespertina": None,
            "Matutina": [{"codDiario": 328525, "codSeccion": "UNICA"}],
        }

        with self.assertRaises(ValueError):
            download_edicion_pdf(self.FECHA, "VES", self.outdir)
        mock_download_pdf.assert_not_called()

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_diario")
    def test_a_day_entirely_outside_sidof_coverage_raises_clearly(
        self, mock_get_diario, mock_download_pdf
    ):
        # get_diario() 404s outright for a day with no edition at all (as
        # opposed to an ordinary response with that one key null/missing).
        response = Mock(status_code=404)
        mock_get_diario.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaises(ValueError):
            download_edicion_pdf(self.FECHA, "MAT", self.outdir)
        mock_download_pdf.assert_not_called()

    @patch("dofjson.api.sidof.get_diario")
    def test_a_non_404_http_error_propagates(self, mock_get_diario):
        response = Mock(status_code=500)
        mock_get_diario.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaises(requests.exceptions.HTTPError):
            download_edicion_pdf(self.FECHA, "MAT", self.outdir)

    def test_rejects_an_unknown_edicion(self):
        with self.assertRaises(ValueError):
            download_edicion_pdf(self.FECHA, "XXX", self.outdir)

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_diario")
    def test_reuses_the_cached_edicion_across_calls(self, mock_get_diario, mock_download_pdf):
        mock_get_diario.return_value = {
            "Matutina": [{"codDiario": 328525, "codSeccion": "UNICA"}],
            "Vespertina": None, "Extraordinaria": None,
        }
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: dest.write_bytes(b"%PDF-edicion")

        download_edicion_pdf(self.FECHA, "MAT", self.outdir)
        download_edicion_pdf(self.FECHA, "MAT", self.outdir)

        mock_download_pdf.assert_called_once()


class TestResolverNotaGuardsAgainstDofwebOnlyNotes(unittest.TestCase):
    """download_nota_imagenes()/download_nota_pdf()/download_nota_imagen_o_pdf()/
    download_nota() resolve a bare codNota through get_nota() above -- not
    dofjson.sidof.get_nota() directly -- when no `nota` is passed in, so a
    codNota SIDOF has no record of at all (recovered only from dofweb, no
    codDiario/pagina) fails with a clear error here instead of crashing on a
    missing field deep inside page-inference logic."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _mock_dofweb_only_nota(self, mock_sidof_get_nota, mock_dofweb_get_nota):
        # SIDOF answers {"Nota": []}, not an error, for a codNota it lacks.
        mock_sidof_get_nota.return_value = {"messageCode": 200, "response": "OK", "Nota": []}
        mock_dofweb_get_nota.return_value = {
            "Nota": {
                "codNota": 4997808,
                "fecha": "03-03-1999",
                "cadenaContenido": "<HTML><BODY><p>x</p></BODY></HTML>",
                "existeHtml": "S",
                "fuente": "dof.gob.mx",
            }
        }

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_download_nota_imagenes_rejects_a_dofweb_only_nota(self, mock_sidof, mock_web):
        self._mock_dofweb_only_nota(mock_sidof, mock_web)

        with self.assertRaises(ValueError):
            download_nota_imagenes(4997808, self.outdir)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_download_nota_pdf_rejects_a_dofweb_only_nota(self, mock_sidof, mock_web):
        self._mock_dofweb_only_nota(mock_sidof, mock_web)

        with self.assertRaises(ValueError):
            download_nota_pdf(4997808, self.outdir)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_download_nota_imagen_o_pdf_rejects_a_dofweb_only_nota(self, mock_sidof, mock_web):
        self._mock_dofweb_only_nota(mock_sidof, mock_web)

        with self.assertRaises(ValueError):
            download_nota_imagen_o_pdf(4997808, self.outdir)

    @patch("dofjson.api.dofweb.get_nota")
    @patch("dofjson.api.sidof.get_nota")
    def test_download_nota_rejects_a_dofweb_only_nota(self, mock_sidof, mock_web):
        self._mock_dofweb_only_nota(mock_sidof, mock_web)

        with self.assertRaises(ValueError):
            download_nota(4997808, self.outdir)


class TestDownloadNota(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_nota")
    def test_saves_json_when_content_exists(self, mock_get_nota, mock_get_imagenes):
        mock_get_nota.return_value = {
            "Nota": {"codNota": 5793719, "cadenaContenido": "<HTML>...</HTML>"}
        }

        dests = download_nota(5793719, self.outdir)

        self.assertEqual(dests, [self.outdir / "nota-5793719.json"])
        self.assertTrue(dests[0].exists())
        mock_get_imagenes.assert_not_called()

    @patch("dofjson.api.sidof.download_imagen")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_imagen")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_imagen")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.download_nota_imagenes")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_imagen")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_notas")
    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.get_nota")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.download_imagen")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
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

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
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

    @patch("dofjson.api.sidof.get_nota")
    def test_returns_cached_imagenes_without_any_network_call(self, mock_get_nota):
        existente = self.outdir / "nota-5793655-20260715-080-U-000.jpg"
        existente.write_bytes(b"\xff\xd8\xff ya estaba aqui")

        dests = download_nota_imagen_o_pdf(5793655, self.outdir)

        mock_get_nota.assert_not_called()
        self.assertEqual(dests, [existente])

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_imagenes")
    @patch("dofjson.api.sidof.get_notas")
    def test_falls_back_to_the_edicion_when_the_imagenes_listing_404s(
        self, mock_get_notas, mock_get_imagenes, mock_download_pdf
    ):
        # Reproduces the reported bug: SIDOF's imagenesFsRecurso endpoint
        # can 404 outright for a codDiario it has no listing for at all,
        # which surfaces as requests.HTTPError (not the ValueError
        # download_nota_imagenes raises for a page with no matching image)
        # and used to propagate uncaught instead of falling back to the pdf.
        nota = {
            "codNota": 4723515, "cadenaContenido": "", "codDiario": 203595,
            "fecha": "01-07-2019", "pagina": 12, "codEdicion": "MAT",
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 4723515, "pagina": 12}]
        }
        response = MagicMock(status_code=404)
        mock_get_imagenes.side_effect = requests.HTTPError(
            "404 Client Error: Not Found for url: "
            "https://sidof.segob.gob.mx/dof/sidof/imagenesFsRecurso/"
            "obtieneImagenesFS/203595",
            response=response,
        )
        mock_download_pdf.side_effect = lambda cod_diario, dest, **kw: dest.write_bytes(b"%PDF-edicion")

        dests = download_nota_imagen_o_pdf(4723515, self.outdir, nota=nota)

        self.assertEqual(dests, [self.outdir / "edicion-203595.pdf"])
        mock_download_pdf.assert_called_once()

    @patch("dofjson.api.sidof.download_pdf")
    @patch("dofjson.api.sidof.get_imagenes")
    def test_returns_cached_edicion_without_retrying_a_failed_imagenes_lookup(
        self, mock_get_imagenes, mock_download_pdf
    ):
        # Reproduces the second reported bug: once the pdf fallback has
        # already run once for this codDiario, a later call must not
        # re-attempt (and re-fail) the imagenes lookup.
        nota = {
            "codNota": 4723515, "cadenaContenido": "", "codDiario": 203595,
            "fecha": "01-07-2019", "pagina": 12, "codEdicion": "MAT",
        }
        cacheada = self.outdir / "edicion-203595.pdf"
        cacheada.write_bytes(b"%PDF-edicion ya estaba aqui")

        dests = download_nota_imagen_o_pdf(4723515, self.outdir, nota=nota)

        self.assertEqual(dests, [cacheada])
        mock_get_imagenes.assert_not_called()
        mock_download_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
