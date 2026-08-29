"""Containers, articles, preamble, transitorios and conclusions (issue #159).

The Markdown in these tests is trimmed from real laws in the SCJN corpus —
the shapes are the ones measured there, not invented ones. Whole files are
never checked in: the largest is 1.89 MB, and the repository does not version
data.
"""

import unittest

from md2akn import parse_markdown
from md2akn.model import REFERS_TO_APARTADO, REFERS_TO_TRANSITORIOS
from md2akn.patterns import ARTICULO
from md2akn.structure import clasifica
from md2akn.segmenter import Block


def _tipos(tree, akn_type):
    return [n for n in tree.walk() if n.akn_type == akn_type]


def _num(tree, akn_type):
    return [n.num for n in tree.walk() if n.akn_type == akn_type]


class TestNumeroDeArticulo(unittest.TestCase):
    """Every shape below is one the corpus actually writes."""

    def _num(self, linea):
        m = ARTICULO.match(linea.replace("**", ""))
        return m.group("num") if m else None

    def test_formas_simples(self):
        self.assertEqual(self._num("**Artículo 28.** El x"), "28")
        self.assertEqual(self._num("**ARTICULO 28.-** El x"), "28")
        self.assertEqual(self._num("Art. 1o.- El x"), "1o")
        # A doubled space between the word and the number, 569 occurrences.
        self.assertEqual(self._num("**Artículo  9o.** El x"), "9o")

    def test_sufijo_latino_en_cualquier_caja(self):
        # The LFT writes `3° bis` and `3° Ter` three blocks apart.
        self.assertEqual(self._num("**ARTICULO 28 Bis.-** x"), "28 Bis")
        self.assertEqual(self._num("**Artículo 3° bis.** x"), "3° bis")
        self.assertEqual(self._num("**Artículo 3° Bis 1.** x"), "3° Bis 1")

    def test_sufijo_latino_unido_por_guion(self):
        # The CCF's own spelling of the same thing.
        self.assertEqual(self._num("**ARTICULO 103-**Bis.- x"), "103-Bis")

    def test_sufijo_de_una_letra(self):
        self.assertEqual(self._num("**ARTICULO 27-A.- x"), "27-A")
        self.assertEqual(self._num("**ARTICULO 410 **A.- x"), "410 A")

    def test_una_mayuscula_suelta_no_es_sufijo(self):
        # Without the lookahead on the separator this swallows the article's
        # own first word.
        self.assertEqual(self._num("**Artículo 5** A las personas x"), "5")

    def test_dos_articulos_en_un_encabezado_quedan_como_un_num(self):
        # `Art. 30,31.` -- one heading, one body, no way to divide the text
        # between them without guessing.
        self.assertEqual(self._num("Art. 30,31. Los x"), "30,31")

    def test_un_separador_de_millares_no_parte_el_numero(self):
        self.assertEqual(self._num("**Artículo 1,297.** x"), "1,297")

    def test_ordinales_en_palabras(self):
        self.assertEqual(self._num("**ARTICULO UNICO.-** x"), "UNICO")
        self.assertEqual(self._num("**ARTICULO PRIMERO.-** x"), "PRIMERO")


