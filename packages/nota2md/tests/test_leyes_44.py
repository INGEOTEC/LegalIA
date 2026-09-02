"""Check reconstruct_legal_provisions() against the laws it was designed against.

These are integration tests, not unit tests: reconstruct_legal_provisions()
fetches each DOF note over the network (SIDOF, with the dofweb fallback), so
this module makes real HTTP calls and is slower than the rest of the suite.
The laws are every federal law with exactly 2 or 3 reforms, all published
after 1999-01-01, minus ligie_2022 — small enough that a handful of reforms
plausibly reconstructs the whole thing. (`44` in this module's and the
fixture's names is the original file count of the directory and is kept only
so the two keep matching; the suite covers 42 laws today.)

## The ground truth is no longer independent of what it checks

Read this before trusting a number this module prints.

Until issue #188 the fixtures were the Cámara de Diputados' own consolidated
"texto vigente" PDF, cleaned up by a module (`nota2md.texto_vigente`) that
was deleted with them: a genuinely separate source, so the comparison was
between two independently produced texts. Issue #184 removed every dependency
on Diputados, and the replacement ground truth is the **SCJN's** own
consolidated text at the law's most recent reform, read off the `scjn-leyes`
release (`scripts/regenera_fixtures_leyes.py` writes it).

That is not an independent check of everything this repo does, and the
weakening is real and specific:

- What *is* still checked, and is the whole point of this module:
  `reconstruct_legal_provisions()` replays the DOF's own decrees, note by
  note, and nothing in that path reads the SCJN. So the reconstruction and
  the fixture still come from two different publishers of the same law.
- What is **not** checked, at all: `legal_provisions()` answers from the SCJN
  corpus by default since issue #117. Nothing here — or anywhere else —
  compares that corpus against a source outside itself. If the SCJN's text of
  a law is wrong, this suite passes.

Both statements are stated rather than glossed because the second one is a
gap, not a subtlety, and it does not close by being left unwritten.

## Fixtures

fixtures/leyes/<abrev>.md is the newest snapshot of the law in the release,
with the provenance header stripped and the SCJN's editorial "N. DE E."
insertions removed (`quita_notas_editoriales`).
fixtures/leyes/historial_44.json is the law's reform history — since issue
#187 that *is* the law's own `indice.json` in the release, one `codNota` per
reform, oldest first.

The two are regenerated together, which is what keeps them consistent: the
history replayed ends at exactly the reform the fixture is the text of. Under
the previous fixtures they were sourced apart, and a law reformed after its
PDF was captured would have been compared against a stale text.

`lfgr` is **excluded**: all three of its snapshots are `ambiguous` and none is
content-diff confirmed, so the corpus cannot say which decrees published them.
Replaying a history with a hole in it measures the hole, not the algorithm.
That exclusion is a visible cost of dropping Diputados, whose curated list had
no such gap — recorded here rather than quietly absorbed.

## How agreement is measured, and what it came out at

Similarity is measured article by article, not over the whole document:
`_segmenta_original()` (the same code reconstruct_legal_provisions() itself
uses to split a note into one entry per article) splits both the real and the
constructed text into their article dictionaries, and the law's score is the
average of every article's own ratio. Comparing the whole document in one
shot lets a long run of shared boilerplate (headers, repeated phrasing) paper
over a handful of badly-reconstructed articles; comparing article by article
cannot, and it also says which articles are the bad ones instead of just how
bad the law is overall — see PEORES_ARTICULOS in a failure message. It is
also far cheaper: difflib's matching-block search scales worse than linearly
with input size, so diffing one document of length N costs more than diffing
N articles of length 1 each summed together.

Similarity, not equality, is what is asserted. reconstruct_legal_provisions()
is a rule-based reconstruction (see nota2md.leyes' module docstring).

Measured when the fixtures were switched over (issue #188), so the change of
source is on the record rather than inferred from a moved threshold:

- **The two ground truths agree.** Compared article by article against the
  Diputados PDFs they replaced, the SCJN snapshots average **0.918** over the
  43 laws, with 39 of them segmenting into exactly the same number of
  articles. Whole-document ratios are much lower and much noisier (0.603 on
  average) because the SCJN carries its own reform annotations and different
  front matter — which is one more reason the per-article comparison is the
  one that means anything here.
- **The reconstruction scores slightly better against them**: average 0.978,
  against 0.971 under the Diputados fixtures. Worst law is now `lfcpo` at
  0.829 (it was `lfpccs` at 0.84); every other law clears 0.919.
- **SEGMENTACION_REAL_ROTA is gone.** `lgpdppso` and `lopjf` used to need
  whole-document comparison because their own PDFs lost the blank line
  between articles across long stretches of text extraction, so the *ground
  truth* segmented into 5 and 14 articles instead of 137 and 307. The SCJN
  text segments correctly, and the two now score 0.999 and 1.000 article by
  article. The exception existed to work around the old source, and went away
  with it.

lspcapf used to be this suite's one unexplained shortfall: it recognizes
every one of its 80 articles correctly but, compared whole-document, used to
land at 0.21 for a reason nobody had tracked down — a guess was that its 2006
reform, which added a whole new Subsistema de Desarrollo Profesional, did
something to article ordering. Article by article it scores 0.99: the guess
was right in spirit but backwards in blame — every article's own content is
reconstructed correctly, it is specifically *comparing the whole document*
that the reordering defeats, dragging down a score that had nothing wrong
with it at the level that matters. No law needs its own exception anymore.

lcid, lfcpo, lgsna and lgmsv used to need one too, for a shared reason: a
reform that restates only the fracciones/incisos it changes and elides the
rest with "..." was read as the article's whole new text, so whatever the
ellipsis stood for got silently dropped. `_fusiona_articulo()` (see
nota2md.leyes) now fills those placeholders back in from the article's own
previous text instead, which is why all four clear the general floor on
their own.

UMBRAL_POR_LEY is the general floor every law clears with room to spare;
UMBRAL_PROMEDIO catches a regression too small for any one law's own floor to
trip; UMBRAL_ARTICULO_A_REPORTAR names, in a failure message, which articles
of a failing law are the bad ones instead of just how bad the law is overall.
"""

