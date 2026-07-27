#!/usr/bin/env python3
"""Render the website and refresh the freeze cache that CI publishes from.

The `Publish website` workflow (.github/workflows/website.yml) runs `quarto
render` on a bare runner: quarto and nothing else — no Python, no microtc, no
downloaded DOF titles. It gets away with that because `execute: freeze: auto`
in _quarto.yml makes quarto reuse the execution results committed under
website/_freeze instead of running the notebooks itself.

Quarto decides a frozen result is still usable by comparing the md5 of the
source notebook against the `hash` recorded in that page's
_freeze/<page>/execute-results/html.json. When the two disagree it re-executes
the page — which on the runner means importing dofjson and microtc, which are
not installed, so the Pages deploy fails. Committing a changed notebook
without its refreshed freeze entry (or the other way round) is therefore
enough to break the publish, and nothing in CI catches it earlier.

So refreshing the cache is more than "run quarto render": the committed
notebook and the committed hash have to agree, and every figure the frozen
markdown points at has to be committed alongside them. This script renders
and then checks exactly that, so a cache that would fail in CI fails here
instead.

Usage, from anywhere in the repo:

    python website/scripts/render.py                    # deps, render, verify
    python website/scripts/render.py --verify-only      # check the cache only
    python website/scripts/render.py pages/titles.ipynb
    python website/scripts/render.py --force pages/titles.ipynb

quarto itself is not installed here: it comes from the devcontainer in
.devcontainer/ (the rocker-org quarto-cli feature), so run this inside that
container, or install quarto some other way first.

Re-rendering pages/titles.ipynb re-downloads every published DOF title
(~1.2 million notes) and refits the tf-idf models behind the word clouds, so
expect tens of minutes. The other three pages are quick. Both the downloaded
dataset and _site/ are gitignored; the artifacts to commit are the notebooks
whose stored outputs quarto rewrote plus website/_freeze, and the script
prints them at the end.

Naming pages on the command line renders only those, which is what you want
while iterating on one of them — but quarto skips the project's post-render
hook (scripts/fix_home_citations.py) for a single-page render, so _site/ is
not fully assembled that way. That only matters for eyeballing the output
locally: CI always renders the whole project. Do a full run before trusting
_site/.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEBSITE = REPO / "website"
FREEZE = WEBSITE / "_freeze"
WORKFLOW = REPO / ".github" / "workflows" / "website.yml"

# What the executable pages import, as {module: pip requirement}. The
# devcontainer's postCreateCommand only installs packages/dof2md, so the
# rendering dependencies are installed on demand here rather than assumed.
# `jupyter` is quarto's execution engine for .ipynb sources, not a notebook
# import.
PYPI_DEPS = {
    "jupyter": "jupyter",
    "IPython": "ipython",
    "matplotlib": "matplotlib",
    "microtc": "microtc",
    "pandas": "pandas",
    "plotly": "plotly",
    "requests": "requests",
    "wordcloud": "wordcloud",
}

# Monorepo packages the pages import, installed editable from the checkout so
# a page always renders against the working tree rather than a release.
LOCAL_DEPS = {
    "dofjson": "packages/dofjson",
    "nota2md": "packages/nota2md",
}


def log(message: str) -> None:
    print(f"[render] {message}", flush=True)


def pages() -> list[Path]:
    """The executable pages, as paths relative to website/."""
    found = [
        p.relative_to(WEBSITE)
        for p in WEBSITE.rglob("*.ipynb")
        if not any(part in {"_freeze", "_site", ".quarto"} for part in p.parts)
    ]
    return sorted(found)


def freeze_result(page: Path) -> Path:
    """Where quarto keeps `page`'s frozen execution result."""
    return FREEZE / page.with_suffix("") / "execute-results" / "html.json"


def quarto_version() -> str:
    """The quarto on PATH, or exit telling the caller where to get one."""
    try:
        out = subprocess.run(
            ["quarto", "--version"], capture_output=True, text=True, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        sys.exit(
            "[render] quarto not found on PATH. Open this repo in the devcontainer "
            "under .devcontainer/, which installs it, or install quarto separately."
        )
    return out.stdout.strip()


def ci_quarto_version() -> str | None:
    """The version website.yml pins for quarto-actions/setup, if any."""
    if not WORKFLOW.exists():
        return None
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"quarto-actions/setup@[^\n]*\n(?:\s*\n)*\s*with:[^\n]*\n\s*version:\s*"
        r"[\"']?([\d.]+)",
        text,
    )
    return match.group(1) if match else None


