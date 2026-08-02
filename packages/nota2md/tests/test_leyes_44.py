"""Check reconstruct_legal_provisions() against the 43 laws it was designed against.

These are integration tests, not unit tests: reconstruct_legal_provisions() fetches each
DOF note over the network (SIDOF, with the dofweb fallback), so this module
makes real HTTP calls and is slower than the rest of the suite. The 43 laws
are every federal law with exactly 2 or 3 reforms, all published after
1999-01-01, minus ligie_2022 (see fixtures/leyes/historial_44.json) — small
enough that a handful of reforms plausibly reconstructs the whole thing.

fixtures/leyes/<abrev>.md is the ground truth: Diputados' own "texto vigente"
PDF for the law, cleaned up by limpia_texto_ley(). reconstruct_legal_provisions() never reads
it or anything derived from it — the comparison is between two independently
sourced texts.

Similarity is measured article by article, not over the whole document:
`_segmenta_original()` (the same code reconstruct_legal_provisions() itself uses
to split a note into one entry per article) splits both the real and the
constructed text into their article dictionaries, and the law's score is the
average of every article's own ratio. Comparing the whole document in one
shot lets a long run of shared boilerplate (headers, repeated phrasing)
paper over a handful of badly-reconstructed articles; comparing article by
article cannot, and it also says which articles are the bad ones instead of
just how bad the law is overall — see PEORES_ARTICULOS in a failure message.
It is also far cheaper: difflib's matching-block search scales worse than
linearly with input size, so diffing one document of length N costs more
than diffing N articles of length 1 each summed together.

Similarity, not equality, is what is asserted. reconstruct_legal_provisions() is a rule-based
reconstruction (see nota2md.leyes' module docstring).

lspcapf used to be this suite's one unexplained shortfall: it recognizes
every one of its 80 articles correctly but, compared whole-document, used to
land at 0.21 for a reason nobody had tracked down — a guess was that its 2006
reform, which added a whole new Subsistema de Desarrollo Profesional, did
something to article ordering. Article by article it scores 0.97: the guess
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

lgpdppso and lopjf are the opposite problem: not reconstruct_legal_provisions()
falling short, but their own "texto vigente" PDF fixtures losing the blank
line between articles across long stretches of text extraction, so
_segmenta_original reads a run of a dozen-plus articles as the single one
that happens to open it. Compared article by article that scores near zero
on both sides — not because the reconstruction is wrong (their whole-document
ratio is 0.93-0.97) but because the ground truth itself won't segment.
SEGMENTACION_REAL_ROTA falls back to comparing those two whole, as every law
was before this module's article-by-article rewrite.

UMBRAL_POR_LEY is the general floor every other law clears with room to
spare; UMBRAL_PROMEDIO catches a regression too small for any one law's own
floor to trip; UMBRAL_ARTICULO_A_REPORTAR names, in a failure message, which
articles of a failing law are the bad ones instead of just how bad the law
is overall.
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
# even though it visibly differs from the real text. The worst law measured
# (lfpccs) lands at 0.84; every other law clears 0.91.
UMBRAL_POR_LEY = 0.78
# No law currently needs its own lowered floor (see the module docstring) —
# kept so a future explained shortfall has somewhere to go without dragging
# UMBRAL_POR_LEY down for every other law too.
UMBRAL_EXCEPCIONES = {}
# Above this on average, an unrelated change that quietly makes the whole
# batch worse would still be caught even where no single law crosses its own
# floor. The measured average is 0.971.
UMBRAL_PROMEDIO = 0.95
# Below this, a single article is a bad enough miss to name in a failure
# message even though its law as a whole cleared UMBRAL_POR_LEY.
UMBRAL_ARTICULO_A_REPORTAR = 0.3
# lgpdppso and lopjf's own "texto vigente" PDFs lose the blank line between
# articles across long stretches of text extraction, so _segmenta_original
# reads a run of a dozen-plus articles as the single one that happens to
# open it — comparing article by article then scores near zero on both,
# not because reconstruct_legal_provisions did anything wrong (their whole-document
# ratio is ~0.93-0.97) but because the ground truth itself won't segment.
# Compared whole instead, like every law was before this module's article-
# by-article rewrite.
SEGMENTACION_REAL_ROTA = {"lgpdppso", "lopjf"}


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
    reconstruct_legal_provisions(): a handful of these fixtures' own "texto
    vigente" PDFs (e.g. lfaar's) still carry the enacting decree's own
    "Artículo Primero.-/Segundo.-" front matter, because that decree
    expedited more than one law at once — left unscoped, `_segmenta_original`
    picks its first instrument, silently handing back the *other* law's
    stub instead of this one's actual articles.

    `abrev` in SEGMENTACION_REAL_ROTA instead compares `real` and
    `construida` whole (see that set) — the article-by-article split is
    meaningless there because it is the ground truth, not the reconstruction,
    that fails to segment.
    """
    if abrev in SEGMENTACION_REAL_ROTA:
        ratio = _similitud(real, construida)
        return ratio, [("(documento completo)", ratio)]

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
                dest = reconstruct_legal_provisions(info["historial"], self.outdir, info["nombre"])
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
