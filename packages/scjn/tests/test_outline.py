"""The outline the Markdown writer keeps (review fix-2 of #291).

The fixtures are real API answers, cut down: `outline_96728.json` is blocks of
the IMDG-style fire test code (tratado 96728), `outline_103948.json` of
reglamento 103948, whose last reform decree reprints the whole instrument.
"""

import importlib.util
import json
import unittest
from pathlib import Path

from scjn.api import Articulo, articulos_a_markdown, articulos_de_crudo, guarda_crudo
from scjn.outline import CONTAINER_TYPES as CONTAINER_TYPES_
from scjn.outline import KINDS as KINDS_
from scjn.outline import classify, marker_line, parse_marker, strip_markers

FIXTURES = Path(__file__).parent / "fixtures" / "scjn_api"


def _blocks(name):
    return [
        Articulo(a["numero"], a["orden"], a["referencia"], a["contenido"])
        for a in json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    ]


class ClassifyTests(unittest.TestCase):
    def test_vocabulary_of_the_two_fixtures(self):
        got = {
            (a.referencia): classify(a.referencia)
            for name in ("outline_96728.json", "outline_103948.json")
            for a in _blocks(name)
        }
        self.assertEqual(got["ENCABEZADO"], {"kind": "header"})
        self.assertEqual(got["ÍNDICE"], {"kind": "toc"})
        self.assertEqual(got["ANEXO 1"], {"kind": "container", "type": "annex", "num": "1"})
        self.assertEqual(got["PARTE 1"], {"kind": "container", "type": "part", "num": "1"})
        self.assertEqual(got["TÍTULO PRIMERO"], {"kind": "container", "type": "title", "num": "PRIMERO"})
        self.assertEqual(got["CAPÍTULO I"], {"kind": "container", "type": "chapter", "num": "I"})
        self.assertEqual(got["ARTICULO 6 TER"], {"kind": "article", "num": "6 TER"})
        self.assertEqual(got["TERCERO"], {"kind": "provision", "num": "TERCERO"})
        # The closing words of an acuerdo are not a container: PARTE FINAL.
        self.assertEqual(got["PARTE FINAL DEL ACUERDO"], {"kind": "other"})
        self.assertEqual(
            got["D.O.F. 10 DE NOVIEMBRE DE 2023"],
            {"kind": "decree", "num": "10 DE NOVIEMBRE DE 2023"},
        )

    def test_more_shapes(self):
        self.assertEqual(classify("TRANSITORIO"), {"kind": "transitorios"})
        self.assertEqual(classify("TRANSITORIOS"), {"kind": "transitorios"})
        self.assertEqual(classify("CONSIDERANDO"), {"kind": "recitals"})
        self.assertEqual(classify("PREÁMBULO"), {"kind": "preamble"})
        self.assertEqual(classify("ANEXO"), {"kind": "container", "type": "annex"})
        self.assertEqual(classify("APÉNDICE A"), {"kind": "container", "type": "appendix", "num": "A"})
        self.assertEqual(classify("ARTÍCULO  18 BIS 1"), {"kind": "article", "num": "18 BIS 1"})
        self.assertEqual(classify("ARTÍCULO PRIMERO"), {"kind": "article", "num": "PRIMERO"})
        self.assertEqual(classify("1.1.0"), {"kind": "provision", "num": "1.1.0"})
        self.assertEqual(classify("DÉCIMO PRIMERO"), {"kind": "provision", "num": "DÉCIMO PRIMERO"})
        # The number keeps its accents: it becomes an eId, `cap_ÚNICO`.
        self.assertEqual(classify("CAPÍTULO ÚNICO")["num"], "ÚNICO")
        self.assertEqual(classify("1 ÁMBITO"), {"kind": "other"})
        self.assertEqual(classify("D.O.F 31 DE OCTUBRE DE 2014")["kind"], "decree")
        self.assertEqual(classify("PROMULGACIÓN"), {"kind": "other"})


