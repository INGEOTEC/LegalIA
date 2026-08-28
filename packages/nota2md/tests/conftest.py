from unittest.mock import patch

import pytest

from nota2md import cache, scjn

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
    """`nota2md.cache.CACHE_DIR` is the default every release read falls back
    to when a caller passes no `cache_dir` of its own (issue #117). Left
    pointing at the real, OS-wide default, a test that forgets to override it
    would silently read whatever this machine's user happens to have
    downloaded there instead of the network mock it set up, and pass or fail
    depending on host state alone -- same reasoning as
    `packages/dofjson/tests/conftest.py`.

    The reverse index is stubbed out to `INDICE_VACIO` for the same reason
    one step earlier: `legal_provisions` now consults it on every "auto" call,
    and without this every DOF-path test in `test_builder.py` would reach the
    real `scjn-leyes` release over the network just to be told the codNota is
    not in it. A test that wants the SCJN path patches this again on top (and
    the patch reverts afterwards).

    Also clears `download_scjn_leyes_index`'s in-process memo, which would
    otherwise leak one test's fabricated index into the next.
    """
    scjn._MEMO_INDICE_GLOBAL.clear()
    with patch.object(cache, "CACHE_DIR", tmp_path / "cache-inexistente"), \
            patch.object(scjn, "download_scjn_leyes_index", return_value=INDICE_VACIO):
        yield
    scjn._MEMO_INDICE_GLOBAL.clear()
