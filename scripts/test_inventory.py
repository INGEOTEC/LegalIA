#!/usr/bin/env python3
"""Candidate duplicates across every test suite of the repository (issue #305).

A read-only inventory: it parses each ``test_*.py`` with ``ast`` (nothing is imported, nothing
is run, no network) and reports where two tests look like the same test. It exists as a
*process*: run it again after the next issue adds a test file, decide by hand, and compare
line coverage before and after (``pytest --cov``, per file, by covered-line *set*) so a
deleted test cannot silently stop covering a line.

For every ``test_*`` function or method it records the file, the name, the fixtures and
parametrisation it uses, the string and numeric literals of its body (the *inputs*), the
names called under ``assert`` and the attributes compared (the *assertions*, as a signature),
the first line of its docstring, and the rule or issue numbers it cites. Then it prints four
tables:

* **same input, same assertions**: equal literal set and equal assertion signature (exact
  duplicates);
* **subset**: the same literal set, and the assertion signature of one test is a strict
  subset of another's (the smaller one is redundant);
* **parametrisable**: tests of one file with an identical assertion signature and different
  literals (one parametrised test would do);
* **helpers**: module-level functions that are not tests, whose normalised syntax tree is the
  same (or nearly, ``--near``) in more than one file.

The heuristics over-report on purpose. Every row names both tests; a human decides, and a test
that pins a different input stays. Usage::

    python scripts/test_inventory.py                      # every suite, tables on stdout
    python scripts/test_inventory.py --suite md2akn       # only suites whose name contains it
    python scripts/test_inventory.py --json inventory.json
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: How the numbers a test cites appear: ``#291``, ``issue 304``, ``rule S4``, ``rule 12``.
_CITED = re.compile(r"#\d+|\b[Rr]ules? [A-Z]?\d+|\b[Ii]ssues? \d+")

#: Calls whose name says "this is an assertion" even outside an ``assert`` statement.
_ASSERTING_CALLS = ("assert", "raises", "warns", "approx")


@dataclass
class TestRecord:
    suite: str
    file: str
    name: str
    line: int
    fixtures: tuple[str, ...]
    parametrized: bool
    literals: frozenset
    assertions: frozenset
    doc: str
    cited: tuple[str, ...]
    source_lines: int = 0

    @property
    def ident(self) -> str:
        return f"{self.file}::{self.name}"


@dataclass
class Helper:
    suite: str
    file: str
    name: str
    line: int
    dump: str = field(repr=False)

    @property
    def ident(self) -> str:
        return f"{self.file}::{self.name}"


def suite_dirs(root: Path) -> dict[str, Path]:
    """``{suite name: tests directory}``: ``packages/<pkg>/tests`` plus the scripts' two."""
    suites = {}
    for tests in sorted((root / "packages").glob("*/tests")):
        suites[tests.parent.name] = tests
    for rel in ("scripts/embeddings/tests", "scripts/tests"):
        if (root / rel).is_dir():
            suites[rel] = root / rel
    return suites


def _is_parametrize(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr == "parametrize"


def _literals(nodes) -> set:
    """Strings and numbers under ``nodes``, docstrings left out."""
    found = set()
    for top in nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Constant) and not isinstance(node.value, (bool, type(None), bytes)):
                if isinstance(node.value, (str, int, float)):
                    found.add(node.value)
    return found


def _assertion_signature(func: ast.AST) -> frozenset:
    """What a test asserts, without the data it asserts it on: the names called and the
    attributes read inside every ``assert`` (and inside ``pytest.raises``/``self.assertX``)."""
    signature = set()

    def collect(expression: ast.AST) -> None:
        for node in ast.walk(expression):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    signature.add(f"call:{target.id}")
                elif isinstance(target, ast.Attribute):
                    signature.add(f"call:{target.attr}")
            elif isinstance(node, ast.Attribute):
                signature.add(f"attr:{node.attr}")
            elif isinstance(node, ast.Compare):
                for op in node.ops:
                    signature.add(f"cmp:{type(op).__name__}")

    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            collect(node.test)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name.startswith(_ASSERTING_CALLS) or name.startswith("assert"):
                signature.add(f"assert-call:{name}")
                for argument in node.args:
                    collect(argument)
    return frozenset(signature)


