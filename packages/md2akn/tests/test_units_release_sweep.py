"""The coverage invariant, swept over the whole cached `scjn-leyes` release.

An integration test, not a unit test: it walks every federal law
`scjn.iter_current_federal_laws()` has cached on disk (315 of them as of
issue #218), parsing and segmenting each one and checking `coverage()`'s
invariant at the corpus' real scale rather than a handful of fixtures. It
makes no network request of its own — the release has to already be cached
(`nota2md download all` populates it) — but at roughly 4.5 minutes for the
full sweep it is excluded from the routine run the same way
`packages/nota2md/tests/test_leyes_44.py` and `packages/scjn`'s `_red` tests
are, and for a related but distinct reason: not network, but wall-clock time
plus a ~300 MB local cache not every dev machine or CI runner has:

    pytest packages/md2akn -q \\
        --ignore=packages/md2akn/tests/test_units_release_sweep.py

`scripts/md2akn_units_sweep.py` is the script that reports the same counts
table issue #218's own body measures against (`unit_type`, `n`, median/mean/
p95/max chars) — this module only asserts the invariant, it does not print
the table.

`scjn` is not one of `md2akn`'s own dependencies (issue #158's "one
dependency, on purpose") and is only ever needed to reach the cached corpus
this test sweeps — imported lazily, and its absence skips the test rather
than failing collection for whoever installed `md2akn[test]` alone.
"""

import importlib.util
import unittest

from md2akn import coverage, parse_markdown, text_units

_TIENE_SCJN = importlib.util.find_spec("scjn") is not None
if _TIENE_SCJN:
    from scjn import iter_current_federal_laws, local_slugs


@unittest.skipUnless(_TIENE_SCJN, "scjn is not installed")
@unittest.skipUnless(_TIENE_SCJN and local_slugs(), "no scjn-leyes release cached locally")
class TestCoberturaSobreElReleaseCompleto(unittest.TestCase):
    def test_cero_no_cubiertos_en_cada_ley_del_release(self):
        fallos = []
        vistas = 0
        for ley in iter_current_federal_laws():
            vistas += 1
            tree = parse_markdown(ley["markdown"])
            units = text_units(tree)
            cov = coverage(tree, units)
            if cov.uncovered_chars:
                fallos.append((ley["slug"], ley["archivo"], cov))
        self.assertGreater(vistas, 0)
        self.assertEqual(fallos, [], f"{len(fallos)} of {vistas} laws left characters uncovered")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
