"""Fracciones, incisos and subincisos inside an article (issue #160).

The cases here are the ambiguities the issue names — `V.` with no `I.` before
it, `C.` opening a document, `A.` as apartado against `A.` as inciso, `1.` as
subinciso against a sentence that begins with a figure — plus the three real
things that break strict consecutiveness and must not break the list.
"""

import unittest

from md2akn import parse_markdown
from md2akn.lists import analiza_etiqueta, valor_romano
from md2akn.model import REFERS_TO_APARTADO


def _hijos(nodo, akn_type=None):
    return [h for h in nodo.children if akn_type is None or h.akn_type == akn_type]


def _articulo(tree, num="1o"):
    return next(n for n in tree.walk() if n.akn_type == "article" and n.num == num)


class TestValorRomano(unittest.TestCase):
    def test_numerales_bien_formados(self):
        self.assertEqual(valor_romano("I"), 1)
        self.assertEqual(valor_romano("IV"), 4)
        self.assertEqual(valor_romano("XXII"), 22)
        self.assertEqual(valor_romano("XXIX"), 29)

    def test_una_letra_que_no_es_numeral_no_vale(self):
        self.assertIsNone(valor_romano("A"))
        self.assertIsNone(valor_romano("B"))


class TestAnalizaEtiqueta(unittest.TestCase):
    def test_las_cuatro_series(self):
        self.assertEqual(analiza_etiqueta("I"), ("romana", 1))
        self.assertEqual(analiza_etiqueta("A"), ("mayuscula", 1))
        self.assertEqual(analiza_etiqueta("B"), ("mayuscula", 2))
        self.assertEqual(analiza_etiqueta("a"), ("minuscula", 1))
        self.assertEqual(analiza_etiqueta("7"), ("digito", 7))

    def test_una_letra_que_tambien_es_numeral_se_reporta_como_numeral(self):
        # `C.` is both; reporting it as Roman is the ambiguity, not a
        # resolution of it -- the open-list state is what decides.
        self.assertEqual(analiza_etiqueta("C"), ("romana", 100))
        self.assertEqual(analiza_etiqueta("V"), ("romana", 5))

    def test_un_sufijo_latino_comparte_ordinal_con_su_base(self):
        # Which is what makes `VII Bis` continue the list instead of
        # restarting it.
        self.assertEqual(analiza_etiqueta("VII Bis"), analiza_etiqueta("VII"))
        self.assertEqual(analiza_etiqueta("VII Ter"), ("romana", 7))


