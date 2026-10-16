"""The coverage and cap invariants, swept over the whole cached SCJN corpus.

An integration test, not a unit test: it walks every instrument the three
cached SCJN releases have on disk — `scjn-leyes` (315 laws, issue #218),
`scjn-reglamentos` (1,082 with text) and `scjn-lineamientos` (126), the
latter two added by issue #227, and `scjn-tratados` (1,373 with text, added
by issue #279) — parsing and segmenting each one and checking
both invariants at the corpus' real scale rather than on a handful of
fixtures:

- `coverage()`: every non-whitespace character is in a unit, the frontmatter
  or a reform annotation. Nothing is lost.
- `max_unit_chars()`: no unit is over the cap except one whose single
  paragraph is itself over it (issue #227's rule 9). The units are usable.

And one property that is specific to rule 8, and is the reason rule 8 is safe
by construction: `modo_sin_articulos` returns `None` for **every** federal
law, so the rule can never change one. That is the cheap, permanent form of
the proof; its strong form was run once when the rule landed — regenerating
the 315 laws' `units.parquet` with rule 8 on and rule 9 off produced a file
byte-identical to the pre-#227 one (one byte apart: the pyarrow writer
version string in the Parquet footer).

Over the treaties it also asserts rule 10's reach (issue #279): every treaty
with a roman-numbered article opener has at least one roman-numbered
`article` unit, so none of them is left as `preamble`/`loose` blocks.

Issue #291's rules 11 to 13 are swept the same way: every treaty that writes a
decimal article opener (`Artículo 1.1:`) has a decimal-numbered `article` unit,
and no table row of any collection comes out as a `loose`, `intro` or
`heading` unit (`TestReglas11A13`). The decimal check reads the *cached*
snapshots: a cache that still holds the old `**Artículo 1.**1:` shape fails it
by name until the seven treaties of that issue are re-downloaded -- not a
reason to loosen the test.

It makes no network request of its own — the releases have to already be
cached (`nota2md download all` populates all four) — but at roughly 17
minutes for the full sweep it is excluded from the routine run the same way
`packages/nota2md/tests/test_leyes_44.py` and `packages/scjn`'s `_red` tests
are, and for a related but distinct reason: not network, but wall-clock time
plus a ~380 MB local cache not every dev machine or CI runner has:

    pytest packages/md2akn -q \\
        --ignore=packages/md2akn/tests/test_units_release_sweep.py

`scripts/md2akn_units_sweep.py` is the script that reports the same counts
table issue #218's own body measures against (`unit_type`, `n`, median/mean/
p95/max chars) — this module only asserts the invariants, it does not print
the table.

`scjn` is not one of `md2akn`'s own dependencies (issue #158's "one
dependency, on purpose") and is only ever needed to reach the cached corpora
this test sweeps — imported lazily, and its absence skips the test rather
than failing collection for whoever installed `md2akn[test]` alone.
"""

import importlib.util
import re
import unittest

from md2akn import coverage, max_unit_chars, parse_markdown, text_units
from md2akn.outline import lee_marcador
from md2akn.patterns import ARTICULO, FILA_TABLA
from md2akn.segmenter import iter_blocks, split_frontmatter
from md2akn.structure import modo_sin_articulos

_TIENE_SCJN = importlib.util.find_spec("scjn") is not None
if _TIENE_SCJN:
    from scjn import (
        iter_current_federal_laws,
        iter_current_lineamientos,
        iter_current_reglamentos,
        iter_current_tratados,
        local_lineamientos_ids,
        local_reglamentos_ids,
        local_slugs,
        local_tratados_ids,
    )

_ROMANO = re.compile(r"[IVXLCDM]+")
_NUMERO_DECIMAL = re.compile(r"\d+(?:\.\d+)+")
_EID_ARTICULO_DECIMAL = re.compile(r"(?:.*__)?art_\d+(?:\.\d+)+")
# An article's eId is scoped under its containers (`cap_II__art_IV`).
_EID_ARTICULO_ROMANO = re.compile(r"(?:.*__)?art_[IVXLCDM]+(?:_\d+)?")


