"""Check construye_ley() against the 43 laws it was designed against.

These are integration tests, not unit tests: construye_ley() fetches each
DOF note over the network (SIDOF, with the dofweb fallback), so this module
makes real HTTP calls and is slower than the rest of the suite. The 43 laws
are every federal law with exactly 2 or 3 reforms, all published after
1999-01-01, minus ligie_2022 (see fixtures/leyes/historial_44.json) — small
enough that a handful of reforms plausibly reconstructs the whole thing.

fixtures/leyes/<abrev>.md is the ground truth: Diputados' own "texto vigente"
PDF for the law, cleaned up by limpia_texto_ley(). construye_ley() never reads
it or anything derived from it — the comparison is between two independently
sourced texts.

Similarity, not equality, is what is asserted. construye_ley() is a rule-based
reconstruction (see nota2md.leyes' module docstring), and a run against this
set turned up a few ways it falls short of the real text that are not worth
chasing further here:

* A reform that restates only the fracciones it changes and elides the rest
  with "..." is read by construye_ley() as the article's whole new text, so
  whatever the ellipsis stood for is dropped (lcid, lfcpo, lgsna).
* lgmsv mixes reforms that restate a whole article with others that only
  print the specific fracciones/párrafos changed, which construye_ley() reads
  as if they were the article's complete new text.
* lspcapf recognizes every one of its 80 articles correctly (its numbering is
  ordinary "Artículo N.-") but still comes out far from the real text for a
  reason this suite hasn't tracked down — its 2006 reform added a whole new
  Subsistema de Desarrollo Profesional, so something about how that scale of
  addition interacts with article ordering is suspect, but that is a guess,
  not a diagnosis.

UMBRAL_EXCEPCIONES gives those explained shortfalls a floor derived from where
they actually land (with a little room below it), so a real further
regression in one of them still fails the test without the general floor
having to be lowered enough to hide it for every other law too.
UMBRAL_POR_LEY is that general floor; UMBRAL_PROMEDIO catches a regression too
small for any one law's own floor to trip.
"""

import difflib
import json
import unittest
from pathlib import Path

from nota2md.leyes import construye_ley, normaliza_para_comparar

FIXTURES = Path(__file__).parent / "fixtures" / "leyes"
HISTORIAL = json.loads((FIXTURES / "historial_44.json").read_text(encoding="utf-8"))

# Below this, a law's reconstruction is close enough to call a partial success
# even though it visibly differs from the real text.
UMBRAL_POR_LEY = 0.55
# Known, explained shortfalls (see the module docstring) that would otherwise
# force UMBRAL_POR_LEY down to where it catches nothing.
UMBRAL_EXCEPCIONES = {
    "lspcapf": 0.0,
    "lgmsv": 0.1,
    "lfcpo": 0.4,
    "lgsna": 0.4,
}
# Above this on average, an unrelated change that quietly makes the whole
# batch worse would still be caught even where no single law crosses its own
# floor.
UMBRAL_PROMEDIO = 0.78


def _similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(
        None, normaliza_para_comparar(a), normaliza_para_comparar(b)
    ).ratio()


class TestConstruyeLeyContraElTextoVigenteReal(unittest.TestCase):
    def test_cada_ley_se_reconstruye_por_encima_del_umbral(self):
        promedios = []
        peores = []
        for abrev, info in HISTORIAL.items():
            with self.subTest(ley=abrev):
                real = (FIXTURES / f"{abrev}.md").read_text(encoding="utf-8")
                construida = construye_ley(info["historial"], info["nombre"])
                ratio = _similitud(real, construida)
                promedios.append(ratio)
                umbral = UMBRAL_EXCEPCIONES.get(abrev, UMBRAL_POR_LEY)
                if ratio < umbral:
                    peores.append((abrev, ratio))
                self.assertGreaterEqual(
                    ratio, umbral,
                    f"{abrev}: similitud {ratio:.2f} por debajo del umbral "
                    f"{umbral} (ver {abrev}.md)",
                )

        promedio = sum(promedios) / len(promedios)
        self.assertGreaterEqual(
            promedio, UMBRAL_PROMEDIO,
            f"similitud promedio {promedio:.3f} por debajo de {UMBRAL_PROMEDIO} "
            f"sobre {len(promedios)} leyes; peores casos: {peores}",
        )