class TestFracciones(unittest.TestCase):
    def test_una_lista_simple(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son obligaciones:\n\n"
            "**I.** La primera.\n\n**II.** La segunda.\n\n**III.** La tercera.\n"
        )
        art = _articulo(tree)
        self.assertEqual([p.num for p in _hijos(art, "paragraph")], ["I", "II", "III"])

    def test_un_articulo_sin_lista_tiene_un_content_por_parrafo(self):
        # Issue #181: the paragraph is a unit of the tree unconditionally, so
        # it can be cited ("el párrafo segundo del artículo 1o.") whether or
        # not the article also happens to carry fracciones.
        tree = parse_markdown("**Artículo 1o.** Un texto sin listas.\n\nOtro parrafo.\n")
        art = _articulo(tree)

        self.assertEqual([h.akn_type for h in art.children], ["content", "content"])
        self.assertEqual([h.num for h in art.children], ["1", "2"])
        self.assertEqual(
            [h.eId for h in art.children], ["art_1o__p_1", "art_1o__p_2"]
        )
        # No hierarchy means no chapeau and no tail: there is nothing for a
        # later XML conversion to choose between.
        self.assertEqual([h.is_chapeau for h in art.children], [False, False])
        self.assertEqual([h.is_tail for h in art.children], [False, False])

    def test_una_fraccion_derogada_deja_un_hueco_y_no_rompe_la_lista(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** Una.\n\n**III.** Tres.\n\n**V.** Cinco.\n"
        )
        self.assertEqual(
            [p.num for p in _hijos(_articulo(tree), "paragraph")], ["I", "III", "V"]
        )

    def test_un_sufijo_latino_continua_la_lista(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** Una.\n\n**VII.** Siete.\n\n"
            "**VII Bis.** Siete bis.\n\n**VII Ter.** Siete ter.\n\n**VIII.** Ocho.\n"
        )
        self.assertEqual(
            [p.num for p in _hijos(_articulo(tree), "paragraph")],
            ["I", "VII", "VII Bis", "VII Ter", "VIII"],
        )

    def test_una_V_sin_I_antes_no_abre_una_lista(self):
        # The rule that does most of the work: only the first label of a
        # series may open a list.
        tree = parse_markdown("**Artículo 1o.** Texto.\n\n**V.** Esto no es una fraccion.\n")
        self.assertEqual(_hijos(_articulo(tree), "paragraph"), [])

    def test_dos_listas_en_el_mismo_articulo(self):
        # A body's composition, then its members' eligibility: the second
        # list restarts at I. and the eId disambiguates the repeated labels.
        tree = parse_markdown(
            "**Artículo 1o.** El Consejo se integra por:\n\n"
            "**I.** Un presidente.\n\n**II.** Dos vocales.\n\n"
            "Los vocales deberan cumplir los requisitos siguientes:\n\n"
            "**I.** Ser mexicano.\n\n**II.** Tener treinta anos.\n"
        )
        art = _articulo(tree)
        nums = [p.num for p in _hijos(art, "paragraph")]
        self.assertEqual(nums, ["I", "II", "I", "II"])
        eids = [p.eId for p in _hijos(art, "paragraph")]
        self.assertEqual(len(set(eids)), 4)
        self.assertEqual(eids[2], "art_1o__para_I_2")


class TestIncisosYSubincisos(unittest.TestCase):
    def test_incisos_dentro_de_una_fraccion(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** La primera, que comprende:\n\n"
            "**a)** Lo uno.\n\n**b)** Lo otro.\n\n**II.** La segunda.\n"
        )
        art = _articulo(tree)
        fracciones = _hijos(art, "paragraph")
        self.assertEqual([f.num for f in fracciones], ["I", "II"])
        self.assertEqual([p.num for p in _hijos(fracciones[0], "point")], ["a", "b"])
        self.assertEqual(_hijos(fracciones[1], "point"), [])

    def test_subincisos_dentro_de_un_inciso(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** Primera:\n\n**a)** Lo uno, a saber:\n\n"
            "**1.** Uno.\n\n**2.** Dos.\n"
        )
        inciso = _hijos(_hijos(_articulo(tree), "paragraph")[0], "point")[0]
        self.assertEqual([s.num for s in _hijos(inciso, "subpoint")], ["1", "2"])

    def test_un_numeral_sin_inciso_abierto_no_es_subinciso(self):
        # The tariff schedules alone hold tens of thousands of lines that
        # open with a figure.
        tree = parse_markdown(
            "**Artículo 1o.** Texto.\n\n1. Los demas bienes causaran el impuesto.\n"
        )
        self.assertEqual(
            [n.akn_type for n in _articulo(tree).walk() if n.akn_type == "subpoint"], []
        )

    def test_los_eid_encadenan_articulo_fraccion_inciso(self):
        tree = parse_markdown(
            "**Artículo 27.** Son:\n\n**I.** Una:\n\n**a)** Lo uno.\n"
        )
        self.assertIsNotNone(tree.find("art_27__para_I__point_a"))


