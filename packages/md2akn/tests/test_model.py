"""The tree's own contract: walk order, find, parent/children consistency,
and the eId allocator (issue #158)."""

import unittest

import spacy

from md2akn.model import AknNode, EIdAllocator, eid_component

_NLP = spacy.blank("es")


def _nodo(akn_type, texto="x", eId=""):
    doc = _NLP(texto)
    return AknNode(akn_type, doc[:], eId=eId)


class TestEIdComponent(unittest.TestCase):
    def test_prefijo_y_numero(self):
        self.assertEqual(eid_component("art", "27"), "art_27")

    def test_el_punto_final_no_entra_al_eid(self):
        self.assertEqual(eid_component("art", "1o."), "art_1o")

    def test_los_espacios_se_pliegan_a_guion_bajo(self):
        self.assertEqual(eid_component("para", "VII Bis"), "para_VII_Bis")
        self.assertEqual(eid_component("art", "27  Bis"), "art_27_Bis")


class TestEIdAllocator(unittest.TestCase):
    """A DOF article can restate the same fracción label under two separate
    "I. a X." lists; the second claimant gets a suffix rather than a
    duplicate eId (see `EIdAllocator`'s own docstring)."""

    def test_un_eid_libre_se_entrega_tal_cual(self):
        self.assertEqual(EIdAllocator().allocate("art_27"), "art_27")

    def test_el_segundo_reclamante_recibe_sufijo(self):
        eids = EIdAllocator()
        self.assertEqual(eids.allocate("art_27__para_I"), "art_27__para_I")
        self.assertEqual(eids.allocate("art_27__para_I"), "art_27__para_I_2")
        self.assertEqual(eids.allocate("art_27__para_I"), "art_27__para_I_3")

    def test_child_encadena_bajo_el_padre(self):
        eids = EIdAllocator()
        art = eids.allocate("art_27")
        para = eids.child(art, "para", "VII")
        self.assertEqual(para, "art_27__para_VII")
        self.assertEqual(eids.child(para, "point", "a"), "art_27__para_VII__point_a")

    def test_child_sin_padre_no_deja_separador_colgando(self):
        self.assertEqual(EIdAllocator().child("", "art", "1"), "art_1")


class TestAknNode(unittest.TestCase):
    def setUp(self):
        self.act = _nodo("act", "raiz", eId="act")
        self.body = self.act.add(_nodo("body", eId="body"))
        self.a = self.body.add(_nodo("article", eId="art_1"))
        self.b = self.body.add(_nodo("article", eId="art_2"))
        self.a1 = self.a.add(_nodo("paragraph", eId="art_1__para_I"))

    def test_add_deja_parent_y_children_consistentes(self):
        for nodo in self.act.walk():
            if nodo.parent is not None:
                self.assertIn(nodo, nodo.parent.children)

    def test_walk_es_preorden(self):
        self.assertEqual(
            [n.eId for n in self.act.walk()],
            ["act", "body", "art_1", "art_1__para_I", "art_2"],
        )

    def test_find_devuelve_el_nodo_por_eid(self):
        self.assertIs(self.act.find("art_1__para_I"), self.a1)

    def test_find_desconocido_es_none(self):
        self.assertIsNone(self.act.find("art_99"))

    def test_find_solo_mira_hacia_abajo(self):
        # `find` walks this node's subtree, not the whole document.
        self.assertIsNone(self.a.find("art_2"))

    def test_los_nodos_se_comparan_por_identidad(self):
        # Structural equality would recurse through `parent` forever, and two
        # nodes with the same text are still two nodes.
        self.assertNotEqual(_nodo("article", "igual"), _nodo("article", "igual"))
        self.assertEqual(len({self.a, self.a}), 1)

    def test_repr_no_recurre_por_el_arbol(self):
        texto = repr(self.act)
        self.assertIn("akn_type='act'", texto)
        self.assertIn("children=1", texto)
        self.assertNotIn("AknNode(akn_type='body'", texto)


if __name__ == "__main__":
    unittest.main()
