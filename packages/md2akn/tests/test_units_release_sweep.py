"""The coverage and cap invariants, swept over the whole cached SCJN corpus.

An integration test, not a unit test: it walks every instrument the three
cached SCJN releases have on disk — `scjn-leyes` (315 laws, issue #218),
`scjn-reglamentos` (1,082 with text) and `scjn-lineamientos` (126), the
latter two added by issue #227 — parsing and segmenting each one and checking
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

It makes no network request of its own — the releases have to already be
cached (`nota2md download all` populates all three) — but at roughly 13
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
import unittest

from md2akn import coverage, max_unit_chars, parse_markdown, text_units
from md2akn.segmenter import iter_blocks, split_frontmatter
from md2akn.structure import modo_sin_articulos

_TIENE_SCJN = importlib.util.find_spec("scjn") is not None
if _TIENE_SCJN:
    from scjn import (
        iter_current_federal_laws,
        iter_current_lineamientos,
        iter_current_reglamentos,
        local_lineamientos_ids,
        local_reglamentos_ids,
        local_slugs,
    )


def _barre(instrumentos, clave):
    """`(vistos, sin_cubrir, partibles)` over `instrumentos` — one parse per
    instrument, both invariants checked on the same tree so the sweep is
    walked once rather than once per assertion."""
    vistos = 0
    sin_cubrir = []
    partibles = []
    for instrumento in instrumentos:
        vistos += 1
        tree = parse_markdown(instrumento["markdown"])
        units = text_units(tree)
        cov = coverage(tree, units)
        if cov.uncovered_chars:
            sin_cubrir.append((instrumento[clave], instrumento["archivo"], cov))
        cap = max_unit_chars(tree, units)
        if cap.splittable:
            partibles.append((instrumento[clave], instrumento["archivo"], cap))
    return vistos, sin_cubrir, partibles


@unittest.skipUnless(_TIENE_SCJN, "scjn is not installed")
class TestInvariantesSobreElReleaseCompleto(unittest.TestCase):
    def _comprueba(self, instrumentos, clave):
        vistos, sin_cubrir, partibles = _barre(instrumentos, clave)
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