def _bloques_que_abren_un_articulo(markdown: str):
    """The blocks whose first line may be an article heading. A document with no
    outline marker: every block. One written with the outline (review fix-2 of
    #291): only the block a marker says is an article's -- a line inside another
    API block (a treaty's text quoting `Artículo 17.9.2 `) is not an opener."""
    _meta, fin_meta = split_frontmatter(markdown)
    bloques = list(iter_blocks(markdown, fin_meta))
    con_esquema = any(lee_marcador(b.text.split("\n", 1)[0]) for b in bloques)
    anterior = None
    for bloque in bloques:
        marca = lee_marcador(bloque.text.split("\n", 1)[0])
        if marca is not None:
            anterior = marca
            continue
        if not con_esquema or (anterior is not None and anterior.get("kind") == "article"):
            yield bloque
        anterior = None


def _abre_con_articulo_romano(markdown: str) -> bool:
    """Whether any block of `markdown` opens on a roman-numbered article
    heading (rule 10, issue #279) — read with the same pattern the builder
    uses, bold markers removed."""
    for bloque in _bloques_que_abren_un_articulo(markdown):
        m = ARTICULO.match(bloque.text.split("\n", 1)[0].replace("**", ""))
        if m and _ROMANO.fullmatch(m.group("num").split()[0]):
            return True
    return False


def _abre_con_articulo_decimal(markdown: str) -> bool:
    """Whether any block of `markdown` opens on a decimal-numbered article
    heading (rule 11, issue #291), read with the pattern the builder uses."""
    for bloque in _bloques_que_abren_un_articulo(markdown):
        m = ARTICULO.match(bloque.text.split("\n", 1)[0].replace("**", ""))
        if m and _NUMERO_DECIMAL.fullmatch(m.group("num")):
            return True
    return False


def _es_fila_de_una_celda(linea):
    celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
    return sum(1 for c in celdas if c) == 1


def _filas_fuera_de_tabla(instrumentos, clave):
    """`(instruments with table rows, [(clave, archivo, unit_type, eId)])`: the
    units that are not `table` units (or an article's own text) and still
    contain a table row. Only a unit before the first container -- the
    `preamble` -- or after the last one -- `conclusions` -- may: those keep
    their pre-#291 treatment (rule 12's "text before any container")."""
    con_tablas = 0
    fuera = []
    for instrumento in instrumentos:
        markdown = instrumento["markdown"]
        if "\n| " not in markdown and not markdown.startswith("| "):
            continue
        con_tablas += 1
        tree = parse_markdown(markdown)
        doc = tree.span.doc.text
        for unit in text_units(tree, split_articles=False):
            if unit.unit_type not in ("loose", "intro", "heading"):
                continue
            filas = [
                linea for linea in doc[unit.start_char:unit.end_char].split("\n")
                if FILA_TABLA.match(linea.strip())
                # Review fix-2 of #291: the SCJN's text puts a tab after some
                # container labels, which `scjn.api` reads as a row of one
                # non-empty cell (`| CAPÍTULO II |  |`). In a heading that row
                # *is* the label the API's marker names -- not table data.
                and not (unit.unit_type == "heading" and _es_fila_de_una_celda(linea))
            ]
            if filas:
                fuera.append((instrumento[clave], instrumento["archivo"], unit.unit_type, unit.eId))
    return con_tablas, fuera


def _barre(instrumentos, clave, *, split_articles: bool = True):
    """`(vistos, sin_cubrir, partibles)` over `instrumentos` — one parse per
    instrument, both invariants checked on the same tree so the sweep is
    walked once rather than once per assertion.

    `split_articles=False` (issue #256) sweeps the same corpus under the
    whole-article rule: `coverage()` must still be 0 and `splittable` must
    still be 0, with an over-cap article counted in `articles_over_cap`
    rather than `splittable`.
    """
    vistos = 0
    sin_cubrir = []
    partibles = []
    for instrumento in instrumentos:
        vistos += 1
        tree = parse_markdown(instrumento["markdown"])
        units = text_units(tree, split_articles=split_articles)
        cov = coverage(tree, units)
        if cov.uncovered_chars:
            sin_cubrir.append((instrumento[clave], instrumento["archivo"], cov))
        cap = max_unit_chars(tree, units, split_articles=split_articles)
        if cap.splittable:
            partibles.append((instrumento[clave], instrumento["archivo"], cap))
    return vistos, sin_cubrir, partibles


