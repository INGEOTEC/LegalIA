import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import xmlschema

from nota2md.akoma_ntoso import AKN_NS, akoma_ntoso_to_markdown, markdown_to_akoma_ntoso

_NS = {"akn": AKN_NS}
# Vendored from the OASIS Standard (29 August 2018) itself — see
# http://docs.oasis-open.org/legaldocml/akn-core/v1.0/os/part2-specs/schemas/ —
# not fetched at test time so this check does not depend on the network or
# on OASIS's docs site staying up.
_XSD = Path(__file__).parent / "fixtures" / "akn" / "akomantoso30.xsd"


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

        preamble = act.find("akn:preamble", _NS)
        self.assertIn("Al margen un sello.", _texto(preamble))
        self.assertIn("Que el Honorable Congreso decreta:", _texto(preamble))

        articulos = act.findall("akn:body/akn:article", _NS)
        self.assertEqual([a.get("eId") for a in articulos], ["art_1", "art_2"])
        self.assertEqual(articulos[0].find("akn:num", _NS).text, "Artículo 1")
        self.assertIn("Texto original del artículo primero.", _texto(articulos[0]))
        # The "Artículo N." lead-in is not duplicated inside <content>.
        self.assertNotIn("Artículo 1.", _texto(articulos[0].find("akn:content", _NS)))

        transitorios = act.find("akn:body/akn:section", _NS)
        self.assertEqual(transitorios.get("eId"), "sec_transitorios")
        self.assertEqual(transitorios.get("refersTo"), "#transitorios")
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

    def test_fracciones_e_incisos_se_anidan_como_paragraph_y_point(self):
        root = self._convierte(
            "**Artículo 1.** Encabezado del artículo.\n\n"
            "**I.** Contenido de la fracción I.\n\n"
            "**II.** Encabezado de la fracción II.\n\n"
            "**a)** Contenido del inciso a).\n\n"
            "**b)** Contenido del inciso b).\n\n"
            "**III.** Contenido de la fracción III.\n"
        )
        articulo = root.find("akn:act/akn:body/akn:article", _NS)
        self.assertIsNone(articulo.find("akn:content", _NS))  # flat <content> and nested hierarchy never mix
        self.assertIn("Encabezado del artículo.", _texto(articulo.find("akn:intro", _NS)))

        fracciones = articulo.findall("akn:paragraph", _NS)
        self.assertEqual([f.get("eId") for f in fracciones], ["art_1__I", "art_1__II", "art_1__III"])
        self.assertEqual(fracciones[0].find("akn:num", _NS).text, "I.")
        self.assertIn("Contenido de la fracción I.", _texto(fracciones[0].find("akn:content", _NS)))

        incisos = fracciones[1].findall("akn:point", _NS)
        self.assertEqual([i.get("eId") for i in incisos], ["art_1__II__a", "art_1__II__b"])
        self.assertIn("Encabezado de la fracción II.", _texto(fracciones[1].find("akn:intro", _NS)))
        self.assertIn("Contenido del inciso a).", _texto(incisos[0].find("akn:content", _NS)))

    def test_etiqueta_de_fraccion_repetida_no_colisiona_de_eid(self):
        # A DOF article can restate "I." under two separate "I. a X." lists.
        root = self._convierte(
            "**Artículo 1.** Encabezado.\n\n"
            "**I.** Primera lista, fracción I.\n\n"
            "**I.** Segunda lista, fracción I otra vez.\n"
        )
        fracciones = root.findall("akn:act/akn:body/akn:article/akn:paragraph", _NS)
        eids = [f.get("eId") for f in fracciones]
        self.assertEqual(eids, ["art_1__I", "art_1__I_2"])
        self.assertEqual(len(eids), len(set(eids)))

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


class TestValidacionContraElEsquemaOficial(unittest.TestCase):
    """Validates the generated XML against the real OASIS Standard XSD
    (akomantoso30.xsd), not just against our own idea of its structure —
    the schema review issue #91 asked for, made into a permanent check
    instead of a one-off manual run."""

    @classmethod
    def setUpClass(cls):
        cls.schema = xmlschema.XMLSchema(str(_XSD))

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _convierte(self, markdown: str, **kwargs) -> Path:
        md_path = self.outdir / "nota-1.md"
        md_path.write_text(markdown, encoding="utf-8")
        return markdown_to_akoma_ntoso(md_path, self.outdir, **kwargs)

    def test_valido_con_fecha(self):
        dest = self._convierte(
            "**Artículo 1.** Texto original del artículo primero.\n\n"
            "## Transitorios\n\n"
            "**Único.** Entrará en vigor al día siguiente.\n",
            fecha="2006-06-28",
            numero="1",
        )
        self.schema.validate(str(dest))

    def test_valido_con_fracciones_e_incisos_repetidos(self):
        dest = self._convierte(
            "**Artículo 1.** Encabezado.\n\n"
            "**I.** Primera lista, fracción I.\n\n"
            "**II.** Encabezado de la fracción II.\n\n"
            "**a)** Contenido del inciso a).\n\n"
            "**I.** Segunda lista, fracción I otra vez.\n",
            fecha="2024-09-15",
        )
        self.schema.validate(str(dest))

    def test_invalido_sin_fecha(self):
        """FRBRdate is mandatory at every FRBR level (coreProperties in the
        XSD) and typed `xsd:date` — there is no placeholder string that
        could stand in for a missing one, so omitting `fecha` genuinely
        produces schema-invalid output, not just an unresolvable IRI. This
        pins that down as a real, tested constraint instead of a guess."""
        dest = self._convierte("**Artículo 1.** Texto sin fecha.\n")
        with self.assertRaises(xmlschema.XMLSchemaValidationError):
            self.schema.validate(str(dest))


