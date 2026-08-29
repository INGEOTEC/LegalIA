"""Frontmatter splitting, block scanning, and the tree's document-wide
invariants.

The block scan and the frontmatter split are issue #158's; the tree they feed
stopped being a flat run of `content` nodes once #159 supplied the structural
rules, so what is checked here is what survives that change — coverage,
ordering, unique eIds — and the rules themselves live in `test_structure.py`."""

import unittest

import spacy

from md2akn.segmenter import iter_blocks, segment, split_frontmatter

_NLP = spacy.blank("es")

CON_FRONTMATTER = """---
fuente: scjn
ordenamiento: LEY FEDERAL DE CINEMATOGRAFIA
sospechoso: false
---

**CAPITULO I.**

Primer parrafo del cuerpo.

Segundo parrafo.
"""


class TestSplitFrontmatter(unittest.TestCase):
    def test_lee_las_claves_y_apunta_al_cuerpo(self):
        meta, inicio = split_frontmatter(CON_FRONTMATTER)
        self.assertEqual(
            meta,
            {
                "fuente": "scjn",
                "ordenamiento": "LEY FEDERAL DE CINEMATOGRAFIA",
                "sospechoso": "false",
            },
        )
        self.assertTrue(CON_FRONTMATTER[inicio:].lstrip().startswith("**CAPITULO"))

    def test_un_valor_con_dos_puntos_sobrevive_entero(self):
        meta, _ = split_frontmatter("---\ntitulo: LEY X: la buena\n---\n\ncuerpo\n")
        self.assertEqual(meta["titulo"], "LEY X: la buena")

    def test_sin_frontmatter_no_es_un_error(self):
        meta, inicio = split_frontmatter("Solo cuerpo.\n")
        self.assertEqual(meta, {})
        self.assertEqual(inicio, 0)

    def test_una_cerca_sin_cerrar_se_trata_como_cuerpo(self):
        # Far likelier a horizontal rule than a truncated header; swallowing
        # the file as metadata would be the worse failure.
        meta, inicio = split_frontmatter("---\nno cierra nunca\n")
        self.assertEqual(meta, {})
        self.assertEqual(inicio, 0)

    def test_una_linea_que_no_es_clave_valor_se_ignora(self):
        meta, _ = split_frontmatter("---\nfuente: scjn\nbasura suelta\n---\n\nx\n")
        self.assertEqual(meta, {"fuente": "scjn"})


class TestIterBlocks(unittest.TestCase):
    def test_los_bloques_se_separan_por_linea_en_blanco(self):
        bloques = list(iter_blocks("uno\n\ndos\ntres\n\n\ncuatro"))
        self.assertEqual([b.text for b in bloques], ["uno", "dos\ntres", "cuatro"])

    def test_los_offsets_apuntan_al_texto_original(self):
        texto = "uno\n\ndos\n"
        for bloque in iter_blocks(texto):
            self.assertEqual(texto[bloque.start:bloque.end], bloque.text)

    def test_offset_inicial_salta_el_frontmatter(self):
        meta, inicio = split_frontmatter(CON_FRONTMATTER)
        bloques = list(iter_blocks(CON_FRONTMATTER, inicio))
        self.assertEqual(bloques[0].text, "**CAPITULO I.**")

    def test_un_documento_vacio_no_da_bloques(self):
        self.assertEqual(list(iter_blocks("")), [])
        self.assertEqual(list(iter_blocks("\n\n   \n")), [])


class TestArbol(unittest.TestCase):
    def setUp(self):
        self.doc = _NLP(CON_FRONTMATTER)
        self.tree, self.meta = segment(self.doc)

    def test_la_raiz_es_un_act_con_un_body(self):
        self.assertEqual(self.tree.akn_type, "act")
        self.assertEqual([h.akn_type for h in self.tree.children], ["body"])

    def test_cada_bloque_llega_a_algun_nodo(self):
        cuerpo = self.tree.children[0]
        self.assertEqual([h.akn_type for h in cuerpo.children], ["chapter"])
        self.assertTrue(cuerpo.children[0].text.startswith("**CAPITULO I.**"))

    def test_el_frontmatter_queda_en_act_meta(self):
        self.assertEqual(self.tree.meta["ordenamiento"], "LEY FEDERAL DE CINEMATOGRAFIA")
        self.assertEqual(self.tree.meta, self.meta)

    def test_las_hojas_mas_los_separadores_reproducen_el_documento_sin_frontmatter(self):
        _, inicio = split_frontmatter(CON_FRONTMATTER)
        hojas = [n for n in self.tree.walk() if not n.children]
        rehecho = ""
        cursor = self.tree.start_char
        for hoja in hojas:
            rehecho += CON_FRONTMATTER[cursor:hoja.start_char] + hoja.text
            cursor = hoja.end_char
        self.assertEqual(rehecho, CON_FRONTMATTER[inicio:].strip())

    def test_el_act_no_cubre_el_frontmatter(self):
        self.assertNotIn("fuente: scjn", self.tree.text)

    def test_los_eid_son_unicos(self):
        eids = [n.eId for n in self.tree.walk()]
        self.assertEqual(len(eids), len(set(eids)))

    def test_los_hijos_caen_dentro_del_padre_y_en_orden(self):
        for nodo in self.tree.walk():
            anterior = None
            for hijo in nodo.children:
                self.assertGreaterEqual(hijo.start_char, nodo.start_char)
                self.assertLessEqual(hijo.end_char, nodo.end_char)
                if anterior is not None:
                    self.assertGreaterEqual(hijo.start_char, anterior.end_char)
                anterior = hijo

    def test_un_documento_sin_frontmatter_da_el_mismo_arbol(self):
        tree, meta = segment(_NLP("**CAPITULO I.**\n\nUno.\n"))
        self.assertEqual(meta, {})
        self.assertEqual([n.akn_type for n in tree.children], ["body"])
        self.assertEqual([n.akn_type for n in tree.children[0].children], ["chapter"])

    def test_un_documento_vacio_no_revienta(self):
        # An `act` always has a `body`, even an empty document's, so no
        # consumer has to special-case its absence.
        tree, meta = segment(_NLP(""))
        self.assertEqual(tree.akn_type, "act")
        self.assertEqual(meta, {})
        self.assertEqual([n.akn_type for n in tree.children], ["body"])
        self.assertEqual(tree.children[0].children, [])


if __name__ == "__main__":
    unittest.main()
