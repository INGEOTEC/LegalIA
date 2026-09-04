from unittest.mock import patch

import pytest

from scjn import cache, release


@pytest.fixture(autouse=True)
def _cache_dir_global_aislado(tmp_path):
    """`scjn.cache.CACHE_DIR` is the default every reader in `scjn.release`
    falls back to when a caller passes no `cache_dir` of its own. Left
    pointing at the real, OS-wide default, a test that forgets to override it
    would silently read whatever this machine happens to have cached there
    instead of the fixture it set up (or, worse, the real ~380 MB corpus).

    Also clears `download_scjn_leyes_index`'s in-process memo, keyed by cache
    directory, so a stale entry from one test never leaks into the next."""
    release._MEMO_INDICE_GLOBAL.clear()
    with patch.object(cache, "CACHE_DIR", tmp_path / "cache-inexistente"):
        yield
    release._MEMO_INDICE_GLOBAL.clear()