class TestClasificacion(unittest.TestCase):
    def _kind(self, texto):
        return clasifica(Block(texto, 0, len(texto)))[0]

    def test_una_anotacion_no_es_un_articulo(self):
        # It contains the word "ARTÍCULOS" and must not be read as one.
        self.assertEqual(
            self._kind("**(ADICIONADO CON LOS ARTÍCULOS QUE LO INTEGRAN, D.O.F. 1 DE MAYO DE 2019)**"),
            "anotacion",
        )

    def test_una_nota_editorial_se_reconoce_para_dejarla_en_paz(self):
        self.assertEqual(
            self._kind("(NOTA: EL 1 DE JUNIO DE 2021, EL PLENO DE LA SUPREMA CORTE...)"),
            "nota_editorial",
        )

    def test_articulos_transitorios_no_es_un_articulo(self):
        self.assertEqual(self._kind("**ARTICULOS TRANSITORIOS**"), "transitorios")
        self.assertEqual(self._kind("## Transitorios"), "transitorios")
        self.assertEqual(self._kind("Artículos Transitorios"), "transitorios")

    def test_un_capitulo_citado_en_una_frase_no_abre_un_capitulo(self):
        self.assertEqual(
            self._kind("Lo dispuesto en el Capítulo III de esta Ley será aplicable."),
            "contenido",
        )

    def test_contenedores_con_y_sin_negritas(self):
        self.assertEqual(self._kind("**CAPÍTULO I**"), "contenedor")
        self.assertEqual(self._kind("Capítulo I"), "contenedor")
        self.assertEqual(self._kind("**TÍTULO PRIMERO**"), "contenedor")
        self.assertEqual(self._kind("Sección Primera"), "contenedor")


LEY_COMPLETA = """---
fuente: scjn
ordenamiento: LEY DE PRUEBA
---

## Al margen un sello con el Escudo Nacional, que dice: Estados Unidos Mexicanos.

VENUSTIANO CARRANZA, hago saber: ha tenido a bien expedir la siguiente:

**TITULO PRIMERO.**

**DISPOSICIONES GENERALES.**

**CAPITULO I.**

**DEL OBJETO DE LA LEY.**

**Artículo 1o.** La presente Ley es de orden publico.

**Artículo 2o.** Para los efectos de esta Ley se entiende por lo siguiente.

**CAPITULO II.**

**DE LAS AUTORIDADES.**

**Artículo 3o.** Son autoridades competentes las siguientes.

**TITULO SEGUNDO.**

**CAPITULO I.**

**Artículo 4o.** Las disposiciones de este Titulo son aplicables.

## Transitorios

**Primero.** El presente Decreto entrara en vigor al dia siguiente.

**Segundo.** Se derogan las disposiciones que se opongan.

**D.O.F. 15 DE SEPTIEMBRE DE 2024.**

**Unico.-** Este Decreto entrara en vigor el dia de su publicacion.
"""


