"""legal_provisions as a dispatcher between the SCJN corpus and the DOF
(issue #117, Paso 4): which path each `source` takes, what each one names its
output file, that the SCJN's provenance header survives, and that a release
that cannot answer falls back to the DOF instead of raising."""

import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import requests

from nota2md import cache
from nota2md.builder import legal_provisions

# The SCJN's own snapshot text, header and all -- what the corpus actually
# ships, and what has to reach the output file unchanged.
SNAPSHOT_SCJN = (
    "fuente: scjn\n"
    "ordenamiento: LEY Federal de Cine y el Audiovisual\n"
    "fecha_publicacion: 05-01-1999\n"
    "categoria: Ley\n"
    "\n"
    "**TEXTO VIGENTE DE LA LEY COMPLETA.**\n"
)

# A note the DOF serves as HTML -- the fallback path's own input.
NOTA_HTML = {
    "codNota": 4967917,
    "titulo": "DECRETO por el que se reforma la Ley Federal de Cine",
    "codEdicion": "MAT",
    "fecha": "05-01-1999",
    "cadenaContenido": (
        "<body><div><h1 class='Titulo_1'><span>DECRETO</span></h1>"
        "<div class='Texto'><span>Cuerpo del decreto.</span></div></div></body>"
    ),
}


class TestDespachador(unittest.TestCase):
    def setUp(self):
        self.outdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.outdir))

    def _con_cobertura(self, **kwargs):
        """Patch the SCJN resolver as if the release covered 4967917."""
        return patch(
            "nota2md.scjn.snapshot_de_codNota",
            return_value=("lfca", "05-01-1999.md", SNAPSHOT_SCJN),
            **kwargs,
        )

    # --- source="auto": la SCJN es el default ---------------------------

    def test_auto_con_cobertura_escribe_la_ley_completa_de_la_scjn(self):
        with self._con_cobertura():
            destino = legal_provisions(4967917, self.outdir)

        self.assertEqual(destino.name, "lfca-05-01-1999.md")
        self.assertIn("**TEXTO VIGENTE DE LA LEY COMPLETA.**", destino.read_text())

    def test_el_archivo_de_la_scjn_conserva_su_cabecera_de_procedencia(self):
        # Quien lea el resultado tiene que poder saber, del archivo solo, que
        # no es texto del DOF: la SCJN no es fuente oficial.
        with self._con_cobertura():
            destino = legal_provisions(4967917, self.outdir)

        self.assertTrue(destino.read_text().startswith("fuente: scjn"))

    def test_la_ruta_scjn_no_hace_ni_una_llamada_a_sidof(self):
        with self._con_cobertura(), patch("nota2md.builder.fetch_nota") as mock_nota:
            legal_provisions(4967917, self.outdir)

        mock_nota.assert_not_called()

    def test_el_sufijo_de_fechas_repetidas_viaja_en_el_nombre_del_archivo(self):
        # Issue #113: dos reformas el mismo dia se distinguen por `-N`, asi
        # que `<slug>-<fecha>.md` sigue siendo unico.
        with patch(
            "nota2md.scjn.snapshot_de_codNota",
            return_value=("cpeum", "01-04-2025-2.md", SNAPSHOT_SCJN),
        ):
            destino = legal_provisions(4967917, self.outdir)

        self.assertEqual(destino.name, "cpeum-01-04-2025-2.md")

    def test_auto_sin_cobertura_cae_al_dof(self):
        with patch("nota2md.scjn.snapshot_de_codNota", return_value=None), \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML):
            destino = legal_provisions(4967917, self.outdir)

        self.assertEqual(destino.name, "nota-4967917.md")
        self.assertIn("Cuerpo del decreto", destino.read_text())

    # --- source="dof" y los caminos concretos del DOF -------------------

    def test_dof_salta_la_scjn_aunque_haya_cobertura(self):
        with self._con_cobertura() as mock_scjn, \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML):
            destino = legal_provisions(4967917, self.outdir, source="dof")

        mock_scjn.assert_not_called()
        self.assertEqual(destino.name, "nota-4967917.md")
        self.assertIn("Cuerpo del decreto", destino.read_text())

    def test_html_tambien_salta_la_scjn(self):
        # Pedir un camino concreto del DOF ya es pedir la fuente original.
        with self._con_cobertura() as mock_scjn, \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML):
            destino = legal_provisions(4967917, self.outdir, source="html")

        mock_scjn.assert_not_called()
        self.assertEqual(destino.name, "nota-4967917.md")

    def test_dof_sin_html_sigue_eligiendo_el_camino_de_imagen(self):
        # "dof" solo significa "no la SCJN": de ahi en adelante se comporta
        # exactamente como el "auto" de antes de este issue.
        sin_html = {**NOTA_HTML, "cadenaContenido": None}
        with patch("nota2md.builder.fetch_nota", return_value=sin_html), \
                patch("dofjson.download_nota_imagenes", return_value=[]) as mock_img, \
                patch("dofjson.get_notas", return_value={"NotasMatutinas": []}), \
                patch("nota2md.builder._load_converter") as mock_conv, \
                patch("nota2md.builder._cut_and_write", return_value=Path("x")):
            legal_provisions(4967917, self.outdir, source="dof")

        mock_img.assert_called_once()
        mock_conv.assert_called_once_with("convert_images_to_markdown")

    def test_un_source_desconocido_sigue_siendo_un_error(self):
        with self.assertRaises(ValueError):
            legal_provisions(4967917, self.outdir, source="scjn")

    # --- el release que no puede contestar ------------------------------

    def test_un_asset_no_publicado_cae_al_dof_con_advertencia(self):
        with patch("nota2md.scjn.snapshot_de_codNota", side_effect=KeyError("sin asset")), \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML), \
                warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always")
            destino = legal_provisions(4967917, self.outdir)

        self.assertEqual(destino.name, "nota-4967917.md")
        self.assertEqual(len(avisos), 1)
        self.assertIn("DOF", str(avisos[0].message))

    def test_un_fallo_de_red_del_release_cae_al_dof_con_advertencia(self):
        with patch(
            "nota2md.scjn.snapshot_de_codNota",
            side_effect=requests.ConnectionError("sin red"),
        ), patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML), \
                warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always")
            destino = legal_provisions(4967917, self.outdir)

        self.assertEqual(destino.name, "nota-4967917.md")
        self.assertEqual(len(avisos), 1)

    def test_un_codnota_ambiguo_no_se_esconde_cayendo_al_dof(self):
        # A diferencia de "sin cobertura" o de un fallo de red, esta es
        # contestable: se responde pasando instrumento=. Regresar el decreto
        # del DOF en silencio seria dar otra cosa sin decirlo.
        with patch(
            "nota2md.scjn.snapshot_de_codNota",
            side_effect=ValueError("reforma mas de un instrumento"),
        ), self.assertRaises(ValueError):
            legal_provisions(4967917, self.outdir)

    # --- los parametros que la ruta de la SCJN si usa -------------------

    def test_instrumento_y_la_cache_se_pasan_al_resolvedor(self):
        with self._con_cobertura() as mock_scjn:
            legal_provisions(
                4967917, self.outdir, instrumento="lfca",
                cache_dir=self.outdir / "cache", refrescar=True,
            )

        _, kwargs = mock_scjn.call_args
        self.assertEqual(kwargs["instrumento"], "lfca")
        self.assertEqual(kwargs["cache_dir"], self.outdir / "cache")
        self.assertTrue(kwargs["refrescar"])

    def test_los_parametros_del_ocr_se_ignoran_en_la_ruta_scjn_sin_fallar(self):
        # Un lote mixto los pasa siempre; no deben provocar un error aqui.
        with self._con_cobertura():
            destino = legal_provisions(
                4967917, self.outdir, nota=NOTA_HTML,
                notas_del_dia={"NotasMatutinas": []}, keep_pages=True,
                min_confidence=0.9, converter=object(),
            )

        self.assertEqual(destino.name, "lfca-05-01-1999.md")


