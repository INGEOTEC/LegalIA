"""The `dof2md` tombstone: it must raise on import, and its version must stay
readable without importing it (issue #228)."""
import ast
import sys
from pathlib import Path

import pytest

INIT = Path(__file__).resolve().parents[1] / "dof2md" / "__init__.py"


def test_import_raises_and_names_the_new_package():
    """`import dof2md` fails loudly and says where the code went."""
    sys.modules.pop("dof2md", None)
    with pytest.raises(ImportError) as raised:
        import dof2md  # noqa: F401
    message = str(raised.value)
    assert "document2md" in message
    assert "pip install document2md" in message


def test_version_is_readable_without_importing():
    """`__version__` is a plain literal assigned *above* the `raise`.

    This is what makes a module that raises on import publishable: setuptools'
    `attr:` resolution and `scripts/check_package_versions.py`'s
    `local_version()` both read it statically, the way this test does. Putting
    the `raise` first would break the publish workflow silently.
    """
    tree = ast.parse(INIT.read_text(), filename=str(INIT))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            assert ast.literal_eval(node.value) == "0.3.0"
            break
    else:
        pytest.fail(f"__version__ not found in {INIT}")