class TestLeyCompleta(unittest.TestCase):
    def setUp(self):
        self.tree = parse_markdown(LEY_COMPLETA)

    def test_el_acto_tiene_preambulo_y_cuerpo_como_hermanos(self):
        self.assertEqual([n.akn_type for n in self.tree.children], ["preamble", "body"])

    def test_el_preambulo_llega_hasta_el_primer_contenedor(self):
        preambulo = self.tree.children[0]
        self.assertIn("Al margen un sello", preambulo.text)
        self.assertIn("hago saber", preambulo.text)
        self.assertNotIn("TITULO PRIMERO", preambulo.text)

    def test_los_contenedores_anidan_por_precedencia(self):
        titulos = _tipos(self.tree, "title")
        self.assertEqual([t.num for t in titulos], ["PRIMERO", "SEGUNDO"])
        self.assertEqual([c.num for c in titulos[0].children if c.akn_type == "chapter"],
                         ["I", "II"])

    def test_un_titulo_nuevo_cierra_el_capitulo_abierto(self):
        segundo = _tipos(self.tree, "title")[1]
        self.assertEqual([c.num for c in segundo.children if c.akn_type == "chapter"], ["I"])

    def test_la_numeracion_que_se_reinicia_la_resuelve_el_eid(self):
        # Two `CAPITULO I`, one per title -- distinct paths, no collision.
        caps = [n.eId for n in _tipos(self.tree, "chapter") if n.num == "I"]
        self.assertEqual(caps, ["tit_PRIMERO__cap_I", "tit_SEGUNDO__cap_I"])

    def test_el_epigrafe_viene_del_bloque_siguiente(self):
        self.assertEqual(_tipos(self.tree, "title")[0].heading, "DISPOSICIONES GENERALES")
        self.assertEqual(_tipos(self.tree, "chapter")[0].heading, "DEL OBJETO DE LA LEY")

    def test_los_articulos_cuelgan_del_contenedor_mas_profundo(self):
        cap = _tipos(self.tree, "chapter")[0]
        self.assertEqual([a.num for a in cap.children if a.akn_type == "article"],
                         ["1o", "2o"])
        self.assertEqual(cap.children[0].parent, cap)

    def test_los_eid_son_jerarquicos_y_unicos(self):
        eids = [n.eId for n in self.tree.walk()]
        self.assertEqual(len(eids), len(set(eids)))
        self.assertIsNotNone(self.tree.find("tit_PRIMERO__cap_II__art_3o"))

    def test_dos_bloques_de_transitorios_son_secciones_hermanas(self):
        secciones = [n for n in self.tree.walk() if n.refers_to == REFERS_TO_TRANSITORIOS]
        self.assertEqual(len(secciones), 2)
        self.assertEqual(secciones[1].num, "15 DE SEPTIEMBRE DE 2024")
        # Never merged: which decree a provision belongs to is exactly what
        # the separation records.
        self.assertNotEqual(secciones[0].eId, secciones[1].eId)

    def test_los_transitorios_se_numeran_con_ordinales_en_negritas(self):
        secciones = [n for n in self.tree.walk() if n.refers_to == REFERS_TO_TRANSITORIOS]
        self.assertEqual([a.num for a in secciones[0].children if a.akn_type == "article"],
                         ["Primero", "Segundo"])
        self.assertEqual([a.num for a in secciones[1].children if a.akn_type == "article"],
                         ["Unico"])

    def test_los_transitorios_cuelgan_del_cuerpo_no_del_ultimo_capitulo(self):
        seccion = [n for n in self.tree.walk() if n.refers_to == REFERS_TO_TRANSITORIOS][0]
        self.assertEqual(seccion.parent.akn_type, "body")

    def test_un_padre_cubre_a_sus_hijos_y_los_hijos_son_disjuntos(self):
        for nodo in self.tree.walk():
            anterior = None
            for hijo in nodo.children:
                self.assertGreaterEqual(hijo.start_char, nodo.start_char)
                self.assertLessEqual(hijo.end_char, nodo.end_char)
                if anterior is not None:
                    self.assertGreaterEqual(hijo.start_char, anterior.end_char)
                anterior = hijo

    def test_walk_va_en_orden_de_documento(self):
        offsets = [n.start_char for n in self.tree.walk()]
        self.assertEqual(offsets, sorted(offsets))


class TestLeyPlana(unittest.TestCase):
    """Many short laws have no containers at all; an article with no chapter
    is not an error."""

    def setUp(self):
        self.tree = parse_markdown(
            "**LEY DE ALGO**\n\n"
            "**Artículo 1o.** Uno.\n\n"
            "**Artículo 2o.** Dos.\n"
        )

    def test_los_articulos_cuelgan_del_cuerpo(self):
        cuerpo = [n for n in self.tree.children if n.akn_type == "body"][0]
        self.assertEqual([a.num for a in cuerpo.children], ["1o", "2o"])

    def test_los_eid_no_llevan_prefijo_de_cuerpo(self):
        self.assertIsNotNone(self.tree.find("art_1o"))