def _normalised_dump(function: ast.AST) -> str:
    """The function's tree with its name and docstring removed, so two helpers that differ only
    by name or comment compare equal."""
    clone = ast.parse(ast.unparse(function)).body[0]
    clone.name = "_"
    if (clone.body and isinstance(clone.body[0], ast.Expr)
            and isinstance(clone.body[0].value, ast.Constant) and isinstance(clone.body[0].value.value, str)):
        clone.body = clone.body[1:] or [ast.Pass()]
    return ast.dump(clone, annotate_fields=False)


def _body_after_docstring(function) -> list:
    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _record_test(suite: str, rel: str, function, prefix: str = "") -> TestRecord:
    decorators = [d for d in function.decorator_list if _is_parametrize(d)]
    docstring = (ast.get_docstring(function) or "").strip().splitlines()
    first_doc = docstring[0] if docstring else ""
    cited = tuple(sorted(set(_CITED.findall(" ".join(docstring)))))
    arguments = tuple(a.arg for a in function.args.args if a.arg not in ("self", "cls"))
    return TestRecord(
        suite=suite, file=rel, name=prefix + function.name, line=function.lineno,
        fixtures=arguments, parametrized=bool(decorators),
        literals=frozenset(_literals(_body_after_docstring(function))),
        assertions=_assertion_signature(function), doc=first_doc, cited=cited,
        source_lines=(function.end_lineno or function.lineno) - function.lineno + 1,
    )


def scan_file(suite: str, path: Path, root: Path) -> tuple[list[TestRecord], list[Helper]]:
    """The tests (module-level and in classes) and the module-level helpers of one file."""
    rel = path.relative_to(root).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tests, helpers = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                tests.append(_record_test(suite, rel, node))
            elif not any("fixture" in ast.unparse(d) for d in node.decorator_list):
                helpers.append(Helper(suite, rel, node.name, node.lineno, _normalised_dump(node)))
        elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(
                (isinstance(b, ast.Attribute) and b.attr == "TestCase") or getattr(b, "id", "") == "TestCase"
                for b in node.bases)):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    tests.append(_record_test(suite, rel, item, prefix=f"{node.name}."))
    return tests, helpers


def scan(root: Path, only: str | None = None) -> tuple[list[TestRecord], list[Helper], dict[str, int]]:
    tests, helpers, files = [], [], {}
    for suite, directory in suite_dirs(root).items():
        if only and only not in suite:
            continue
        paths = sorted(directory.rglob("test_*.py"))
        if not paths:
            continue
        files[suite] = len(paths)
        for path in paths:
            t, h = scan_file(suite, path, root)
            tests.extend(t)
            helpers.extend(h)
    return tests, helpers, files


# -- the four tables ---------------------------------------------------------------------


def _informative(test: TestRecord) -> bool:
    return bool(test.literals) and bool(test.assertions)


def exact_duplicates(tests: list[TestRecord]) -> list[list[TestRecord]]:
    groups = defaultdict(list)
    for test in tests:
        if _informative(test) and not test.parametrized:
            groups[(test.suite, test.literals, test.assertions)].append(test)
    return [g for g in groups.values() if len(g) > 1]


def subsets(tests: list[TestRecord]) -> list[tuple[TestRecord, TestRecord]]:
    """``(smaller, larger)``: same literals, the smaller's assertions a strict subset."""
    by_literals = defaultdict(list)
    for test in tests:
        if _informative(test) and not test.parametrized:
            by_literals[(test.suite, test.literals)].append(test)
    rows = []
    for group in by_literals.values():
        for small in group:
            for large in group:
                if small is not large and small.assertions < large.assertions:
                    rows.append((small, large))
    return rows


def parametrisable(tests: list[TestRecord], min_signature: int = 3) -> list[list[TestRecord]]:
    """Tests of one file that assert the same things (at least ``min_signature`` calls,
    attributes or comparisons: fewer matches nearly every test) on different inputs."""
    groups = defaultdict(list)
    for test in tests:
        if len(test.assertions) >= min_signature and not test.parametrized:
            groups[(test.file, test.assertions)].append(test)
    return [g for g in groups.values() if len(g) > 1 and len({t.literals for t in g}) > 1]