class TestAkomaNtosoToMarkdown(unittest.TestCase):
    """`akoma_ntoso_to_markdown` — the other direction issue #93 asked for."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _ida_y_vuelta(self, markdown: str, **kwargs) -> str:
        """`markdown` converted to Akoma Ntoso and straight back — the
        Markdown `akoma_ntoso_to_markdown` reconstructs from that XML."""
        md_path = self.outdir / "nota-1.md"
        md_path.write_text(markdown, encoding="utf-8")
        xml_path = markdown_to_akoma_ntoso(md_path, self.outdir, **kwargs)
        md_de_vuelta = akoma_ntoso_to_markdown(xml_path, self.outdir)
        self.assertEqual(md_de_vuelta, self.outdir / "nota-1.md")
        return md_de_vuelta.read_text(encoding="utf-8")

    def test_nombre_de_archivo_quita_el_akn(self):
        md_path = self.outdir / "nota-1.md"
        md_path.write_text("**Artículo 1.** Texto.\n", encoding="utf-8")
        xml_path = markdown_to_akoma_ntoso(md_path, self.outdir)
        self.assertEqual(xml_path.name, "nota-1.akn.xml")
        dest = akoma_ntoso_to_markdown(xml_path, self.outdir)
        self.assertEqual(dest.name, "nota-1.md")

    def test_ida_y_vuelta_articulos_simples(self):
        markdown = (
            "**Artículo 1.** Texto original del artículo primero.\n\n"
            "**Artículo 2.** Texto original del artículo segundo.\n"
        )
        self.assertEqual(self._ida_y_vuelta(markdown), markdown)

    def test_ida_y_vuelta_negritas_dentro_del_texto(self):
        markdown = "**Artículo 1.** Texto con una **palabra en negrita** dentro.\n"
        self.assertEqual(self._ida_y_vuelta(markdown), markdown)

    def test_ida_y_vuelta_fracciones_e_incisos(self):
        markdown = (
            "**Artículo 1.** Encabezado del artículo.\n\n"
            "**I.** Contenido de la fracción I.\n\n"
            "**II.** Encabezado de la fracción II.\n\n"
            "**a)** Contenido del inciso a).\n\n"
            "**b)** Contenido del inciso b).\n\n"
            "**III.** Contenido de la fracción III.\n"
        )
        self.assertEqual(self._ida_y_vuelta(markdown), markdown)

    def test_ida_y_vuelta_transitorios_preserva_el_encabezado(self):
        markdown = (
            "**Artículo 1.** Texto original del artículo primero.\n\n"
            "## Transitorios\n\n"
            "**Único.** Entrará en vigor al día siguiente.\n"
        )
        self.assertEqual(self._ida_y_vuelta(markdown), markdown)

    def test_preambulo_pierde_el_marcado_de_encabezado(self):
        # Akoma Ntoso keeps no heading-ness for <preamble>'s own paragraphs
        # (only <section> has a dedicated <heading>) — this is a best-effort
        # inverse, not a lossless one, so the "##" is gone on the way back.
        markdown = (
            "## Al margen un sello.\n\n"
            "Que el Honorable Congreso decreta:\n\n"
            "**Artículo 1.** Texto.\n"
        )
        self.assertEqual(
            self._ida_y_vuelta(markdown),
            "Al margen un sello.\n\n"
            "Que el Honorable Congreso decreta:\n\n"
            "**Artículo 1.** Texto.\n",
        )

    def test_convierte_un_xml_escrito_a_mano(self):
        xml_path = self.outdir / "manual.akn.xml"
        xml_path.write_text(
            f"""<?xml version='1.0' encoding='utf-8'?>
<akomaNtoso xmlns="{AKN_NS}">
  <act name="decreto">
    <meta>
      <identification source="#legalia">
        <FRBRWork>
          <FRBRthis value="/akn/mx/act/decreto/2024-01-01/1/!main"/>
          <FRBRuri value="/akn/mx/act/decreto/2024-01-01/1"/>
          <FRBRdate date="2024-01-01" name="publication"/>
          <FRBRauthor href="#dof"/>
          <FRBRcountry value="mx"/>
          <FRBRnumber value="1"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="/akn/mx/act/decreto/2024-01-01/1/esp@/!main"/>
          <FRBRuri value="/akn/mx/act/decreto/2024-01-01/1/esp@"/>
          <FRBRdate date="2024-01-01" name="publication"/>
          <FRBRauthor href="#dof"/>
          <FRBRlanguage language="esp"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="/akn/mx/act/decreto/2024-01-01/1/esp@.xml/!main"/>
          <FRBRuri value="/akn/mx/act/decreto/2024-01-01/1/esp@.xml"/>
          <FRBRdate date="2024-01-01" name="publication"/>
          <FRBRauthor href="#dof"/>
        </FRBRManifestation>
      </identification>
    </meta>
    <preamble>
      <p>Al margen un sello.</p>
    </preamble>
    <body>
      <article eId="art_1">
        <num>Artículo 1</num>
        <content>
          <p>Texto con <b>una palabra</b> en negrita.</p>
        </content>
      </article>
    </body>
  </act>
</akomaNtoso>
""",
            encoding="utf-8",
        )
        dest = akoma_ntoso_to_markdown(xml_path, self.outdir)
        self.assertEqual(dest, self.outdir / "manual.md")
        self.assertEqual(
            dest.read_text(encoding="utf-8"),
            "Al margen un sello.\n\n"
            "**Artículo 1.** Texto con **una palabra** en negrita.\n",
        )