class TestApartados(unittest.TestCase):
    def test_una_A_antes_de_toda_fraccion_es_un_apartado(self):
        # The shape of articles 2 and 123 of the Constitution.
        tree = parse_markdown(
            "**Artículo 123.** Toda persona tiene derecho al trabajo.\n\n"
            "**A.** Entre los obreros y los patrones:\n\n"
            "**I.** La duracion de la jornada.\n\n**II.** La jornada nocturna.\n\n"
            "**B.** Entre los Poderes de la Union:\n\n**I.** La jornada.\n"
        )
        art = _articulo(tree, "123")
        apartados = _hijos(art, "level")
        self.assertEqual([a.num for a in apartados], ["A", "B"])
        self.assertEqual(apartados[0].refers_to, REFERS_TO_APARTADO)
        self.assertEqual([p.num for p in _hijos(apartados[0], "paragraph")], ["I", "II"])

    def test_una_A_bajo_una_fraccion_es_un_inciso(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** La primera, que comprende:\n\n"
            "**A.** Lo uno.\n\n**B.** Lo otro.\n"
        )
        fraccion = _hijos(_articulo(tree), "paragraph")[0]
        self.assertEqual([p.akn_type for p in fraccion.children], ["point", "point"])
        self.assertEqual([p.num for p in fraccion.children], ["A", "B"])

    def test_una_C_que_abre_el_documento_no_es_nada(self):
        # "El C. Primer Jefe del Ejército Constitucionalista..." -- `C.` is a
        # valid numeral and a valid letter, and is neither here.
        tree = parse_markdown(
            "**Artículo 1o.** Texto.\n\n"
            "C. Primer Jefe del Ejercito Constitucionalista, Encargado del Poder.\n"
        )
        art = _articulo(tree)
        # Both blocks are plain paragraphs of the article (issue #181); what
        # matters here is that no list node was opened by that `C.`.
        self.assertEqual([n.akn_type for n in art.walk()][1:], ["content", "content"])


class TestChapeauYTail(unittest.TestCase):
    """The flags exist because Akoma Ntoso forbids exactly the shape a
    Mexican article has: a flat introduction *and* hierarchical children."""

    def setUp(self):
        self.tree = parse_markdown(
            "**Artículo 4o.** Son obligaciones:\n\n"
            "**I.** La primera.\n\n**II.** La segunda.\n\n"
            "Las obligaciones anteriores se cumpliran conforme al reglamento.\n"
        )
        self.art = _articulo(self.tree, "4o")

    def test_el_introductorio_es_chapeau(self):
        primero = self.art.children[0]
        self.assertEqual(primero.akn_type, "content")
        self.assertTrue(primero.is_chapeau)
        self.assertIn("Son obligaciones", primero.text)

    def test_el_parrafo_final_es_tail(self):
        ultimo = self.art.children[-1]
        self.assertEqual(ultimo.akn_type, "content")
        self.assertTrue(ultimo.is_tail)
        self.assertFalse(ultimo.is_chapeau)

    def test_hijos_intercalados_en_orden_de_documento(self):
        self.assertEqual(
            [h.akn_type for h in self.art.children],
            ["content", "paragraph", "paragraph", "content"],
        )

    def test_un_articulo_sin_lista_no_lleva_banderas(self):
        tree = parse_markdown("**Artículo 1o.** Solo texto.\n")
        self.assertFalse(any(n.is_chapeau or n.is_tail for n in tree.walk()))


class TestParrafoCitable(unittest.TestCase):
    """The paragraph as a unit of the tree, always (issue #181)."""

    LEY = (
        "**Artículo 4o.** Son obligaciones:\n\n"
        "**I.** La primera, que dice:\n\n"
        "Un parrafo dentro de la fraccion.\n\n"
        "Y otro parrafo dentro de la misma fraccion.\n\n"
        "**II.** La segunda.\n\n"
        "Un parrafo final del articulo.\n"
    )

    def setUp(self):
        self.art = _articulo(parse_markdown(self.LEY), "4o")

    def test_el_articulo_con_jerarquia_conserva_su_forma_y_sus_banderas(self):
        self.assertEqual(
            [h.akn_type for h in self.art.children],
            ["content", "paragraph", "paragraph", "content"],
        )
        self.assertTrue(self.art.children[0].is_chapeau)
        self.assertTrue(self.art.children[-1].is_tail)

    def test_los_parrafos_propios_del_articulo_se_numeran_entre_ellos(self):
        # Mexican citation counts the article's own paragraphs -- the chapeau
        # and the closing one -- not the text inside its fracciones.
        propios = [h for h in self.art.children if h.akn_type == "content"]
        self.assertEqual([h.num for h in propios], ["1", "2"])
        self.assertEqual([h.eId for h in propios], ["art_4o__p_1", "art_4o__p_2"])

    def test_la_fraccion_absorbe_sus_propios_parrafos_y_no_entran_en_la_cuenta(self):
        # Settling the question #181 leaves open: the count is scoped to the
        # immediate parent, so a fracción's continuation paragraphs never
        # advance the article's own numbering. They are not `content` nodes
        # at all today — #160's rule grows the fracción's span over them
        # instead — and the two paragraphs the article does own are numbered
        # 1 and 2 with those in between, exactly as "el párrafo segundo del
        # artículo 4o." means it.
        fraccion = _hijos(self.art, "paragraph")[0]
        self.assertEqual(_hijos(fraccion, "content"), [])
        self.assertIn("Y otro parrafo dentro de la misma fraccion", fraccion.text)

        propios = [h for h in self.art.children if h.akn_type == "content"]
        self.assertEqual([h.num for h in propios], ["1", "2"])
        self.assertIn("Un parrafo final del articulo", propios[1].text)

    def test_los_eid_son_unicos_en_todo_el_arbol(self):
        # The literal `"p"` placeholder made this false before #181: two
        # paragraphs of one article proposed the same eId.
        eids = [n.eId for n in parse_markdown(self.LEY).walk() if n.eId]
        self.assertEqual(len(eids), len(set(eids)))
        self.assertFalse([e for e in eids if e.endswith("__p_2_2")])