def ensure_dependencies() -> None:
    """Install whatever the pages import and is not importable yet."""
    missing_pypi = [
        req for module, req in PYPI_DEPS.items() if importlib.util.find_spec(module) is None
    ]
    missing_local = [
        path for module, path in LOCAL_DEPS.items() if importlib.util.find_spec(module) is None
    ]
    if not missing_pypi and not missing_local:
        log("rendering dependencies already installed")
        return

    if missing_pypi:
        log(f"installing {' '.join(missing_pypi)}")
        subprocess.run([sys.executable, "-m", "pip", "install", *missing_pypi], check=True)
    for path in missing_local:
        log(f"installing {path} (editable)")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(REPO / path)], check=True
        )


def render(targets: list[Path], force: bool) -> None:
    """Run quarto over `targets` (all pages when empty)."""
    if force:
        for page in targets or pages():
            stale = FREEZE / page.with_suffix("")
            if stale.exists():
                log(f"dropping frozen result for {page}")
                shutil.rmtree(stale)

    command = ["quarto", "render", *(str(t) for t in targets)]
    log(f"{' '.join(command)}  (cwd: {WEBSITE})")
    result = subprocess.run(command, cwd=WEBSITE)
    if result.returncode != 0:
        sys.exit(f"[render] quarto render failed with exit code {result.returncode}")


def verify() -> list[str]:
    """Check every page's frozen result the way CI's quarto will read it."""
    problems = []
    for page in pages():
        source = WEBSITE / page
        result_file = freeze_result(page)
        if not result_file.exists():
            problems.append(
                f"{page}: no frozen result at {result_file.relative_to(REPO)} — CI would "
                f"try to execute this page. Render it (this script, without --verify-only)."
            )
            continue

        frozen = json.loads(result_file.read_text(encoding="utf-8"))
        digest = hashlib.md5(source.read_bytes()).hexdigest()
        if digest != frozen["hash"]:
            problems.append(
                f"{page}: stale freeze — md5(source)={digest} but the frozen hash is "
                f"{frozen['hash']}. CI would re-execute this page and fail. Re-render it, "
                f"and do not edit the notebook afterwards without rendering again."
            )
            continue

        # Figures the frozen markdown points at live beside it under
        # _freeze/<page>/, one directory per name in `supporting`. A missing one
        # renders as a broken image on Pages, and CI has no way to notice.
        result = frozen["result"]
        markdown = result["markdown"]
        missing_assets = []
        for supporting in result.get("supporting", []):
            referenced = set(re.findall(rf"{re.escape(supporting)}/[\w./-]+", markdown))
            for reference in sorted(referenced):
                relative = Path(reference).relative_to(supporting)
                asset = FREEZE / page.with_suffix("") / relative
                if not asset.exists():
                    missing_assets.append(
                        f"{page}: frozen markdown references {reference} but "
                        f"{asset.relative_to(REPO)} is missing."
                    )

        if missing_assets:
            problems.extend(missing_assets)
        else:
            log(f"{page}: freeze OK (hash {digest[:12]})")

    # A frozen result only helps CI once it is committed: the runner checks the
    # repo out and renders from that, so anything still untracked here is
    # invisible there.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "website/_freeze"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.split()
    for path in untracked:
        problems.append(f"{path} is untracked — git add it, or CI will not see it.")
    return problems


def report_artifacts() -> None:
    """Show what the render touched, so the caller knows what to commit."""
    paths = ["website/_freeze", *(f"website/{page}" for page in pages())]
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not changed:
        log("nothing changed — the committed cache already matches the sources")
        return
    log("commit these together, so the notebooks and their freeze hashes stay in step:")
    for line in changed.splitlines():
        print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="pages to render, relative to website/ (default: the whole site)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="skip rendering; only check that the committed freeze cache is usable by CI",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="discard the frozen results of the selected pages so they re-execute",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="do not install missing Python dependencies before rendering",
    )
    args = parser.parse_args()

    targets = [Path(t) for t in args.targets]
    for target in targets:
        if not (WEBSITE / target).exists():
            sys.exit(f"[render] no such page: website/{target}")

    if not args.verify_only:
        local = quarto_version()
        pinned = ci_quarto_version()
        log(f"quarto {local} (CI pins {pinned or 'nothing'})")
        if pinned and local != pinned:
            log(
                f"note: CI renders with quarto {pinned}. The freeze hash is a plain md5 "
                f"of the source, so a cache written here is still valid there, but "
                f"pin the devcontainer's quarto-cli feature to {pinned} if the rendered "
                f"HTML ever diverges."
            )
        if not args.skip_deps:
            ensure_dependencies()
        render(targets, args.force)

    problems = verify()
    if problems:
        log(f"{len(problems)} problem(s) — CI would NOT publish this:")
        for problem in problems:
            print(f"    - {problem}")
        return 1

    log("every page's frozen result matches its source; CI can publish without Python")
    if not args.verify_only:
        report_artifacts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
