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


class TestCatalogKey(unittest.TestCase):
    def test_usa_abrev_cuando_esta_disponible(self):
        self.assertEqual(catalog.catalog_key({"abrev": "ccf", "nombre": "Codigo Civil Federal"}), "ccf")

    def test_exige_abrev(self):
        # Same reason as `slug_instrumento` (issue #189).
        with self.assertRaises(KeyError):
            catalog.catalog_key({"nombre": "Convenio 107 OIT"})


class TestMergeCatalogOverrides(unittest.TestCase):
    def test_conserva_nombre_scjn_de_la_entrada_correspondiente(self):
        nuevo = [{"nombre": "IMPUESTO sobre Servicios...", "abrev": "lisipl"}]
        previo = [
            {"nombre": "IMPUESTO sobre Servicios...", "abrev": "lisipl", "nombre_scjn": "LEY DEL IMPUESTO..."}
        ]

        fusionado = catalog.merge_catalog_overrides(nuevo, previo)

        self.assertEqual(fusionado[0]["nombre_scjn"], "LEY DEL IMPUESTO...")
        # nombre/abrev are the freshly re-downloaded ones, untouched.
        self.assertEqual(fusionado[0]["nombre"], "IMPUESTO sobre Servicios...")

    def test_no_inventa_nombre_scjn_para_una_entrada_sin_override_previo(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        previo = [{"nombre": "LEY de Amparo", "abrev": "la"}]

        fusionado = catalog.merge_catalog_overrides(nuevo, previo)

        self.assertNotIn("nombre_scjn", fusionado[0])

    def test_empareja_por_abrev_aunque_el_nombre_cambie_de_forma(self):
        nuevo = [{"nombre": "LEY Federal de Cine y el Audiovisual", "abrev": "lfca"}]
        previo = [{"nombre": "LEY de Cine (nombre distinto)", "abrev": "lfca", "nombre_scjn": "X"}]

        fusionado = catalog.merge_catalog_overrides(nuevo, previo)

        self.assertEqual(fusionado[0]["nombre_scjn"], "X")

    def test_catalogo_previo_none_regresa_el_nuevo_intacto(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        self.assertEqual(catalog.merge_catalog_overrides(nuevo, None), nuevo)

    def test_catalogo_previo_vacio_regresa_el_nuevo_intacto(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        self.assertEqual(catalog.merge_catalog_overrides(nuevo, []), nuevo)


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


class TestMergeCatalogWithPrevious(unittest.TestCase):
    """The seed overlaid on the catalogue already on disk (issue #186)."""

    SEED = [
        {"abrev": "lft", "nombre": "LEY Federal del Trabajo"},
        {"abrev": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual"},
    ]

    def test_ordena_por_slug_que_es_el_orden_del_indice_del_release(self):
        catalogo, faltantes = catalog.merge_catalog_with_previous(self.SEED, None)

        self.assertEqual([e["abrev"] for e in catalogo], ["lfca", "lft"])
        self.assertEqual(faltantes, [])

    def test_conserva_el_abrev_previo_verbatim_aunque_el_slug_lo_normalice(self):
        # `lif_2026` in the catalogue is `lif-2026` in the release. The
        # `abrev` is the asset name of an already published law, so it is
        # the release's slug that gives way, not the other way round.
        previo = [{"nombre": "LEY de Ingresos vieja", "abrev": "lif_2026"}]

        catalogo, faltantes = catalog.merge_catalog_with_previous(
            [{"abrev": "lif-2026", "nombre": "LEY de Ingresos de la Federacion"}], previo
        )

        self.assertEqual(faltantes, [])
        self.assertEqual(catalogo, [{"nombre": "LEY de Ingresos de la Federacion",
                                     "abrev": "lif_2026"}])

    def test_el_catalogo_anterior_es_el_piso_y_lo_ausente_se_reporta(self):
        previo = [{"nombre": "ORDENANZA General de la Armada", "abrev": "oga"}]

        catalogo, faltantes = catalog.merge_catalog_with_previous(self.SEED, previo)

        self.assertEqual([e["abrev"] for e in catalogo], ["lfca", "lft", "oga"])
        self.assertEqual([e["abrev"] for e in faltantes], ["oga"])

    def test_conserva_los_campos_escritos_a_mano_del_catalogo_anterior(self):
        previo = [{"nombre": "viejo", "abrev": "lft", "nombre_scjn": "LEY FEDERAL DEL TRABAJO"}]

        catalogo, _ = catalog.merge_catalog_with_previous(self.SEED, previo)
        lft, = [e for e in catalogo if e["abrev"] == "lft"]

        self.assertEqual(lft["nombre_scjn"], "LEY FEDERAL DEL TRABAJO")
        self.assertEqual(lft["nombre"], "LEY Federal del Trabajo")


class TestApplyActualizado(unittest.TestCase):
    def test_gana_la_fecha_mas_nueva_de_todas_las_fuentes(self):
        catalogo = [{"nombre": "LEY Federal del Trabajo", "abrev": "lft"}]

        resultado = catalog.apply_actualizado(
            catalogo, {"lft": "2025-11-14"}, {"lft": "2026-05-14"}
        )

        self.assertEqual(resultado[0]["actualizado"], "2026-05-14")

    def test_sin_fecha_el_campo_queda_ausente_no_en_none(self):
        catalogo = [{"nombre": "LEY", "abrev": "lfcpq", "actualizado": "2020-01-01"}]

        resultado = catalog.apply_actualizado(catalogo, {}, {})

        self.assertNotIn("actualizado", resultado[0])

    def test_conserva_la_posicion_del_campo_en_la_entrada(self):
        catalogo = [{"nombre": "LEY", "abrev": "lft", "actualizado": "2020-01-01",
                     "nombre_scjn": "LEY"}]

        resultado = catalog.apply_actualizado(catalogo, {"lft": "2026-05-14"})

        self.assertEqual(list(resultado[0]), ["nombre", "abrev", "actualizado",
                                              "nombre_scjn"])

    def test_no_muta_el_catalogo_recibido(self):
        catalogo = [{"nombre": "LEY", "abrev": "lft"}]

        catalog.apply_actualizado(catalogo, {"lft": "2026-05-14"})

        self.assertNotIn("actualizado", catalogo[0])


class TestReconstruccionDelCatalogo(unittest.TestCase):
    """The three steps `extract_scjn_titles.py` composes, in order, over the
    one case issue #186 requires to survive a rebuild: `lisipl`'s manual
    `nombre_scjn`. It is the only entry in the real catalogue that has one,
    and it exists precisely because no automated step can re-derive it."""

    LISIPL = (
        "IMPUESTO sobre Servicios Expresamente Declarados de Interés Público por Ley, "
        "en los que Intervengan Empresas Concesionarias de Bienes del Dominio Directo "
        "de la Nación (LEY que establece, reforma y adiciona las disposiciones "
        "relativas a diversos impuestos)"
    )
    NOMBRE_SCJN = (
        "LEY DEL IMPUESTO SOBRE SERVICIOS EXPRESAMENTE DECLARADOS DE INTERES PUBLICO "
        "POR LEY"
    )

    def test_el_override_nombre_scjn_sobrevive_a_una_reconstruccion(self):
        previo = [{"nombre": self.LISIPL, "abrev": "lisipl",
                   "nombre_scjn": self.NOMBRE_SCJN}]
        seed = [{"abrev": "lisipl", "nombre": self.LISIPL}]

        catalogo, faltantes = catalog.merge_catalog_with_previous(seed, previo)
        catalogo = catalog.apply_actualizado(catalogo, {"lisipl": "2026-01-15"})
        catalogo = catalog.merge_catalog_overrides(catalogo, previo)

        self.assertEqual(faltantes, [])
        self.assertEqual(catalogo, [{
            "nombre": self.LISIPL,
            "abrev": "lisipl",
            "nombre_scjn": self.NOMBRE_SCJN,
            "actualizado": "2026-01-15",
        }])