import difflib
import json
import tempfile
import unittest
from pathlib import Path

from nota2md.leyes import (
    _clave_orden,
    _segmenta_original,
    reconstruct_legal_provisions,
    normaliza_para_comparar,
)

FIXTURES = Path(__file__).parent / "fixtures" / "leyes"
HISTORIAL = json.loads((FIXTURES / "historial_44.json").read_text(encoding="utf-8"))

# Below this, a law's reconstruction is close enough to call a partial success
# even though it visibly differs from the real text. Against the SCJN
# fixtures (issue #188) the worst law is lfcpo at 0.829; every other law
# clears 0.919. The floor stays where the Diputados fixtures put it: the
# scores moved up, not down, and a floor that tracks the best measurement
# available turns every future corpus refresh into a threshold edit.
UMBRAL_POR_LEY = 0.78
# No law currently needs its own lowered floor (see the module docstring) —
# kept so a future explained shortfall has somewhere to go without dragging
# UMBRAL_POR_LEY down for every other law too.
UMBRAL_EXCEPCIONES = {}
# Above this on average, an unrelated change that quietly makes the whole
# batch worse would still be caught even where no single law crosses its own
# floor. The measured average is 0.978 over 42 laws (0.971 under the
# Diputados fixtures, over 43).
UMBRAL_PROMEDIO = 0.95
# Below this, a single article is a bad enough miss to name in a failure
# message even though its law as a whole cleared UMBRAL_POR_LEY.
UMBRAL_ARTICULO_A_REPORTAR = 0.3
def _similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(
        None, normaliza_para_comparar(a), normaliza_para_comparar(b)
    ).ratio()


