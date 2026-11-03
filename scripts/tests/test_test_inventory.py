"""`scripts/test_inventory.py` (issue #305) on a toy repository built under `tmp_path`: it must
find an exact duplicate, a subset, a parametrisable group and a helper shared by two files,
and cope with a file with no tests and a method inside a `unittest.TestCase`."""

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "test_inventory.py"
spec = importlib.util.spec_from_file_location("test_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
sys.modules["test_inventory"] = inventory
spec.loader.exec_module(inventory)


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _toy(tmp_path: Path) -> Path:
    _write(tmp_path, "packages/pkg/tests/test_a.py", '''
        import unittest

        def build(x):
            """Builds a thing."""
            return {"x": x, "y": [x, x]}

        def test_exact_one():
            """Pins the thing (issue #12, rule S4)."""
            thing = build("alpha")
            assert thing["x"] == "alpha"
            assert len(thing["y"]) == 2

        def test_exact_two():
            thing = build("alpha")
            assert thing["x"] == "alpha"
            assert len(thing["y"]) == 2

        def test_small():
            thing = build("beta")
            key = "x"  # the same input as `test_large`, asserted less
            assert len(thing["y"]) == 2

        def test_large():
            thing = build("beta")
            assert len(thing["y"]) == 2
            assert thing.get("x") == "beta"

        def test_param_one():
            thing = build("gamma")
            assert thing.get("x") == "gamma"
            assert len(thing["y"]) == 2
            assert isinstance(thing, dict)

        def test_param_two():
            thing = build("delta")
            assert thing.get("x") == "delta"
            assert len(thing["y"]) == 2
            assert isinstance(thing, dict)

        class TestCaseStyle(unittest.TestCase):
            def test_method(self):
                self.assertEqual(build("epsilon")["x"], "epsilon")
    ''')
    _write(tmp_path, "packages/pkg/tests/test_b.py", '''
        def build(x):
            """The same helper, defined again."""
            return {"x": x, "y": [x, x]}

        def test_other():
            assert build("zeta")["x"] == "zeta"
    ''')
    _write(tmp_path, "packages/pkg/tests/test_empty.py", '"""No tests here."""\n')
    _write(tmp_path, "scripts/tests/test_scripts.py", "def test_s():\n    assert 1 + 1 == 2\n")
    return tmp_path


def _names(rows):
    return {t.name for t in rows}


def test_scan_finds_functions_methods_and_a_file_with_no_tests(tmp_path):
    tests, helpers, files = inventory.scan(_toy(tmp_path))
    assert files == {"pkg": 3, "scripts/tests": 1}
    names = {t.name for t in tests}
    assert {"test_exact_one", "TestCaseStyle.test_method", "test_other", "test_s"} <= names
    first = next(t for t in tests if t.name == "test_exact_one")
    assert first.doc == "Pins the thing (issue #12, rule S4)."
    assert first.cited == ("#12", "rule S4")
    assert "alpha" in first.literals and 2 in first.literals
    assert not any(t.file.endswith("test_empty.py") for t in tests)


def test_exact_duplicates_are_grouped(tmp_path):
    tests, _, _ = inventory.scan(_toy(tmp_path))
    groups = inventory.exact_duplicates(tests)
    assert [sorted(_names(g)) for g in groups] == [["test_exact_one", "test_exact_two"]]


def test_a_subset_names_the_redundant_test_first(tmp_path):
    tests, _, _ = inventory.scan(_toy(tmp_path))
    rows = [(a.name, b.name) for a, b in inventory.subsets(tests)]
    assert ("test_small", "test_large") in rows
    assert ("test_large", "test_small") not in rows


def test_tests_with_the_same_assertions_on_different_inputs_are_parametrisable(tmp_path):
    tests, _, _ = inventory.scan(_toy(tmp_path))
    groups = [sorted(_names(g)) for g in inventory.parametrisable(tests)]
    assert ["test_param_one", "test_param_two"] in groups
    # The two exact duplicates share their inputs, so they are not "different inputs".
    assert not any("test_exact_one" in g for g in groups)


def test_a_helper_defined_in_two_files_is_reported_once_as_a_group(tmp_path):
    _, helpers, _ = inventory.scan(_toy(tmp_path))
    groups = inventory.shared_helpers(helpers)
    assert len(groups) == 1
    assert {h.file for h in groups[0]} == {
        "packages/pkg/tests/test_a.py", "packages/pkg/tests/test_b.py"}


def test_nearly_identical_helpers_need_the_near_option(tmp_path):
    root = _toy(tmp_path)
    _write(root, "packages/pkg/tests/test_c.py", '''
        def tweak(x):
            return {"x": x, "y": [x, x, x]}

        def test_c():
            assert tweak("eta")["x"] == "eta"
    ''')
    _, helpers, _ = inventory.scan(root)
    assert all(len(g) == 2 for g in inventory.shared_helpers(helpers))
    loose = inventory.shared_helpers(helpers, near=0.8)
    assert any(len({h.file for h in g}) == 3 for g in loose)


def test_the_command_line_writes_json_and_filters_by_suite(tmp_path, capsys):
    root = _toy(tmp_path)
    out = tmp_path / "inv.json"
    assert inventory.main(["--root", str(root), "--suite", "pkg", "--json", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data["suites"]) == {"pkg"}
    assert data["exact"] and data["helpers"]
    printed = capsys.readouterr().out
    assert "same input, same assertions: 1 groups" in printed
    assert "scripts/tests" not in printed.split("==")[0]


def test_the_real_repository_scans_without_importing_anything():
    before = set(sys.modules)
    tests, _, files = inventory.scan(inventory.ROOT)
    assert tests and files
    assert not {m for m in set(sys.modules) - before if m.split(".")[0] in ("md2akn", "scjn", "nota2md", "dofjson")}