class TestSinOutdir(unittest.TestCase):
    """`legal_provisions(codNota)` with no `outdir`: the note is materialized
    inside `nota2md`'s cache and its path returned (issue #165)."""

    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.cache_dir))

    def _localizado(self, **kwargs):
        """Patch the reverse index as if the release covered 4967917 with
        `lfca/05-01-1999.md`."""
        return patch(
            "nota2md.scjn.localiza_codNota",
            return_value=("lfca", "05-01-1999.md"),
            **kwargs,
        )

    def _extraccion(self, **kwargs):
        return patch(
            "nota2md.scjn.markdown_de_snapshot",
            return_value=SNAPSHOT_SCJN,
            **kwargs,
        )

    def test_devuelve_la_ruta_en_el_cache_y_escribe_ahi(self):
        with self._localizado(), self._extraccion():
            destino = legal_provisions(4967917, cache_dir=self.cache_dir)

        self.assertEqual(
            destino, self.cache_dir / "scjn-leyes" / "md" / "lfca-05-01-1999.md"
        )
        self.assertTrue(destino.read_text().startswith("fuente: scjn"))

    def test_la_segunda_llamada_no_abre_el_tarball(self):
        with self._localizado(), self._extraccion():
            primera = legal_provisions(4967917, cache_dir=self.cache_dir)

        with self._localizado(), self._extraccion() as mock_md:
            segunda = legal_provisions(4967917, cache_dir=self.cache_dir)

        mock_md.assert_not_called()
        self.assertEqual(primera, segunda)

    def test_refrescar_vuelve_a_extraer_sobre_lo_que_ya_estaba(self):
        with self._localizado(), self._extraccion():
            legal_provisions(4967917, cache_dir=self.cache_dir)

        with self._localizado(), self._extraccion() as mock_md:
            legal_provisions(4967917, cache_dir=self.cache_dir, refrescar=True)

        mock_md.assert_called_once()

    def test_un_parcial_no_cuenta_como_acierto(self):
        # Un proceso interrumpido a media escritura no puede dejar un Markdown
        # truncado que la siguiente llamada de por bueno.
        md = self.cache_dir / "scjn-leyes" / "md"
        md.mkdir(parents=True)
        (md / ("lfca-05-01-1999.md" + cache.SUFIJO_PARCIAL)).write_text("a medias")

        with self._localizado(), self._extraccion() as mock_md:
            destino = legal_provisions(4967917, cache_dir=self.cache_dir)

        mock_md.assert_called_once()
        self.assertEqual(destino.name, "lfca-05-01-1999.md")
        self.assertTrue(destino.read_text().startswith("fuente: scjn"))

    def test_sin_cache_y_sin_outdir_no_hay_donde_escribir(self):
        with self._localizado() as mock_loc, self.assertRaises(ValueError):
            legal_provisions(4967917, cache_dir=None)

        # Y falla antes de tocar la red, no despues de bajar el indice.
        mock_loc.assert_not_called()

    def test_con_outdir_explicito_nada_cambia(self):
        outdir = self.cache_dir / "salida"
        with patch(
            "nota2md.scjn.snapshot_de_codNota",
            return_value=("lfca", "05-01-1999.md", SNAPSHOT_SCJN),
        ), self._localizado() as mock_loc:
            destino = legal_provisions(4967917, outdir, cache_dir=self.cache_dir)

        mock_loc.assert_not_called()
        self.assertEqual(destino, outdir / "lfca-05-01-1999.md")

    def test_sin_cobertura_scjn_la_nota_del_dof_va_al_subdirectorio_dof(self):
        with patch("nota2md.scjn.localiza_codNota", return_value=None), \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML):
            destino = legal_provisions(4967917, cache_dir=self.cache_dir)

        self.assertEqual(destino, self.cache_dir / "dof" / "nota-4967917.md")
        self.assertIn("Cuerpo del decreto", destino.read_text())

    def test_source_dof_sin_outdir_tambien_va_al_subdirectorio_dof(self):
        with self._localizado() as mock_loc, \
                patch("nota2md.builder.fetch_nota", return_value=NOTA_HTML):
            destino = legal_provisions(
                4967917, source="dof", cache_dir=self.cache_dir
            )

        mock_loc.assert_not_called()
        self.assertEqual(destino, self.cache_dir / "dof" / "nota-4967917.md")