def _similitud_por_ley(
    real: str, construida: str, nombre_ley: str, abrev: str
) -> tuple[float, list[tuple[str, float]]]:
    """The law's score — the average of every article's own similitud(),
    comparing `real` against `construida` article by article via
    `_segmenta_original()` (the same split reconstruct_legal_provisions() itself
    builds on) instead of diffing the two documents whole — and each
    article's ratio, worst first, so a low score says which articles to go
    look at instead of just how low it is.

    `nombre_ley` matters here too, and for the same reason it matters to
    reconstruct_legal_provisions(): a handful of these fixtures (e.g. lfaar's)
    still carry the enacting decree's own "Artículo Primero.-/Segundo.-" front
    matter, because that decree expedited more than one law at once — left
    unscoped, `_segmenta_original` picks its first instrument, silently
    handing back the *other* law's stub instead of this one's actual
    articles.

    `abrev` is unused by the comparison itself and kept in the signature for
    failure messages: until issue #188 two laws needed a whole-document
    fallback because their own ground truth would not segment (see the module
    docstring's SEGMENTACION_REAL_ROTA note), and that went away with the
    fixtures that caused it.
    """
    articulos_real = _segmenta_original(real, nombre_ley)[1]
    articulos_construida = _segmenta_original(construida, nombre_ley)[1]
    numeros = sorted(set(articulos_real) | set(articulos_construida), key=_clave_orden)
    por_articulo = [
        (numero, _similitud(articulos_real.get(numero, ""), articulos_construida.get(numero, "")))
        for numero in numeros
    ]
    promedio = sum(ratio for _, ratio in por_articulo) / len(por_articulo)
    return promedio, sorted(por_articulo, key=lambda t: t[1])


class TestConstruyeLeyContraElTextoVigenteReal(unittest.TestCase):
    # A fixed path, not a fresh temp dir per run — reconstruct_legal_provisions()
    # reads a note it already fetched here straight off disk instead of
    # refetching it, so a rerun of this slow, real-network suite only pays
    # for the notes it does not already have. Left in place on purpose: it
    # is what makes a second run fast, not a leak to clean up.
    OUTDIR = Path(tempfile.gettempdir()) / "nota2md-reconstruct-legal-provisions"

    def setUp(self):
        self.outdir = self.OUTDIR
        self.outdir.mkdir(parents=True, exist_ok=True)

    def test_cada_ley_se_reconstruye_por_encima_del_umbral(self):
        promedios = []
        peores_leyes = []
        peores_articulos = []
        for abrev, info in HISTORIAL.items():
            with self.subTest(ley=abrev):
                real = (FIXTURES / f"{abrev}.md").read_text(encoding="utf-8")
                dest = reconstruct_legal_provisions(
                    info["historial"], self.outdir, nombre_ley=info["nombre"]
                )
                construida = dest.read_text(encoding="utf-8")
                ratio, por_articulo = _similitud_por_ley(real, construida, info["nombre"], abrev)
                promedios.append(ratio)
                peores_articulos.extend(
                    (abrev, numero, r)
                    for numero, r in por_articulo
                    if r < UMBRAL_ARTICULO_A_REPORTAR
                )
                umbral = UMBRAL_EXCEPCIONES.get(abrev, UMBRAL_POR_LEY)
                if ratio < umbral:
                    peores_leyes.append((abrev, ratio))
                self.assertGreaterEqual(
                    ratio, umbral,
                    f"{abrev}: similitud {ratio:.2f} por debajo del umbral {umbral}; "
                    f"peores artículos: {por_articulo[:5]} (ver {abrev}.md)",
                )

        promedio = sum(promedios) / len(promedios)
        self.assertGreaterEqual(
            promedio, UMBRAL_PROMEDIO,
            f"similitud promedio {promedio:.3f} por debajo de {UMBRAL_PROMEDIO} "
            f"sobre {len(promedios)} leyes; peores leyes: {peores_leyes}; "
            f"peores artículos: {peores_articulos}",
        )
