from unittest.mock import patch

import pytest

import nota2md.linking as linking
import scjn.release as release
from nota2md import cache

#: An index that covers nothing -- the shape `download_scjn_leyes_index`
#: returns, with no codNota in it, so `snapshot_de_codNota` answers None and
#: `legal_provisions` falls through to the DOF exactly as it does for a note
#: the corpus does not cover.
INDICE_VACIO = {
    "generado": "2026-01-01T00:00:00+00:00",
    "coleccion": "leyes",
    "instrumentos": {},
    "codNota": {},
}


@pytest.fixture(autouse=True)
def _cache_dir_global_aislado(tmp_path):
    """`nota2md.cache.CACHE_DIR`/`scjn.cache.CACHE_DIR` are the defaults every
    release read falls back to when a caller passes no `cache_dir` of its own
    (issues #117/#209). Left pointing at their real, OS-wide defaults, a test
    that forgets to override them would silently read whatever this machine
    happens to have downloaded there instead of the network mock it set up,
    and pass or fail depending on host state alone -- same reasoning as
    `packages/dofjson/tests/conftest.py`.

    `download_scjn_leyes_index` is stubbed out to `INDICE_VACIO` for the same
    reason one step earlier: `legal_provisions` now consults it on every
    "auto" call, and without this every DOF-path test in `test_builder.py`
    would reach the real `scjn-leyes` release just to be told the codNota is
    not in it. Patched where `nota2md.linking` looks it up (issue #209 made
    it a module-level import there), not where `scjn.release` defines it — a
    test that wants the SCJN path patches this again on top (and the patch
    reverts afterwards).

    Also clears `download_scjn_leyes_index`'s in-process memo, which would
    otherwise leak one test's fabricated index into the next.
    """
    release._MEMO_INDICE_GLOBAL.clear()
    with patch.object(cache, "CACHE_DIR", tmp_path / "cache-inexistente"), \
            patch("scjn.cache.CACHE_DIR", tmp_path / "cache-scjn-inexistente"), \
            patch.object(linking, "download_scjn_leyes_index", return_value=INDICE_VACIO):
        yield
    release._MEMO_INDICE_GLOBAL.clear()