class TestOtrasFormas(unittest.TestCase):
    def test_un_apartado_es_level_con_refers_to(self):
        tree = parse_markdown(
            "**Artículo 123.** Toda persona tiene derecho al trabajo.\n\n"
            "**APARTADO A.**\n\n"
            "**Artículo 124.** Entre los obreros y los patrones.\n"
        )
        niveles = _tipos(tree, "level")
        self.assertEqual([n.num for n in niveles], ["A"])
        self.assertEqual(niveles[0].refers_to, REFERS_TO_APARTADO)

    def test_un_libro_es_book_y_manda_sobre_el_titulo(self):
        tree = parse_markdown(
            "**LIBRO PRIMERO**\n\n**TITULO PRIMERO**\n\n**Artículo 1o.** Uno.\n"
        )
        libro = _tipos(tree, "book")[0]
        self.assertEqual([n.akn_type for n in libro.children], ["title"])

    def test_un_capitulo_vacio_se_emite_igual(self):
        # A chapter whose articles were all repealed is a fact about the law.
        tree = parse_markdown(
            "**CAPITULO I**\n\n**CAPITULO II**\n\n**Artículo 1o.** Uno.\n"
        )
        caps = _tipos(tree, "chapter")
        self.assertEqual([c.num for c in caps], ["I", "II"])
        self.assertEqual(caps[0].children, [])

    def test_un_capitulo_antes_de_un_titulo_queda_colgando(self):
        # The precedence rule closes the chapter rather than nesting the
        # title inside it -- which is what the document actually says.
        tree = parse_markdown(
            "**CAPITULO I**\n\n**Artículo 1o.** Uno.\n\n"
            "**TITULO PRIMERO**\n\n**Artículo 2o.** Dos.\n"
        )
        cuerpo = [n for n in tree.children if n.akn_type == "body"][0]
        self.assertEqual([n.akn_type for n in cuerpo.children], ["chapter", "title"])

    def test_un_articulo_viejo_sin_negritas(self):
        tree = parse_markdown("Art. 1o.- En los Estados Unidos Mexicanos.\n")
        self.assertEqual(_num(tree, "article"), ["1o"])

    def test_las_conclusiones_solo_al_final(self):
        tree = parse_markdown(
            "**Artículo 1o.** Uno.\n\n"
            "Ciudad de Mexico, a 15 de abril de 2026.- Dip. Fulano, Presidente.- Rubricas.\n"
        )
        # No preamble here: the document opens on an article, so there is
        # nothing before the body.
        self.assertEqual([n.akn_type for n in tree.children], ["body", "conclusions"])

    def test_sin_rubricas_no_hay_conclusiones(self):
        tree = parse_markdown("**Artículo 1o.** Uno.\n\nUn parrafo final cualquiera.\n")
        self.assertNotIn("conclusions", [n.akn_type for n in tree.children])

    def test_una_anotacion_no_rompe_la_estructura(self):
        tree = parse_markdown(
            "**(REFORMADO PRIMER PÁRRAFO, D.O.F. 10 DE JUNIO DE 2011)**\n\n"
            "**Artículo 1o.** Uno.\n"
        )
        self.assertEqual(_num(tree, "article"), ["1o"])

    def test_un_dof_fuera_de_transitorios_es_texto(self):
        tree = parse_markdown(
            "**Artículo 1o.** Publicado en el D.O.F.\n\n**D.O.F. 15 DE MAYO DE 2020.**\n"
        )
        self.assertEqual([n.refers_to for n in tree.walk() if n.refers_to], [])

    def test_un_epigrafe_de_varias_lineas_en_negritas(self):
        # The corpus hard-wraps long epigraphs, bolding each line separately.
        tree = parse_markdown(
            "**Capítulo IV**\n\n"
            "**De la Circulación, Difusión y Comercialización de las Obras**\n"
            "**Audiovisuales**\n\n"
            "**Artículo 24.** Para los efectos.\n"
        )
        self.assertEqual(
            _tipos(tree, "chapter")[0].heading,
            "De la Circulación, Difusión y Comercialización de las Obras Audiovisuales",
        )

    def test_un_parrafo_no_se_confunde_con_un_epigrafe(self):
        tree = parse_markdown(
            "**CAPITULO I**\n\n"
            "Las disposiciones de este capitulo son de observancia general.\n\n"
            "**Artículo 1o.** Uno.\n"
        )
        self.assertIsNone(_tipos(tree, "chapter")[0].heading)


if __name__ == "__main__":
    unittest.main()