def _similar(first: str, second: str, ratio: float) -> bool:
    """`difflib` similarity of two dumps, with the cheap upper bounds tried first: the helper
    table has a few hundred entries and the full ratio is quadratic in the dump length."""
    matcher = difflib.SequenceMatcher(None, first, second, autojunk=False)
    return (matcher.real_quick_ratio() >= ratio and matcher.quick_ratio() >= ratio
            and matcher.ratio() >= ratio)


def shared_helpers(helpers: list[Helper], near: float = 1.0) -> list[list[Helper]]:
    """Helpers defined in more than one file of a suite with the same (``near`` = 1.0) or nearly
    the same (``near`` < 1.0, by ``difflib`` ratio of the dumps) normalised tree."""
    rows = []
    by_suite = defaultdict(list)
    for helper in helpers:
        by_suite[helper.suite].append(helper)
    for group in by_suite.values():
        if near >= 1.0:
            by_dump = defaultdict(list)
            for helper in group:
                by_dump[helper.dump].append(helper)
            rows.extend(g for g in by_dump.values() if len({h.file for h in g}) > 1)
            continue
        used = set()
        for i, a in enumerate(group):
            if i in used:
                continue
            cluster = [a]
            for j in range(i + 1, len(group)):
                b = group[j]
                if j not in used and b.file != a.file and (a.dump == b.dump or _similar(a.dump, b.dump, near)):
                    cluster.append(b)
                    used.add(j)
            if len({h.file for h in cluster}) > 1:
                rows.append(cluster)
    return rows


# -- output -------------------------------------------------------------------------------


def _row(test: TestRecord) -> str:
    return f"{test.ident} (line {test.line}, {test.source_lines} lines)"


def report(tests, helpers, files, near: float, min_signature: int = 3) -> dict:
    exact = exact_duplicates(tests)
    small = subsets(tests)
    param = parametrisable(tests, min_signature)
    helper_rows = shared_helpers(helpers, near)
    print("Tests per suite (files, tests):")
    for suite, count in files.items():
        print(f"  {suite:32} {count:4} files {sum(1 for t in tests if t.suite == suite):5} tests")
    print(f"  {'total':32} {sum(files.values()):4} files {len(tests):5} tests\n")
    print(f"== same input, same assertions: {len(exact)} groups")
    for group in exact:
        print("  - " + "\n    ".join(_row(t) for t in group))
    print(f"\n== subset (the first is redundant against the second): {len(small)} pairs")
    for a, b in small:
        print(f"  - {_row(a)}\n    <= {_row(b)}")
    print(f"\n== parametrisable (one file, one assertion signature, different inputs): {len(param)} groups")
    for group in param:
        print("  - " + "\n    ".join(_row(t) for t in group))
    print(f"\n== helpers defined in more than one file: {len(helper_rows)} groups")
    for group in helper_rows:
        print("  - " + "\n    ".join(f"{h.ident} (line {h.line})" for h in group))
    return {
        "suites": files,
        "tests": [
            {"suite": t.suite, "file": t.file, "name": t.name, "line": t.line, "fixtures": list(t.fixtures),
             "parametrized": t.parametrized, "doc": t.doc, "cited": list(t.cited),
             "literals": len(t.literals), "assertions": sorted(t.assertions)}
            for t in tests
        ],
        "exact": [[t.ident for t in g] for g in exact],
        "subset": [[a.ident, b.ident] for a, b in small],
        "parametrisable": [[t.ident for t in g] for g in param],
        "helpers": [[h.ident for h in g] for g in helper_rows],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this one)")
    parser.add_argument("--suite", help="only suites whose name contains this text")
    parser.add_argument("--json", type=Path, metavar="PATH", help="also write the inventory as JSON")
    parser.add_argument("--near", type=float, default=1.0, metavar="RATIO",
                        help="helpers: also report nearly identical ones (difflib ratio, default 1.0 = identical)")
    parser.add_argument("--min-signature", type=int, default=3, metavar="N",
                        help="parametrisable: the least assertion-signature size a group must share (default 3)")
    args = parser.parse_args(argv)
    tests, helpers, files = scan(args.root, args.suite)
    data = report(tests, helpers, files, args.near, args.min_signature)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