class VocabularyFixtureTests(unittest.TestCase):
    """`md2akn` imports nothing of this package, so the marker grammar is read twice:
    here (`scjn.outline`) and in `md2akn.outline`. The fixture
    `md2akn/tests/fixtures_units/marker_vocabulary.json` is the bridge. It is
    *generated* by `scripts/verify_scjn_outline.py --write-vocabulary` from
    `scjn.outline` and never edited; this test regenerates it and compares (the way
    `marker_free_digests.json` is used), and `md2akn`'s own test reads the same file
    (issue #303)."""

    ROOT = Path(__file__).resolve().parents[3]
    FIXTURE = ROOT / "packages" / "md2akn" / "tests" / "fixtures_units" / "marker_vocabulary.json"
    SCRIPT = ROOT / "scripts" / "verify_scjn_outline.py"

    @classmethod
    def setUpClass(cls):
        if not (cls.FIXTURE.exists() and cls.SCRIPT.exists()):
            raise unittest.SkipTest("not inside the monorepo")
        spec = importlib.util.spec_from_file_location("verify_scjn_outline", cls.SCRIPT)
        cls.script = importlib.util.module_from_spec(spec)
        import sys

        sys.path.insert(0, str(cls.SCRIPT.parent))
        try:
            spec.loader.exec_module(cls.script)
        finally:
            sys.path.remove(str(cls.SCRIPT.parent))
        cls.fixture = json.loads(cls.FIXTURE.read_text(encoding="utf-8"))

    def test_the_fixture_is_what_scjn_outline_produces_now(self):
        regenerated = self.script.build_vocabulary(None)
        for key in ("marker_prefix", "kinds", "container_types", "examples"):
            with self.subTest(key):
                self.assertEqual(self.fixture[key], regenerated[key])

    def test_the_vocabulary_is_scjn_outlines_own(self):
        from scjn.outline import CONTAINER_TYPES, KINDS, MARKER_PREFIX

        self.assertEqual(self.fixture["kinds"], list(KINDS))
        self.assertEqual(self.fixture["container_types"], list(CONTAINER_TYPES))
        self.assertEqual(self.fixture["marker_prefix"], MARKER_PREFIX)

    def test_every_classify_branch_has_an_example(self):
        kinds = {e["attributes"]["kind"] for e in self.fixture["examples"]}
        self.assertEqual(kinds, set(KINDS_))
        types = {e["attributes"].get("type") for e in self.fixture["examples"]} - {None}
        self.assertEqual(types, set(CONTAINER_TYPES_))

    def test_each_example_is_a_marker_that_parses_back_to_its_attributes(self):
        for example in self.fixture["examples"]:
            with self.subTest(example["referencia"]):
                self.assertEqual(example["line"], marker_line(example["orden"], example["numero"], example["referencia"]))
                self.assertEqual(parse_marker(example["line"]), example["attributes"])
                self.assertEqual(example["attributes"]["label"], example["referencia"])
                classified = classify(example["referencia"])
                self.assertEqual(
                    {k: example["attributes"][k] for k in ("kind", "type", "num") if k in example["attributes"]},
                    classified,
                )

    def test_a_label_that_could_end_the_comment_or_the_attribute_cannot(self):
        (example,) = [e for e in self.fixture["examples"] if "--" in e["referencia"]]
        self.assertEqual(example["line"].count("-->"), 1)
        self.assertEqual(parse_marker(example["line"])["label"], example["referencia"])

    def test_the_distribution_is_informative_and_only_uses_known_kinds(self):
        for collection, counts in self.fixture["distribution"].items():
            with self.subTest(collection):
                self.assertLessEqual(set(counts["kind"]), set(KINDS_))
                self.assertLessEqual(set(counts["type"]), set(CONTAINER_TYPES_))
                self.assertEqual(sum(counts["kind"].values()), counts["markers"])


