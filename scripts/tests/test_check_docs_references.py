"""`scripts/check_docs_references.py` (issue #306) on a toy repository built under `tmp_path`: an
existing and a missing path, a dotted name that resolves and one that does not (by `ast`, never by
import), a flag that exists and one that does not, the history allow-list (frozen documents, a
changelog section, a line that says the thing is gone) and the scratch allow-list."""

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_docs_references.py"
spec = importlib.util.spec_from_file_location("check_docs_references", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules["check_docs_references"] = checker
spec.loader.exec_module(checker)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _toy(tmp_path: Path) -> Path:
    _write(tmp_path, "packages/scjn/scjn/__init__.py", """
        from scjn.api import ScjnApi, snapshot

        __version__ = "0.5.1"
    """)
    _write(tmp_path, "packages/scjn/scjn/api.py", """
        LIMIT = 3

        class ScjnApi:
            def reformas_of_ordenamiento(self):
                self.cache = 1

        def snapshot():
            pass
    """)
    _write(tmp_path, "scripts/tool.py", """
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--real-flag")
        parser.add_argument("-n", "--short")
    """)
    _write(tmp_path, "docs/source/index.rst", "title\n=====\n")
    return tmp_path


def _report(root: Path, document: str, text: str):
    _write(root, document, text)
    report = checker.run(root, [root / document])
    return report


def _kinds(report):
    return sorted((f.kind, f.text, f.history) for f in report.findings)


def test_an_existing_and_a_missing_path(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", "See `scripts/tool.py` and `scripts/absent.py`, also `docs/source`.\n")
    assert _kinds(report) == [("path", "scripts/absent.py", False)]


def test_a_path_with_a_placeholder_is_checked_up_to_the_placeholder(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", "`scripts/<name>.py` and `nowhere/<name>.py` and `scripts/*.py`.\n")
    assert _kinds(report) == []  # `nowhere/` is not a top-level entry of this repository: not a repo path


def test_dotted_names_resolve_by_ast_without_importing(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", (
        "`scjn.api.ScjnApi.reformas_of_ordenamiento`, `scjn.snapshot()`, `scjn.api.LIMIT`, `scjn.ScjnApi`, "
        "`scjn.api.ScjnApi.cache`, `scjn.api`, `scjn.__version__` but not `scjn.api.nothing`, "
        "`scjn.ScjnApi.missing` or `scjn.nomodule.thing`.\n"
    ))
    assert _kinds(report) == [("name", "scjn.ScjnApi.missing", False), ("name", "scjn.api.nothing", False),
                              ("name", "scjn.nomodule.thing", False)]
    assert "scjn.api" not in sys.modules or sys.modules["scjn.api"].__file__ != str(
        root / "packages/scjn/scjn/api.py")


def test_a_flag_is_checked_against_the_script_named_beside_it(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", (
        "Run `scripts/tool.py --real-flag 1 -n x --ghost` or `scripts/tool.py --pytest-only`.\n"
        "A lone `--real-flag` exists somewhere; `--nowhere-at-all` does not; `--ignore` is pytest's.\n"
    ))
    assert _kinds(report) == [("flag", "--ghost", False), ("flag", "--nowhere-at-all", False),
                              ("flag", "--pytest-only", False)]


def test_flags_in_a_fenced_command_are_read_next_to_its_script_across_continuations(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", (
        "```bash\n"
        "python scripts/tool.py --real-flag 1 \\\n"
        "    --ghost 2\n"
        "```\n"
    ))
    assert _kinds(report) == [("flag", "--ghost", False)]


def test_the_history_allow_list(tmp_path):
    root = _toy(tmp_path)
    text = "`scripts/deleted.py` and `scjn.gone.name`.\n"
    # A frozen history document, a changelog section and a line that says it is gone: reported, not failing.
    frozen = _report(root, "docs/HISTORY.md", text)
    assert frozen.findings and all(f.document == "docs/HISTORY.md" for f in frozen.findings)
    changelog = _report(root, "packages/scjn/README.md",
                        "# scjn\n\nFine.\n\n## Changelog\n\n- `scripts/deleted.py` was here.\n\n## Other\n\n`scripts/after.py`\n")
    assert _kinds(changelog) == [("path", "scripts/after.py", False), ("path", "scripts/deleted.py", True)]
    gone = _report(root, "README.md", "`scripts/deleted.py` no longer exists, but `scripts/missing.py` does.\n")
    assert all(f.history for f in gone.findings)


def test_main_exits_one_only_for_what_is_not_history(tmp_path, capsys):
    root = _toy(tmp_path)
    _write(root, "README.md", "`scripts/tool.py`\n")
    _write(root, "docs/HISTORY.md", "`scripts/deleted.py`\n")
    out = tmp_path / "out.json"
    assert checker.main(["--root", str(root), "--json", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["unresolved"] == [] and len(data["history"]) == 1
    _write(root, "README.md", "`scripts/missing.py`\n")
    assert checker.main(["--root", str(root), "--quiet"]) == 1
    assert "1 unresolved" in capsys.readouterr().out


def test_scratch_paths_are_never_checked_and_issue_numbers_are_listed(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "README.md", (
        "`emb-run-atlas/matrix.npy`, `scripts/scjn/leyes/x.md`, `output/x.json`, `~/.cache/scjn`, "
        "`website/pages/atlas/pairs/1-2.json`; issues #291 and #300 (and `#12` is not an issue: one digit pair).\n"
    ))
    assert report.findings == []
    assert sorted(report.issues) == ["12", "291", "300"]


def test_rst_roles_and_automodule_are_read(tmp_path):
    root = _toy(tmp_path)
    report = _report(root, "docs/source/api.rst", (
        ".. automodule:: scjn.api\n\n"
        ":py:func:`~scjn.snapshot` and :py:class:`scjn.api.Missing`.\n\n"
        ">>> scjn.api.not_checked_in_a_doctest\n"
    ))
    assert _kinds(report) == [("name", "scjn.api.Missing", False)]

def test_the_real_repository_has_no_unresolved_reference():
    report = checker.run(checker.ROOT)
    failing = [f for f in report.findings
               if f.document not in checker.HISTORY_DOCS and not f.history]
    assert failing == [], "\n".join(f"{f.document}:{f.line} {f.kind} {f.text}" for f in failing)
