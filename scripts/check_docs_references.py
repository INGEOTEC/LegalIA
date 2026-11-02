#!/usr/bin/env python3
"""Does every path, dotted name, CLI flag and issue number a document cites still exist? (issue #306)

A read-only checker over the repository's documents: `CLAUDE.md`, `README.md`, every package
README, `packages/md2akn/RULES.md`, `scripts/embeddings/README.md`, `docs/HISTORY.md`,
`docs/source/*.rst`, `website/pages/*.qmd` and the release bodies in `.github/*.md`. It reads
what is between backticks (and the lines of fenced command blocks) and resolves:

* **paths** -- `packages/scjn/scjn/api.py`, `scripts/embeddings/measure_trees.py`,
  `website/pages/atlas/atlas.json`: they must exist (relative to the repository, to the document
  or to the package the document belongs to). A path with a `<placeholder>` or a glob is checked
  up to the placeholder. Gitignored scratch (`emb-run*/`, `scripts/scjn/`, `output/`, `~/.cache/`,
  the installed Atlas pair sets, ...) is never checked;
* **dotted names** -- `scjn.api.ScjnApi.reformas_of_ordenamiento`, `md2akn.AknNode.walk`: resolved
  over the package sources by ``ast``, never by import (modules, then a function, class, constant,
  re-export, or a method of a class one level down);
* **CLI flags** -- `--all-snapshots` next to `scripts/embeddings/measure_trees.py`: it must be an
  ``add_argument`` flag of that script (or of the console script named beside it); a flag with no
  script beside it must exist in *some* script of the repository or be an external tool's;
* **issue numbers** -- `#291`: collected and listed; checked with ``gh issue view`` only with
  ``--issues``.

`docs/HISTORY.md`, `.github/migracion-diputados.md` and `.github/historial-legislativo.md` are
frozen history, and so is a `## Changelog` section or a line that says the thing is gone ("no
longer exists", "deleted", ...): what they cite may have been removed since, so they are reported
and never fail the run. Exit status 1 when anything else is unresolved.

    python scripts/check_docs_references.py                 # the report, exit 1 on a problem
    python scripts/check_docs_references.py --json out.json
    python scripts/check_docs_references.py --issues        # also ask GitHub about #N (network)
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PACKAGES = ("dofjson", "scjn", "nota2md", "md2akn", "legalvec")

#: Documents that describe the past: their references are listed, never counted as failures.
HISTORY_DOCS = (
    "docs/HISTORY.md",
    ".github/migracion-diputados.md",
    ".github/historial-legislativo.md",
)

#: The documents, as globs relative to the repository root.
DOCUMENT_GLOBS = (
    "CLAUDE.md", "README.md", "packages/*/README.md", "packages/md2akn/RULES.md",
    "scripts/embeddings/README.md", "docs/HISTORY.md", "docs/source/*.rst",
    "website/pages/*.qmd", ".github/*.md",
)

#: Paths that are local scratch or machine state: gitignored directories and files, the caches.
SCRATCH_PREFIXES = (
    "emb-run", "/emb-run", "output/", "/output/", "scripts/scjn/", "/scripts/scjn/",
    "notas-archivo/", "/notas-archivo/", "~/", "$HOME", "/tmp/", "/home/", "website/_site",
    "website/pages/atlas/pairs", "/website/pages/atlas/pairs", "website/pages/instruments/files",
    "/website/pages/instruments/files", "docs/build", ".venv", "/.venv", "/usr/", "/etc/",
    "website/_freeze", ".git/", "node_modules", "tests/fixtures", "scripts/embeddings/runs",
    "PUBLICAR.md", "/PUBLICAR.md", "indice-global", "MANIFEST.md",
)

#: Flags of tools that are not scripts of this repository.
EXTERNAL_FLAGS = {
    "--ignore", "--cov", "--cov-report", "--group", "--with", "--no-freeze", "--execute", "--clobber",
    "--pattern", "--dir", "--repo", "--json", "--version", "--help", "--no-deps", "--editable",
    "--upgrade", "--output", "--exclusive", "--cpus-per-task", "--mem", "--time", "--partition",
    "--nodelist", "--exclude", "--gres", "--wrap", "--job-name", "--parsable", "--jobs", "--branch",
    "--force", "--hard", "--stat", "--name-only", "--attributes", "--install", "--title", "--body",
    "--notes-file", "--generate-notes", "--latest", "--verify-tag", "--prerelease", "--draft",
    "--delete-asset", "--yes", "--cleanup-tag", "-q", "--quiet", "--tb", "--durations", "--lf",
    "--collect-only", "-k", "-x", "-v", "-s", "-p", "--maxfail", "--import-mode", "--rootdir",
    "--strict-markers", "--co", "--no-header", "--extra", "--all-extras", "--frozen", "--locked",
    "--python", "--system", "--no-cache", "--only-binary", "--index-url", "--break-system-packages",
    "--cached", "--oneline", "--format", "--since", "--grep", "--author", "--root", "--no-verify", "--ignore-missing",
    "--discussion-category", "--target", "--tag", "--assets", "--jq", "--field", "--method",
    "--header", "--input", "--paginate", "--silent", "--slurp", "--query", "--limit", "--state",
    "--label", "--assignee", "--web", "--comments", "--fixed-strings", "--ssh", "--user",
    "--local", "--global", "--system", "--no-progress", "--progress-bar", "--depth", "--filter",
    "--sparse", "--shallow-since", "--allow-empty", "--amend", "--signoff", "--squash", "--no-ff",
    "--ff-only", "--rebase", "--prune", "--tags", "--follow-tags", "--set-upstream", "--delete",
    "--timeout", "--retry", "--connect-timeout", "--max-time", "-o", "--location", "--fail",
    "--remote-name", "--compressed", "--no-clobber", "-O", "--continue", "--wait", "--dry-run",
}

_EXTENSIONS = (".py", ".md", ".json", ".rst", ".qmd", ".yml", ".yaml", ".toml", ".sh", ".js", ".css",
               ".bib", ".parquet", ".gz", ".tgz", ".txt", ".ipynb", ".html", ".cfg", ".ini")
_ROOTS = ("packages/", "scripts/", "website/", "docs/", ".github/", "tests/")


_SPAN = re.compile(r"``([^`\n]+?)``|`([^`\n]+?)`")
_ISSUE = re.compile(r"(?<![\w&/])#(\d{2,4})\b")
_DOTTED = re.compile(r"^~?\.?((?:%s)(?:\.[A-Za-z_]\w*)+)(?:\(\))?$" % "|".join(PACKAGES))
_FLAG = re.compile(r"(?<![\w-])(--[a-z][\w-]*)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
#: A line that says the thing it names is gone legitimately names it.
_GONE = re.compile(r"\b(no longer|deleted|removed|retired|replaced|renamed|formerly|used to|gone|"
                   r"does not exist|were moved|was moved|moved out|moved to|left this repository|"
                   r"dropped|abandoned|superseded|tombstone)\b", re.I)


@dataclass
class Finding:
    document: str
    line: int
    kind: str          # path | name | flag
    text: str
    detail: str = ""
    #: True when the line sits in a changelog or says the thing is gone: reported, never failing.
    history: bool = False


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    issues: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    checked: dict[str, int] = field(default_factory=lambda: defaultdict(int))


# -- what exists --------------------------------------------------------------------------


class Repository:
    """Lazy, read-only facts about the repository: files, package names, script flags."""

    def __init__(self, root: Path):
        self.root = root
        self._basenames: set[str] | None = None
        self._modules: dict[Path, ast.Module] = {}
        self._flags: dict[str, set[str]] = {}
        self._all_flags: set[str] | None = None
        #: Top-level entries of the repository a path may start with (tracked or not).
        self.top_level = {p.name for p in root.iterdir() if p.name not in (".git", ".venv")} | {"tests"}

    # paths
    def basenames(self) -> set[str]:
        if self._basenames is None:
            names = set()
            for path in self.root.rglob("*"):
                parts = path.relative_to(self.root).parts
                if any(p in (".git", ".venv", "node_modules", "__pycache__", "_site", "build") for p in parts):
                    continue
                names.add(path.name)
            self._basenames = names
        return self._basenames

    def path_exists(self, token: str, document: Path) -> bool:
        token = token.strip().rstrip("/,.;:")
        token = token.lstrip("/") if token.startswith("/") and not token.startswith("//") else token
        bases = [self.root, document.parent]
        owner = _package_of(document, self.root)
        if owner:
            bases += [self.root / "packages" / owner, self.root / "packages" / owner / owner,
                      self.root / "packages" / owner / "tests"]
        # A tests/... or fixtures path may be relative to any package.
        bases += [self.root / "packages" / p for p in PACKAGES]
        bases += [self.root / "scripts", self.root / "scripts" / "embeddings", self.root / "website"]
        for base in bases:
            if _exists_with_placeholders(base, token):
                return True
        return False

    # modules / names
    def _parse(self, path: Path) -> ast.Module | None:
        if path not in self._modules:
            try:
                self._modules[path] = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                self._modules[path] = None
        return self._modules[path]

    def name_resolves(self, dotted: str) -> bool:
        parts = dotted.split(".")
        package_dir = self.root / "packages" / parts[0] / parts[0]
        if not package_dir.is_dir():
            return False
        # Walk down modules/packages as far as they go.
        current, index = package_dir, 1
        while index < len(parts):
            nxt_dir = current / parts[index]
            nxt_file = current / f"{parts[index]}.py"
            if nxt_dir.is_dir() and (nxt_dir / "__init__.py").exists():
                current, index = nxt_dir, index + 1
            elif nxt_file.exists():
                current, index = nxt_file, index + 1
                break
            else:
                break
        module_file = current / "__init__.py" if current.is_dir() else current
        rest = parts[index:]
        tree = self._parse(module_file)
        if not rest:
            return True
        if tree is None:
            return False
        return _resolve_in_module(tree, rest, self._follow)

    def _follow(self, dotted: str) -> bool:
        return self.name_resolves(dotted)

    # flags
    def script_flags(self, relative: str) -> set[str] | None:
        path = self.root / relative
        if not path.is_file():
            return None
        if relative not in self._flags:
            tree = self._parse(path)
            self._flags[relative] = _flags_of(tree) if tree else set()
        return self._flags[relative]

    def all_flags(self) -> set[str]:
        if self._all_flags is None:
            flags = set()
            for path in list((self.root / "scripts").rglob("*.py")) + list((self.root / "packages").glob("*/*/*.py")):
                if "tests" in path.parts or "scripts/scjn" in path.as_posix():
                    continue
                tree = self._parse(path)
                if tree:
                    flags |= _flags_of(tree)
            self._all_flags = flags
        return self._all_flags


def _package_of(document: Path, root: Path) -> str | None:
    try:
        parts = document.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) > 1 and parts[0] == "packages":
        return parts[1]
    return None


def _exists_with_placeholders(base: Path, token: str) -> bool:
    if "<" in token or "{" in token or "*" in token or "N" == token:
        cut = re.split(r"[<{*]", token, maxsplit=1)[0]
        cut = cut.rsplit("/", 1)[0] if "/" in cut else ""
        if "*" in token and "<" not in token and "{" not in token:
            return bool(glob.glob(str(base / token)))
        return (base / cut).exists() if cut else False
    return (base / token).exists()


def _flags_of(tree: ast.Module) -> set[str]:
    flags = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                    flags.add(arg.value)
        # `argv` literals and `--flag` strings used by sbatch wrappers also count as "known".
    return flags


def _resolve_in_module(tree: ast.Module, rest: list[str], follow) -> bool:
    """`rest` inside `tree`; an imported name is followed into this repository's own packages (a
    re-export), and trusted when it comes from anywhere else."""
    head, tail = rest[0], rest[1:]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == head:
            return not tail
        if isinstance(node, ast.ClassDef) and node.name == head:
            return _resolve_in_class(node, tail)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == head for t in targets):
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == head:
                    source = node.module if isinstance(node, ast.ImportFrom) else None
                    if tail and source and source.split(".")[0] in PACKAGES and node.level == 0:
                        return follow(".".join([source, alias.name, *tail]))
                    return True
        if isinstance(node, (ast.If, ast.Try)):
            inner = ast.Module(body=list(node.body) + [n for h in getattr(node, "handlers", []) for n in h.body]
                               + list(getattr(node, "orelse", [])), type_ignores=[])
            if _resolve_in_module(inner, rest, follow):
                return True
    return False


def _resolve_in_class(node: ast.ClassDef, tail: list[str]) -> bool:
    if not tail:
        return True
    name = tail[0]
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return len(tail) == 1
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return True
    # Dataclass fields and attributes set in __init__.
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self" \
                and sub.attr == name:
            return True
    return False


# -- extraction ---------------------------------------------------------------------------


def _documents(root: Path) -> list[Path]:
    found = []
    for pattern in DOCUMENT_GLOBS:
        found.extend(sorted(root.glob(pattern)))
    return sorted(set(p for p in found if p.is_file()))


def _clean_path(token: str) -> str:
    """`packages/scjn[test]` -> `packages/scjn`; `docs/source/conf.py:21` -> `docs/source/conf.py`."""
    token = re.sub(r"\[[\w,]+\]$", "", token)
    return re.sub(r":[\d,\-]+$", "", token)


def _is_path_like(token: str, top_level: set[str]) -> bool:
    """A path in the repository: it starts with one of its top-level entries. Anything relative to
    another root (an `outdir/`, a work directory's `runs/`, an installed `pairs/`) cannot be resolved
    here and is left alone."""
    if any(c in token for c in ' ()"\'=:!|;') and not re.search(r":[\d,\-]+$", token):
        return False
    if token.startswith(("-", "http", "git@", "$", "@", "//", "/*", "~")) or "://" in token:
        return False
    token = _clean_path(token).lstrip("/")
    first = token.split("/", 1)[0]
    return "/" in token and (first in top_level or first.startswith("."))


def _is_scratch(token: str) -> bool:
    stripped = token.lstrip("./")
    return token.startswith(SCRATCH_PREFIXES) or stripped.startswith(SCRATCH_PREFIXES) \
        or "emb-run" in token or "scripts/scjn/" in token or token.endswith(("PUBLICAR.md", "MANIFEST.md"))


def _spans(line: str):
    for match in _SPAN.finditer(line):
        yield (match.group(1) or match.group(2)).strip()


def _tokens_of_span(span: str) -> list[str]:
    return [t.strip("(),;:'\"") for t in span.split()]


def check_document(path: Path, repo: Repository, report: Report) -> None:
    relative = path.relative_to(repo.root).as_posix()
    in_fence = False
    in_doctest = False
    pending = ""
    changelog_level = 0
    before = len(report.findings)
    lines = path.read_text(encoding="utf-8").split("\n")
    for number, raw in enumerate(lines, start=1):
        heading = _HEADING.match(raw) if not in_fence else None
        if heading:
            level = len(heading.group(1))
            if changelog_level and level <= changelog_level:
                changelog_level = 0
            if re.search(r"changelog", heading.group(2), re.I):
                changelog_level = level
        start = len(report.findings)
        for issue in _ISSUE.findall(raw):
            if number not in report.issues[issue]:
                report.issues[issue].append(f"{relative}:{number}")
        if path.suffix == ".rst" and raw.lstrip().startswith(">>>"):
            in_doctest = True
        elif in_doctest and not raw.strip():
            in_doctest = False
        if in_doctest:
            continue
        if _FENCE.match(raw):
            in_fence = not in_fence
            pending = ""
            continue
        if in_fence:
            # Join `\` continuations so a flag is read next to its script.
            line = pending + raw.strip()
            if line.endswith("\\"):
                pending = line[:-1] + " "
                continue
            pending = ""
            _check_command_line(line, path, repo, report, number)
            for finding in report.findings[start:]:
                finding.history = finding.history or bool(changelog_level)
            continue
        automodule = re.match(r"\s*\.\. (?:auto\w+|currentmodule|module)::\s*(\S+)", raw)
        if automodule:
            _check_name(automodule.group(1), path, repo, report, number)
        for span in _spans(raw):
            _check_span(span, path, repo, report, number)
        for finding in report.findings[start:]:
            # A sentence wraps: "... are deleted outright" may end a line before the name it says is gone.
            window = " ".join(lines[max(number - 2, 0):number + 1])
            finding.history = finding.history or bool(changelog_level) or bool(_GONE.search(window))


def _check_span(span: str, path: Path, repo: Repository, report: Report, number: int) -> None:
    tokens = _tokens_of_span(span)
    if not tokens:
        return
    if len(tokens) == 1:
        token = tokens[0]
        if _DOTTED.match(token):
            _check_name(_DOTTED.match(token).group(1), path, repo, report, number)
            return
        if _is_path_like(token, repo.top_level):
            _check_path(token, path, repo, report, number)
            return
        if token.endswith(".py") and token.startswith("test_"):
            report.checked["path"] += 1
            if token not in repo.basenames():
                report.findings.append(Finding(path.relative_to(repo.root).as_posix(), number, "path", token,
                                               "no file of that name"))
            return
    _check_command_line(span, path, repo, report, number)


def _check_command_line(line: str, path: Path, repo: Repository, report: Report, number: int) -> None:
    """Paths and flags of one command: a flag is checked against the script named in the line."""
    tokens = _tokens_of_span(line)
    script = None
    for token in tokens:
        if token.endswith(".py") and "/" in token and not _is_scratch(token):
            candidate = token.lstrip("./")
            if (repo.root / candidate).is_file():
                script = candidate
    consoles = {t for t in tokens if t in ("legalvec", "scjn", "nota2md", "dofjson")}
    for token in tokens:
        if _is_path_like(token, repo.top_level) and not _is_scratch(token):
            _check_path(token, path, repo, report, number)
    for flag in _FLAG.findall(line):
        report.checked["flag"] += 1
        if flag in EXTERNAL_FLAGS:
            continue
        known = repo.script_flags(script) if script else None
        if known is None and consoles:
            known = set()
            for pkg in consoles:
                for cli in (repo.root / "packages" / pkg / pkg).glob("cli*.py"):
                    known |= repo.script_flags(cli.relative_to(repo.root).as_posix()) or set()
        if known is not None and known:
            if flag not in known:
                owner = script or "/".join(sorted(consoles))
                report.findings.append(Finding(path.relative_to(repo.root).as_posix(), number, "flag", flag,
                                               f"not an argument of {owner}"))
        elif flag not in repo.all_flags():
            report.findings.append(Finding(path.relative_to(repo.root).as_posix(), number, "flag", flag,
                                           "no script of the repository defines it"))


def _check_path(token: str, path: Path, repo: Repository, report: Report, number: int) -> None:
    token = _clean_path(token)
    if _is_scratch(token):
        return
    report.checked["path"] += 1
    if not repo.path_exists(token, path):
        report.findings.append(Finding(path.relative_to(repo.root).as_posix(), number, "path", token,
                                       "does not exist"))


def _check_name(dotted: str, path: Path, repo: Repository, report: Report, number: int) -> None:
    if not _DOTTED.match(dotted):
        return
    report.checked["name"] += 1
    if not repo.name_resolves(dotted):
        report.findings.append(Finding(path.relative_to(repo.root).as_posix(), number, "name", dotted,
                                       "does not resolve in the package sources"))


# -- driver -------------------------------------------------------------------------------


def run(root: Path, documents: list[Path] | None = None) -> Report:
    repo = Repository(root)
    report = Report()
    for document in documents if documents is not None else _documents(root):
        check_document(document, repo, report)
    return report


def check_issues(report: Report) -> dict[str, str]:
    """`{issue: state}` from ``gh issue view`` (network); an unknown issue maps to ``missing``."""
    states = {}
    for number in sorted(report.issues, key=int):
        result = subprocess.run(["gh", "issue", "view", number, "--json", "state", "-q", ".state"],
                                capture_output=True, text=True)
        states[number] = result.stdout.strip() if result.returncode == 0 else "missing"
    return states


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", type=Path, metavar="PATH")
    parser.add_argument("--issues", action="store_true", help="check the #N numbers with `gh` (network)")
    parser.add_argument("--quiet", action="store_true", help="print only the totals")
    parser.add_argument("documents", nargs="*", type=Path, help="only these documents")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    documents = [d if d.is_absolute() else root / d for d in args.documents] or None
    report = run(root, documents)
    history = [f for f in report.findings if f.document in HISTORY_DOCS or f.history]
    failing = [f for f in report.findings if f.document not in HISTORY_DOCS and not f.history]
    if not args.quiet:
        for title, rows in (("UNRESOLVED", failing), ("HISTORY (reported, never failing)", history)):
            if rows:
                print(f"== {title}: {len(rows)}")
                for f in rows:
                    print(f"  {f.document}:{f.line}: {f.kind} `{f.text}` -- {f.detail}")
    states = check_issues(report) if args.issues else {}
    missing = [n for n, s in states.items() if s == "missing"]
    if missing:
        print(f"== issues GitHub does not know: {', '.join(missing)}")
    print(f"checked {dict(report.checked)}; {len(report.issues)} distinct #N cited; "
          f"{len(failing)} unresolved, {len(history)} in history documents")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "checked": report.checked,
            "unresolved": [vars(f) for f in failing],
            "history": [vars(f) for f in history],
            "issues": {k: v for k, v in report.issues.items()},
            "issue_states": states,
        }, indent=1), encoding="utf-8")
    return 1 if failing or missing else 0


if __name__ == "__main__":
    sys.exit(main())