@unittest.skipUnless(_TIENE_SCJN, "scjn is not installed")
class TestInvariantesSobreElReleaseCompleto(unittest.TestCase):
    def _comprueba(self, instrumentos, clave, *, split_articles: bool = True):
        vistos, sin_cubrir, partibles = _barre(instrumentos, clave, split_articles=split_articles)
        self.assertGreater(vistos, 0)
        self.assertEqual(
            sin_cubrir, [], f"{len(sin_cubrir)} of {vistos} left characters uncovered"
        )
        self.assertEqual(
            partibles, [],
            f"{len(partibles)} of {vistos} carry a unit over the cap that rule 9 "
            "could still have split",
        )

    @unittest.skipUnless(_TIENE_SCJN and local_slugs(), "no scjn-leyes release cached locally")
    def test_leyes(self):
        self._comprueba(iter_current_federal_laws(), "slug")

    @unittest.skipUnless(
        _TIENE_SCJN and local_reglamentos_ids(), "no scjn-reglamentos release cached locally"
    )
    def test_reglamentos(self):
        self._comprueba(iter_current_reglamentos(), "id_ordenamiento")

    @unittest.skipUnless(
        _TIENE_SCJN and local_lineamientos_ids(), "no scjn-lineamientos release cached locally"
    )
    def test_lineamientos(self):
        self._comprueba(iter_current_lineamientos(), "id_ordenamiento")

    # -- issue #256: split_articles=False, one case per collection -------- #

    @unittest.skipUnless(_TIENE_SCJN and local_slugs(), "no scjn-leyes release cached locally")
    def test_leyes_split_articles_false(self):
        self._comprueba(iter_current_federal_laws(), "slug", split_articles=False)

    @unittest.skipUnless(
        _TIENE_SCJN and local_reglamentos_ids(), "no scjn-reglamentos release cached locally"
    )
    def test_reglamentos_split_articles_false(self):
        self._comprueba(iter_current_reglamentos(), "id_ordenamiento", split_articles=False)

    @unittest.skipUnless(
        _TIENE_SCJN and local_lineamientos_ids(), "no scjn-lineamientos release cached locally"
    )
    def test_lineamientos_split_articles_false(self):
        self._comprueba(iter_current_lineamientos(), "id_ordenamiento", split_articles=False)

    # -- issue #279: scjn-tratados --------------------------------------- #

    @unittest.skipUnless(
        _TIENE_SCJN and local_tratados_ids(), "no scjn-tratados release cached locally"
    )
    def test_tratados(self):
        self._comprueba(iter_current_tratados(), "id_ordenamiento")

    @unittest.skipUnless(
        _TIENE_SCJN and local_tratados_ids(), "no scjn-tratados release cached locally"
    )
    def test_tratados_split_articles_false(self):
        self._comprueba(iter_current_tratados(), "id_ordenamiento", split_articles=False)

    @unittest.skipUnless(
        _TIENE_SCJN and local_tratados_ids(), "no scjn-tratados release cached locally"
    )
    def test_todo_tratado_con_articulos_romanos_tiene_unidades_de_articulo(self):
        """Rule 10's reach: a treaty that numbers its articles `ARTÍCULO I`..
        has at least one roman-numbered `article` unit (before the rule, 176
        of the 229 treaties with no `article` unit were exactly these), and
        the treaties left with no article unit at all are the ones with no
        roman opener either (53 measured, issue #279; 54 since review fix-1
        of #291: the treaty 22648 writes its articles as a bold `**III**` and
        its ten recitals `**PRIMERO.-**` .. `**DECIMO.-**` used to be read as
        its only articles, rule 17 correctly leaves them in the preamble)."""
        romanos = 0
        sin_unidad_romana = []
        sin_articulo = 0
        for tratado in iter_current_tratados():
            units = text_units(tratado["markdown"], split_articles=False)
            if not any(u.unit_type == "article" for u in units):
                sin_articulo += 1
            if not _abre_con_articulo_romano(tratado["markdown"]):
                continue
            romanos += 1
            if not any(
                u.unit_type == "article" and _EID_ARTICULO_ROMANO.fullmatch(u.eId)
                for u in units
            ):
                sin_unidad_romana.append((tratado["id_ordenamiento"], tratado["archivo"]))
        self.assertGreater(romanos, 0)
        self.assertEqual(
            sin_unidad_romana, [],
            f"{len(sin_unidad_romana)} of {romanos} roman-numbered treaties have no "
            "roman-numbered article unit",
        )
        self.assertLessEqual(sin_articulo, 54)

    @unittest.skipUnless(
        _TIENE_SCJN and local_tratados_ids(), "no scjn-tratados release cached locally"
    )
    def test_todo_tratado_con_articulos_decimales_tiene_unidades_de_articulo_decimales(self):
        """Rule 11's reach (issue #291): a treaty that numbers its articles
        `Artículo 1.1:` has decimal-numbered `article` units, so its articles
        are not all article `1`."""
        decimales = 0
        sin_unidad_decimal = []
        for tratado in iter_current_tratados():
            if not _abre_con_articulo_decimal(tratado["markdown"]):
                continue
            decimales += 1
            units = text_units(tratado["markdown"], split_articles=False)
            if not any(
                u.unit_type == "article" and _EID_ARTICULO_DECIMAL.fullmatch(u.eId)
                for u in units
            ):
                sin_unidad_decimal.append((tratado["id_ordenamiento"], tratado["archivo"]))
        self.assertGreater(decimales, 0)
        self.assertEqual(sin_unidad_decimal, [])

    def _comprueba_tablas(self, instrumentos, clave):
        con_tablas, fuera = _filas_fuera_de_tabla(instrumentos, clave)
        self.assertGreater(con_tablas, 0)
        self.assertEqual(
            fuera, [],
            f"{len(fuera)} unit(s) of {con_tablas} instruments with tables still hold table rows",
        )

    @unittest.skipUnless(_TIENE_SCJN and local_slugs(), "no scjn-leyes release cached locally")
    def test_ninguna_fila_de_tabla_queda_suelta_en_las_leyes(self):
        self._comprueba_tablas(iter_current_federal_laws(), "slug")

    @unittest.skipUnless(
        _TIENE_SCJN and local_reglamentos_ids(), "no scjn-reglamentos release cached locally"
    )
    def test_ninguna_fila_de_tabla_queda_suelta_en_los_reglamentos(self):
        self._comprueba_tablas(iter_current_reglamentos(), "id_ordenamiento")

    @unittest.skipUnless(
        _TIENE_SCJN and local_lineamientos_ids(), "no scjn-lineamientos release cached locally"
    )
    def test_ninguna_fila_de_tabla_queda_suelta_en_los_lineamientos(self):
        self._comprueba_tablas(iter_current_lineamientos(), "id_ordenamiento")

    @unittest.skipUnless(
        _TIENE_SCJN and local_tratados_ids(), "no scjn-tratados release cached locally"
    )
    def test_ninguna_fila_de_tabla_queda_suelta_en_los_tratados(self):
        self._comprueba_tablas(iter_current_tratados(), "id_ordenamiento")

    @unittest.skipUnless(_TIENE_SCJN and local_slugs(), "no scjn-leyes release cached locally")
    def test_la_regla_8_no_se_activa_en_ninguna_ley(self):
        """Rule 8's gate is off for every federal law — which is what makes
        the rule a no-op on them by construction, rather than by inspection
        of what it happened to produce (issue #227's Fase 1)."""
        activadas = []
        for ley in iter_current_federal_laws():
            _meta, fin_meta = split_frontmatter(ley["markdown"])
            modo = modo_sin_articulos(list(iter_blocks(ley["markdown"], fin_meta)))
            if modo is not None:
                activadas.append((ley["slug"], modo))
        self.assertEqual(activadas, [], f"rule 8 switched on for {len(activadas)} laws")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