class TestInvariantes(unittest.TestCase):
    LEY = (
        "**Artículo 1o.** Son obligaciones:\n\n"
        "**I.** La primera, que comprende:\n\n"
        "**a)** Lo uno, a saber:\n\n**1.** Uno.\n\n**2.** Dos.\n\n"
        "**b)** Lo otro.\n\n**II.** La segunda.\n\n"
        "Un parrafo final del articulo.\n"
    )

    def setUp(self):
        self.tree = parse_markdown(self.LEY)

    def test_ningun_point_sin_paragraph_o_level_padre(self):
        for n in self.tree.walk():
            if n.akn_type == "point":
                self.assertIn(n.parent.akn_type, ("paragraph", "level"))

    def test_ningun_subpoint_sin_point_padre(self):
        for n in self.tree.walk():
            if n.akn_type == "subpoint":
                self.assertEqual(n.parent.akn_type, "point")

    def test_los_eid_son_unicos(self):
        eids = [n.eId for n in self.tree.walk()]
        self.assertEqual(len(eids), len(set(eids)))

    def test_un_padre_cubre_a_sus_hijos_disjuntos_y_en_orden(self):
        for nodo in self.tree.walk():
            anterior = None
            for hijo in nodo.children:
                self.assertGreaterEqual(hijo.start_char, nodo.start_char)
                self.assertLessEqual(hijo.end_char, nodo.end_char)
                if anterior is not None:
                    self.assertGreaterEqual(hijo.start_char, anterior.end_char)
                anterior = hijo

    def test_un_bloque_sin_marcador_seguido_de_otro_item_continua_el_abierto(self):
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** La primera:\n\n"
            "**a)** Lo uno.\n\nUn parrafo que continua el inciso.\n\n"
            "**b)** Lo otro.\n"
        )
        inciso = _hijos(_hijos(_articulo(tree), "paragraph")[0], "point")[0]
        self.assertIn("Un parrafo que continua el inciso", inciso.text)

    def test_un_bloque_sin_marcador_que_cierra_el_articulo_es_su_cola(self):
        # Identical in form to the block above; only what follows tells them
        # apart, so the decision waits for the next block.
        tree = parse_markdown(
            "**Artículo 1o.** Son:\n\n**I.** La primera:\n\n"
            "**a)** Lo uno.\n\nUn parrafo final del articulo.\n"
        )
        art = _articulo(tree)
        cola = art.children[-1]
        self.assertEqual(cola.akn_type, "content")
        self.assertTrue(cola.is_tail)
        self.assertIn("Un parrafo final del articulo", cola.text)
        inciso = _hijos(_hijos(art, "paragraph")[0], "point")[0]
        self.assertNotIn("Un parrafo final", inciso.text)


if __name__ == "__main__":
    unittest.main()
