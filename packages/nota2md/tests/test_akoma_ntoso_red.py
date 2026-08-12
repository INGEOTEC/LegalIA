"""Round-trip a real DOF note through the whole `markdown_to_akoma_ntoso`/
`akoma_ntoso_to_markdown` pair and check how close the result lands to the
original — an integration test, not a unit test: `legal_provisions()`
fetches the note over the network (SIDOF, with the dofweb fallback), so
this module makes a real HTTP call and is excluded from the default test
run the same way `test_leyes_44.py` is:

    pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py \\
        --ignore=packages/nota2md/tests/test_akoma_ntoso_red.py

`COD_NOTA` (5793639, a CONAGUA "acuerdo") is the same note issue #91's own
"map a real note by hand" check used, and #92's own PR description reports
it was eyeballed for correctness — this pins that manual check down as a
repeatable, automatic one, real note formatting quirks included, instead
of only ever exercising the converters against hand-written Markdown
snippets.

Similarity is measured the same way `test_leyes_44.py` measures a
reconstruction against its own ground truth: `difflib.SequenceMatcher`
over `normaliza_para_comparar()`'d text, which folds away Markdown syntax
— so a lost "#"/"##" marker does not, on its own, count against the score;
only actually-missing words do.

The round trip is not lossless, by design (see `akoma_ntoso_to_markdown`'s
own docstring): the note's own H1 title is dropped by `_segmenta_original`
before conversion even starts, and Akoma Ntoso keeps no heading-ness for
anything but the Transitorios `<section>`'s own — every other "#"/"##" in
the note (its "Al margen un sello" byline, "CONSIDERANDO", the restated
"ACUERDO..." heading right before its articles) comes back as a plain
paragraph. Measured against codNota 5793639, that costs about 0.4
similarity points (~0.996 of 1.0) — almost all of it the dropped H1 title,
since `normaliza_para_comparar` already erases the "#"/"##" markers
themselves from both sides. UMBRAL is set with headroom below that
measurement for a note that loses a little more of this than 5793639 does,
not so much headroom that a real regression in either converter would slip
under it.
"""

import difflib
import tempfile
import unittest
from pathlib import Path

from nota2md.akoma_ntoso import akoma_ntoso_to_markdown, markdown_to_akoma_ntoso
from nota2md.builder import legal_provisions
from nota2md.leyes import normaliza_para_comparar

COD_NOTA = 5793639
UMBRAL = 0.98


class TestAkomaNtosoIdaYVueltaNotaReal(unittest.TestCase):
    def test_ida_y_vuelta_conserva_casi_todo_el_contenido(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            md_path = legal_provisions(COD_NOTA, outdir)
            original = md_path.read_text(encoding="utf-8")

            xml_path = markdown_to_akoma_ntoso(md_path, outdir)
            md_de_vuelta = akoma_ntoso_to_markdown(xml_path, outdir)
            reconstruido = md_de_vuelta.read_text(encoding="utf-8")

            ratio = difflib.SequenceMatcher(
                None,
                normaliza_para_comparar(original),
                normaliza_para_comparar(reconstruido),
                autojunk=False,
            ).ratio()
            self.assertGreaterEqual(
                ratio,
                UMBRAL,
                f"similitud {ratio:.4f} por debajo del umbral {UMBRAL} para codNota {COD_NOTA}",
            )


if __name__ == "__main__":
    unittest.main()
