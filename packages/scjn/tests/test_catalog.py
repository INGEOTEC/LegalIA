import tempfile
import unittest
from pathlib import Path

import scjn.catalog as catalog


class TestSlugInstrumento(unittest.TestCase):
    def test_usa_abrev_cuando_esta_disponible(self):
        self.assertEqual(catalog.slug_instrumento({"abrev": "cpeum", "nombre": "CONSTITUCIÓN"}), "cpeum")

    def test_exige_abrev(self):
        # Issue #189: with `leyes` the only collection left, every catalogue
        # entry has an `abrev`, so falling back to the `nombre` would only
        # ever hide a malformed entry behind a plausible-looking slug.
        with self.assertRaises(KeyError):
            catalog.slug_instrumento({"nombre": "Convenio 107 OIT"})

    def test_slugify_forma_un_slug_de_cualquier_texto(self):
        self.assertEqual(catalog.slugify("Convenio 107 OIT"), "convenio-107-oit")


class TestSearchName(unittest.TestCase):
    def test_usa_nombre_scjn_cuando_esta_presente(self):
        entrada = {"nombre": "IMPUESTO sobre Servicios... (LEY que...)", "nombre_scjn": "LEY DEL IMPUESTO..."}
        self.assertEqual(catalog.search_name(entrada), "LEY DEL IMPUESTO...")

    def test_recae_en_nombre_sin_override(self):
        entrada = {"nombre": "LEY de Amparo"}
        self.assertEqual(catalog.search_name(entrada), "LEY de Amparo")


class TestIsoDateFromNote(unittest.TestCase):
    def test_convierte_fecha_dd_mm_yyyy_a_iso(self):
        self.assertEqual(catalog.iso_date_from_note({"fecha": "24-05-2026"}), "2026-05-24")

    def test_regresa_none_sin_fecha(self):
        self.assertIsNone(catalog.iso_date_from_note({}))


class TestInstrumentoUpToDate(unittest.TestCase):
    def test_no_salta_sin_fecha_de_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(catalog.instrument_up_to_date(destino, "2020-01-01", None))

    def test_no_salta_sin_snapshots_en_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)  # vacio -- nunca se le encontro nada en la SCJN
            self.assertFalse(catalog.instrument_up_to_date(destino, "2020-01-01", "2026-01-01"))

    def test_no_salta_sin_actualizado_en_el_catalogo(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(catalog.instrument_up_to_date(destino, None, "2026-01-01"))

    def test_salta_cuando_ya_tiene_snapshots_y_esta_al_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertTrue(catalog.instrument_up_to_date(destino, "2020-01-01", "2026-01-01"))

    def test_no_salta_cuando_actualizado_es_posterior_al_corpus(self):
        # Caso lfca (issue #124): una ley reformada despues del ultimo
        # rastreo completo se re-intenta en cada refresh.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(catalog.instrument_up_to_date(destino, "2026-05-24", "2026-01-01"))


class TestMintAbrev(unittest.TestCase):
    """The one identifier no source assigns (issue #186)."""

    def test_son_las_iniciales_sin_las_palabras_vacias(self):
        self.assertEqual(catalog.mint_abrev("LEY Federal de Cine y el Audiovisual"), "lfca")
        self.assertEqual(
            catalog.mint_abrev("CÓDIGO Nacional de Procedimientos Civiles y Familiares"),
            "cnpcf",
        )

    def test_es_determinista_y_no_depende_de_los_acentos(self):
        self.assertEqual(
            catalog.mint_abrev("CÓDIGO Nacional"), catalog.mint_abrev("CODIGO NACIONAL")
        )

    def test_una_colision_recibe_un_sufijo_numerado(self):
        self.assertEqual(
            catalog.mint_abrev("LEY Federal de Cine y el Audiovisual", {"lfca"}), "lfca-2"
        )
        self.assertEqual(
            catalog.mint_abrev("LEY Federal de Cine y el Audiovisual", {"lfca", "lfca-2"}),
            "lfca-3",
        )

    def test_el_resultado_ya_es_un_slug(self):
        # Unlike the 14 historical `abrev` with an underscore, a minted one
        # never has to be normalized to become the release's asset name.
        for nombre in ("LEY de Ingresos de la Federación para 2026",
                       "PRESUPUESTO de Egresos de la Federación"):
            abrev = catalog.mint_abrev(nombre)
            self.assertEqual(catalog.slug_instrumento({"abrev": abrev}), abrev)
