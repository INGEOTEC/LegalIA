from unittest.mock import patch

import pytest

from dofjson import titulos


@pytest.fixture(autouse=True)
def _cache_dir_global_aislado(tmp_path):
    """dofjson.titulos.CACHE_DIR is the default every get_notas()/
    download_archivo()/download_dof_assets() call falls back to when a
    caller does not pass its own cache_dir -- see issue #103. Left pointing
    at the real, OS-wide default, a test that forgets to override it would
    silently read whatever this machine's user happens to have downloaded
    there (e.g. from an unrelated notebook session) instead of the network
    mock it set up, and pass or fail depending on host state alone.

    Every test gets a guaranteed-empty, per-test path instead, so a cache
    hit only ever happens when a test asks for one (via its own
    ``patch.object(titulos, "CACHE_DIR", ...)``, which layers on top of this
    and reverts to it afterwards).
    """
    with patch.object(titulos, "CACHE_DIR", tmp_path / "cache-inexistente"):
        yield
