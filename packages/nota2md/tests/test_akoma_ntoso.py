import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from nota2md.akoma_ntoso import AKN_NS, markdown_to_akoma_ntoso

_NS = {"akn": AKN_NS}


def _texto(elemento) -> str:
    """`elemento`'s own text plus every descendant's text/tail, concatenated —
    enough to check content ended up in the right place regardless of exactly
    how it is split across <p>/<b> children."""
    return "".join(elemento.itertext())


class TestMarkdownToAkomaNtoso(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _convierte(self, markdown: str, **kwargs) -> ET.Element:
        md_path = self.outdir / "nota-1.md"
        md_path.write_text(markdown, encoding="utf-8")
        dest = markdown_to_akoma_ntoso(md_path, self.outdir, **kwargs)
        self.assertEqual(dest, self.outdir / "nota-1.akn.xml")
        return ET.parse(dest).getroot()

    def test_estructura_basica(self):
        root = self._convierte(
            "# DECRETO por el que se expide la Ley de Prueba.\n\n"
            "## Al margen un sello.\n\n"
            "Que el Honorable Congreso decreta:\n\n"
            "**Artículo 1.** Texto original del artículo primero.\n\n"
            "**Artículo 2.** Texto original del artículo segundo.\n\n"
            "## Transitorios\n\n"
            "**Único.** Entrará en vigor al día siguiente.\n"
        )
        self.assertEqual(root.tag, f"{{{AKN_NS}}}akomaNtoso")
        act = root.find("akn:act", _NS)
        self.assertEqual(act.get("name"), "decreto")

        preamble = act.find("akn:body/akn:preamble", _NS)
        self.assertIn("Al margen un sello.", _texto(preamble))
        self.assertIn("Que el Honorable Congreso decreta:", _texto(preamble))

        articulos = act.findall("akn:body/akn:article", _NS)
        self.assertEqual([a.get("eId") for a in articulos], ["art_1", "art_2"])
        self.assertEqual(articulos[0].find("akn:num", _NS).text, "Artículo 1")
        self.assertIn("Texto original del artículo primero.", _texto(articulos[0]))
        # The "Artículo N." lead-in is not duplicated inside <content>.
        self.assertNotIn("Artículo 1.", _texto(articulos[0].find("akn:content", _NS)))

        transitorios = act.find("akn:body/akn:section", _NS)
        self.assertEqual(transitorios.get("eId"), "transitorios")
        self.assertEqual(transitorios.find("akn:heading", _NS).text, "Transitorios")
        contenido = _texto(transitorios.find("akn:content", _NS))
        self.assertIn("Entrará en vigor al día siguiente.", contenido)
        # The "Transitorios" heading block itself is not duplicated as content.
        self.assertNotIn("Transitorios", contenido)

    def test_negritas_se_preservan_como_elemento_b(self):
        root = self._convierte(
            "**Artículo 1.** Texto con una **palabra en negrita** dentro.\n"
        )
        p = root.find("akn:act/akn:body/akn:article/akn:content/akn:p", _NS)
        b = p.find("akn:b", _NS)
        self.assertIsNotNone(b)
        self.assertEqual(b.text, "palabra en negrita")

    def test_sin_fecha_ni_numero_usa_placeholder_explicito(self):
        root = self._convierte("**Artículo 1.** Texto.\n")
        uri = root.find(
            "akn:act/akn:meta/akn:identification/akn:FRBRWork/akn:FRBRuri", _NS
        )
        self.assertEqual(uri.get("value"), "/akn/mx/act/decreto/sin-fecha/sin-numero")
        self.assertIsNone(
            root.find(
                "akn:act/akn:meta/akn:identification/akn:FRBRWork/akn:FRBRdate", _NS
            )
        )

    def test_fecha_y_numero_se_reflejan_en_la_iri(self):
        root = self._convierte(
            "**Artículo 1.** Texto.\n", fecha="2006-06-28", numero="1", subtipo="decreto"
        )
        work = root.find("akn:act/akn:meta/akn:identification/akn:FRBRWork", _NS)
        self.assertEqual(
            work.find("akn:FRBRuri", _NS).get("value"),
            "/akn/mx/act/decreto/2006-06-28/1",
        )
        self.assertEqual(work.find("akn:FRBRdate", _NS).get("date"), "2006-06-28")

    def test_sin_transitorios_no_agrega_section(self):
        root = self._convierte("**Artículo 1.** Texto sin transitorios.\n")
        self.assertIsNone(root.find("akn:act/akn:body/akn:section", _NS))

    def test_nombre_ley_selecciona_el_instrumento(self):
        markdown = (
            "**Artículo Primero.- Se expide la Ley A.**\n\n"
            "**Artículo 1.** Texto de la Ley A.\n\n"
            "**Artículo Segundo.- Se expide la Ley B.**\n\n"
            "**Artículo 1.** Texto de la Ley B.\n"
        )
        root = self._convierte(markdown, nombre_ley="Ley B")
        contenido = _texto(root.find("akn:act/akn:body/akn:article", _NS))
        self.assertIn("Texto de la Ley B.", contenido)