class MarkerTests(unittest.TestCase):
    def test_round_trip_keeps_the_label_verbatim(self):
        for label in ("ÍNDICE", 'ARTÍCULO "X" -- raro', "PARTE 3 \\ 4"):
            line = marker_line(3, 7, label)
            self.assertNotIn("\n", line)
            # An HTML comment ends at the first `-->`: only the last one may.
            self.assertEqual(line.count("-->"), 1)
            self.assertEqual(parse_marker(line)["label"], label.replace("--", "--"))
            self.assertEqual(parse_marker(line)["orden"], "3")

    def test_not_a_marker(self):
        self.assertIsNone(parse_marker("<!-- otro comentario -->"))
        self.assertIsNone(parse_marker("**Artículo 1.**"))


class WriterTests(unittest.TestCase):
    def test_every_block_opens_with_its_marker(self):
        md = articulos_a_markdown(_blocks("outline_96728.json"), outline=True)
        markers = [parse_marker(b) for b in md.split("\n\n") if b.startswith("<!--")]
        self.assertEqual(
            [(m["orden"], m["kind"]) for m in markers],
            [("1", "header"), ("6", "toc"), ("17", "container"), ("18", "container"),
             ("19", "container"), ("29", "container")],
        )
        self.assertEqual([m.get("type") for m in markers[2:]], ["annex", "part", "part", "annex"])
        # A marker is a paragraph of its own, right before the block's text.
        self.assertTrue(md.startswith('<!-- scjn:block orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n**ACUERDO'))

    def test_the_text_does_not_change(self):
        for name in ("outline_96728.json", "outline_103948.json", "articulos.json"):
            if name == "articulos.json":
                blocks = [
                    Articulo(a.get("numero"), a.get("orden"), (a.get("referencia") or "").strip(),
                             a.get("contenido") or "")
                    for a in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["articulos"]
                ]
            else:
                blocks = _blocks(name)
            with self.subTest(name):
                plain = articulos_a_markdown(blocks)
                self.assertEqual(strip_markers(articulos_a_markdown(blocks, outline=True)), plain)

    def test_default_is_the_old_output(self):
        blocks = _blocks("outline_103948.json")
        self.assertNotIn("scjn:block", articulos_a_markdown(blocks))

    def test_a_block_with_no_text_gets_no_marker(self):
        blocks = [Articulo(0, 1, "ENCABEZADO", "LEY"), Articulo(0, 2, "TRANSITORIO", "  \r\n ")]
        md = articulos_a_markdown(blocks, outline=True)
        self.assertEqual(md.count("scjn:block"), 1)

    def test_raw_round_trip(self):
        import tempfile

        blocks = _blocks("outline_96728.json")
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "a" / "01-01-2020.json.gz"
            guarda_crudo(destino, blocks)
            self.assertEqual(articulos_de_crudo(destino), blocks)


class MarkdownFixtureTests(unittest.TestCase):
    """`md2akn` parses `fixtures_units/outline_*.md` and imports nothing of this
    package, so the marker grammar exists twice. These two files are what
    keeps the copies from drifting: the writer must still produce them, byte
    for byte, from the real API blocks they were made of."""

    MD2AKN = Path(__file__).resolve().parents[2] / "md2akn" / "tests" / "fixtures_units"

    def test_the_writer_still_produces_the_md2akn_fixtures(self):
        if not self.MD2AKN.is_dir():
            self.skipTest("not inside the monorepo")
        for name in ("tratado", "decreto"):
            with self.subTest(name):
                expected = (self.MD2AKN / f"outline_{name}.md").read_text(encoding="utf-8")
                header = "---\nfuente: scjn\nordenamiento: EJEMPLO\n---\n\n"
                self.assertEqual(
                    expected, header + articulos_a_markdown(_blocks(f"outline_{name}.json"), outline=True)
                )


if __name__ == "__main__":
    unittest.main()
