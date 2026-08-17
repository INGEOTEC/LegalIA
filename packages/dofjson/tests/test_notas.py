import unittest

from dofjson.notas import infer_paginas, quita_notas_sin_titulo


class TestInferPaginas(unittest.TestCase):
    def _notas_del_dia(self, paginas_ordenadas):
        return {
            "NotasMatutinas": [
                {"codNota": 1000 + i, "pagina": pagina}
                for i, pagina in enumerate(paginas_ordenadas)
            ]
        }

    def test_single_page_when_next_nota_same_page(self):
        notas_del_dia = self._notas_del_dia([20, 20, 21, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21])

    def test_spans_two_pages_when_next_nota_is_next_page(self):
        # Reproduces codNota=4845455 (pagina 21) followed by codNota=4845457
        # (pagina 22) on 02-01-1980.
        notas_del_dia = self._notas_del_dia([20, 20, 21, 22, 23])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])

    def test_last_nota_of_the_day_is_a_single_page(self):
        notas_del_dia = self._notas_del_dia([20, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 22}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [22])

    def test_resorts_by_codnota_when_raw_list_and_orden_dont_match_page_order(self):
        # Real API response shape for 02-01-1980: raw list order and `orden`
        # do not match page order, but codNota ascending does — infer_paginas
        # must re-sort by codNota, not rely on list order or `orden`.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 4845457, "pagina": 22, "orden": 0.0},
                {"codNota": 4845424, "pagina": 2, "orden": 2.0},
                {"codNota": 4845455, "pagina": 21, "orden": 0.0},
            ]
        }
        nota = {"codNota": 4845455, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])

    def test_skips_digital_twin_sharing_the_same_starting_page(self):
        # Reproduces codNota=5793654 (pagina 80, existeDoc "N") on 15-07-2026:
        # its immediate next note by codNota is 5793655, a digital-text twin
        # of the same content (existeDoc "S") also starting on page 80. The
        # true next distinct note (5793656) starts on page 89, but doesn't
        # share it with 5793654 — unlike a genuine same-page neighbor, a
        # skipped twin's page is excluded from the range.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793653, "pagina": 78},
                {"codNota": 5793654, "pagina": 80},
                {"codNota": 5793655, "pagina": 80},
                {"codNota": 5793656, "pagina": 89},
            ]
        }
        nota = {"codNota": 5793654, "codEdicion": "MAT", "pagina": 80, "existeDoc": "N"}

        self.assertEqual(infer_paginas(nota, notas_del_dia), list(range(80, 81)))

    def test_skips_multiple_consecutive_digital_twins(self):
        # Some notes are split into several digital "section" entries, all
        # sharing the image-only note's starting page — infer_paginas must
        # skip all of them, not just the first.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 100, "pagina": 30},
                {"codNota": 101, "pagina": 30},
                {"codNota": 102, "pagina": 30},
                {"codNota": 103, "pagina": 30},
                {"codNota": 104, "pagina": 40},
            ]
        }
        nota = {"codNota": 100, "codEdicion": "MAT", "pagina": 30, "existeDoc": "N"}

        self.assertEqual(infer_paginas(nota, notas_del_dia), list(range(30, 31)))

    def test_same_page_neighbor_without_existdoc_field_stays_single_page(self):
        # A same-page neighbor is only treated as a digital twin when it is
        # explicitly marked existeDoc "S" — synthetic/legacy data lacking
        # that field must keep the original single-page confinement.
        notas_del_dia = self._notas_del_dia([20, 20, 21, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21])


class TestQuitaNotasSinTitulo(unittest.TestCase):
    def test_drops_titleless_stub_note(self):
        # Reproduces codNota=5793456/5793457 on 14-07-2026: 5793456 is a stub
        # duplicate (no titulo, existeHtml "S") of 5793457, which carries the
        # real title on the same page.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793456, "pagina": 5, "existeHtml": "S", "existeDoc": "N"},
                {"codNota": 5793457, "pagina": 5, "existeHtml": "S", "existeDoc": "S", "titulo": "Decreto..."},
            ],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual([n["codNota"] for n in resultado["NotasMatutinas"]], [5793457])

    def test_drops_titleless_image_only_note_too(self):
        # A genuine image-only note (existeHtml "N") has no digital twin at
        # all, but is still dropped: this function's only criterion is
        # whether `titulo` is present.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 5793654, "pagina": 80, "existeHtml": "N", "existeDoc": "N"},
                {"codNota": 5793655, "pagina": 80, "existeHtml": "S", "existeDoc": "S", "titulo": "Convenio..."},
            ],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual([n["codNota"] for n in resultado["NotasMatutinas"]], [5793655])

    def test_keeps_titled_notes_untouched(self):
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 1, "pagina": 1, "existeHtml": "S", "existeDoc": "S", "titulo": "Aviso A"},
                {"codNota": 2, "pagina": 1, "existeHtml": "S", "existeDoc": "S", "titulo": "Aviso B"},
            ],
        }

        resultado = quita_notas_sin_titulo(notas_del_dia)

        self.assertEqual(resultado, notas_del_dia)


if __name__ == "__main__":
    unittest.main()
