"""The website's Atlas page (issue #245), in a real browser.

    uv run --group viz playwright install chromium     # once
    uv run --group viz pytest scripts/embeddings/tests/test_atlas_page.py -q

The page is a hand-written D3 application (`website/pages/atlas/atlas.js`,
`atlas.css`) over the committed `website/pages/atlas/atlas.json` (issue #244),
wrapped by `website/pages/atlas.qmd`. Most browser tests load a harness
page: an HTML file served next to `website/pages/atlas/` whose body is the
qmd's own `<!-- atlas:app -->` block, copied verbatim — so what is clicked
here is the markup the site publishes, not a look-alike. It is fast and needs
no Quarto, but it carries only `atlas.css`, not the site's own stylesheet.

That is how issue #247 got published: Quarto's CSS sends every `aside` to the
page margin with `grid-column: body-end/page-end !important`, and the panel
was an `<aside>`, so on the real page the map's grid track was 0 px wide. The
`rendered_*` tests at the bottom therefore render `pages/atlas.qmd` with
Quarto into `website/_site/` (gitignored) and drive the page Quarto wrote.

Issue #250's explanation dialog is tested twice: over a toy site (the
six-instrument corpus of `test_instrument_matrix.py`, exported by
`export_atlas_data.py` and `export_atlas_pairs.py` into a temporary directory
laid out like `website/pages/`, with the real `atlas.js`/`atlas.css`), and,
when `website/pages/atlas/pairs/` has been installed, over the real
pair the Constitution's heaviest weight names.

The static checks at the top always run. The browser tests skip, with the
reason, when Playwright or its Chromium is missing; the rendered-page tests
also skip when no Quarto binary is found (on `PATH`, else the newest
`~/.local/opt/quarto-*/bin/quarto`).
"""

import functools
import http.server
import json
import re
import shutil
import statistics
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_instrument_matrix import (  # noqa: E402,F401
    cache, index_of, prepared, stub_umap, with_matrix)

REPO = Path(__file__).resolve().parents[3]
WEBSITE = REPO / "website"
PAGES = WEBSITE / "pages"
QMD = PAGES / "atlas.qmd"
APP_JS = PAGES / "atlas" / "atlas.js"
APP_CSS = PAGES / "atlas" / "atlas.css"
DATA = PAGES / "atlas" / "atlas.json"
DATA_4B = PAGES / "atlas" / "atlas-qwen3-4b.json"
DATA_BM25 = PAGES / "atlas" / "atlas-bm25.json"
SCREENSHOT = REPO / "output" / "atlas-chapingo.png"
SITE = WEBSITE / "_site"
PAIRS = PAGES / "atlas" / "pairs"
PAIRS_4B = PAGES / "atlas" / "pairs-qwen3-4b"
PAIRS_BM25 = PAGES / "atlas" / "pairs-bm25"
WORKFLOW = REPO / ".github" / "workflows" / "website.yml"
EXPLAIN_SCREENSHOT = REPO / "output" / "atlas-explain-cpeum.png"
NOT_AVAILABLE = "The explanation for this pair is not available."
OTHER_VERSION = "The explanation was built for a different version of the map."
RENDERED_SCREENSHOT = REPO / "output" / "atlas-rendered.png"
RENDERED_CHAPINGO_SCREENSHOT = REPO / "output" / "atlas-rendered-chapingo.png"
#: Quarto's margin text colour, `#636056`: what an `aside` on the site gets.
QUARTO_MARGIN_COLOR = "rgb(99, 96, 86)"

TITLE = "An Atlas of Mexican Federal Law: Laws, Regulations and Guidelines"
PALETTE = {"leyes": "#2a78d6", "reglamentos": "#008300", "lineamientos": "#e87ba4"}
CONSTITUTION = "CONSTITUCIÓN Política de los Estados Unidos Mexicanos"
CHAPINGO = "LEY que crea la Universidad Autónoma Chapingo"


def atlas_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def instrument(data: dict, clave: str) -> dict:
    return next(entry for entry in data["instruments"] if entry["k"] == clave)


def counts(data: dict) -> dict:
    """The numbers the page prints, read off `atlas.json` so the prose, the
    subtitle and the legend cannot drift from the data."""
    collections = data["meta"]["collections"]
    return {"leyes": collections["leyes"], "reglamentos": collections["reglamentos"],
            "lineamientos": collections["lineamientos"]}


def subtitle_of(data: dict) -> str:
    n = counts(data)
    return (f"{n['leyes']:,} laws, {n['reglamentos']:,} regulations and "
            f"{n['lineamientos']:,} guidelines, placed by where their "
            "excerpts' closest texts live")


def targets_of(data: dict, clave: str) -> list[tuple[str, str]]:
    """`(weight, name)` per closest instrument, as the panel prints them."""
    return [(f"{weight:.1f}", data["instruments"][j]["n"])
            for j, weight in instrument(data, clave)["out"]]


#: What the page must never say: the Python side's word for an excerpt, the
#: word it replaced (issue #271: a heading is not a provision, and a legal
#: provision is a DOF note), the HTML word for the element the page replaced,
#: the parameter's own name, and the #242 page's provenance footer. The one
#: allowed occurrence is a property access (`pair.provisions`): the pair files'
#: key, kept as published.
FORBIDDEN = [re.compile(r"\bunits?\b", re.I), re.compile("tooltip", re.I),
             re.compile(r"(?<!\.)\bprovisions?\b", re.I),
             re.compile("n_neighbors"), re.compile("Generated by"),
             re.compile("build_instrument_umap_html")]


def app_block(text: str) -> str:
    match = re.search(r"<!-- atlas:app -->.*?<!-- /atlas:app -->", text, re.S)
    assert match, "atlas.qmd has no <!-- atlas:app --> block"
    return match.group(0)


def front_matter(text: str) -> dict:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "atlas.qmd has no front matter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def forbidden_in(text: str) -> list[str]:
    return [pattern.pattern for pattern in FORBIDDEN if pattern.search(text)]


# -- static checks, always run -------------------------------------------- #

def test_the_qmd_has_the_agreed_front_matter_and_no_code():
    text = QMD.read_text(encoding="utf-8")
    fields = front_matter(text)
    assert fields["title"] == TITLE
    assert fields["subtitle"] == subtitle_of(atlas_data())
    assert fields["page-layout"] == "full"
    assert fields["toc"] == "false"
    assert fields["number-sections"] == "false"
    # No code cells: nothing to execute, so nothing to freeze.
    assert "```" not in text


def test_the_qmd_mounts_the_application_with_relative_paths():
    block = app_block(QMD.read_text(encoding="utf-8"))
    assert '<link rel="stylesheet" href="atlas/atlas.css">' in block
    assert ('<div id="atlas" class="atlas" data-src="atlas/atlas.json" '
            'data-pairs="atlas/pairs/" data-models=\'') in block
    assert '<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>' in block
    assert '<script src="atlas/atlas.js"></script>' in block


def mount_attributes() -> dict:
    """The attributes of the qmd's `<div id="atlas">`, parsed."""
    from html.parser import HTMLParser

    found = {}

    class Mount(HTMLParser):
        def handle_starttag(self, tag, attributes):
            if tag == "div" and dict(attributes).get("id") == "atlas":
                found.update(dict(attributes))

    Mount().feed(app_block(QMD.read_text(encoding="utf-8")))
    return found


def test_the_mount_lists_three_models_and_the_first_is_the_default():
    attributes = mount_attributes()
    models = json.loads(attributes["data-models"])
    assert models == [
        {"label": "0.6B", "src": "atlas/atlas.json", "pairs": "atlas/pairs/"},
        {"label": "4B", "src": "atlas/atlas-qwen3-4b.json", "pairs": "atlas/pairs-qwen3-4b/"},
        {"label": "BM25", "src": "atlas/atlas-bm25.json", "pairs": "atlas/pairs-bm25/"}]
    # The default model's paths stay where every existing mount and test looks.
    assert (models[0]["src"], models[0]["pairs"]) == (attributes["data-src"],
                                                      attributes["data-pairs"])
    # Relative, so the page works from `_site/pages/atlas.html` and a local server alike.
    assert not any(value.startswith(("/", "http")) for model in models
                   for value in (model["src"], model["pairs"]))
    assert all((PAGES / model["src"]).exists() for model in models[1:])


def test_the_qmd_explains_the_similarity_and_names_all_three_options():
    text = QMD.read_text(encoding="utf-8")
    assert "## The embedding model" not in text
    assert text.index("## The neighbourhood size") < text.index("## The similarity")
    section = text[text.index("## The similarity"):text.index("## Checking the map")]
    flat = " ".join(section.split())
    assert "0.6B" in flat and "4B" in flat and "BM25" in flat
    assert "Qwen3-Embedding-0.6B" in flat and "Qwen3-Embedding-4B" in flat
    assert "[@Zhang2025Qwen3Embedding]" in flat and "[@Robertson2009BM25]" in flat
    assert "lexical baseline" in flat and "words they share" in flat
    assert "the examples above describe" in flat           # the examples stay the 0.6B's
    assert "scores and shows one decimal" in flat
    assert "different method finds different closest texts" in flat
    assert "the layout is drawn again" in flat
    assert "selected instrument stays selected" in flat.replace("The selected", "selected")
    data_block = text[text.index("::: {.atlas-data}"):]
    assert "Qwen/Qwen3-Embedding-0.6B" in data_block and "Qwen/Qwen3-Embedding-4B" in data_block
    assert "BM25" in data_block
    assert forbidden_in(section) == []


def test_the_application_carries_the_model_control_and_its_messages():
    js = APP_JS.read_text(encoding="utf-8")
    for needle in ("mount.dataset.models", 'text: "Similarity"', "atlas-model-label",
                   "cannot be used", "could not be loaded", "function radiogroup",
                   '"Score"', "meta.method"):
        assert needle in js, needle
    assert "Embedding model" not in js
    assert js.count('role: "radiogroup"') == 1        # one helper builds both controls
    css = APP_CSS.read_text(encoding="utf-8")
    assert ".atlas-controls" in css and ".atlas-model" in css


def test_the_qmd_prose_names_the_method_once_and_nothing_forbidden():
    text = QMD.read_text(encoding="utf-8")
    assert forbidden_in(text) == []
    # UMAP is described, and cited, in the neighbourhood-size section (issue #275).
    size = text[text.index("## The neighbourhood size"):text.index("## The similarity")]
    assert "UMAP" in size and "[@McInnes2018UMAP]" in size
    assert "Uniform Manifold Approximation and Projection" in size
    similarity = text[text.index("## The similarity"):text.index("## Checking the map")]
    assert "[@Zhang2025Qwen3Embedding]" in similarity
    assert "Qwen/Qwen3-Embedding-0.6B" in text


def test_the_application_files_say_nothing_forbidden():
    for path in (APP_JS, APP_CSS):
        text = path.read_text(encoding="utf-8")
        assert forbidden_in(text) == [], path
        assert "UMAP" not in text, path
    # D3 v7, and no other library.
    assert "d3." in APP_JS.read_text(encoding="utf-8")
    for color in PALETTE.values():
        assert color in APP_JS.read_text(encoding="utf-8")


def test_the_navbar_puts_atlas_right_after_federal_laws_and_ships_its_files():
    text = (WEBSITE / "_quarto.yml").read_text(encoding="utf-8")
    entries = re.findall(r"- href: (\S+)\n\s+text: (.+)", text)
    names = [name.strip() for _, name in entries]
    assert names[:5] == ["Home", "Archive", "Federal Laws", "Atlas", "DOF Titles"]
    assert ("pages/atlas.qmd", "Atlas") in [(h, n.strip()) for h, n in entries]
    assert re.search(r'resources:\s*\n\s*-\s*"pages/atlas/\*\*"', text)


def test_the_publish_workflow_fetches_the_pair_explanations_before_publishing():
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.index("name: Fetch the Atlas pair explanations")
    assert step < text.index("quarto-dev/quarto-actions/publish")
    body = text[step:text.index("quarto-dev/quarto-actions/publish")]
    assert "gh release download atlas-pairs" in body
    assert "--pattern 'atlas-pairs.tar.gz'" in body and "--pattern 'SHA256SUMS.txt'" in body
    assert "sha256sum -c --ignore-missing SHA256SUMS.txt" in body
    assert "set -euo pipefail" in body                 # a missing asset fails the job
    assert "website/pages/atlas/pairs" in body
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in body
    assert "Hallazgo C" in text


def test_the_installed_pairs_are_never_committed():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/website/pages/atlas/pairs/" in lines
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "website/pages/atlas/pairs"],
                             capture_output=True, text=True, check=True).stdout
    assert tracked == ""


def test_the_publish_workflow_fetches_the_4b_pair_explanations_before_publishing():
    text = WORKFLOW.read_text(encoding="utf-8")
    publish = text.index("quarto-dev/quarto-actions/publish")
    step = text.index("name: Fetch the Atlas pair explanations of the 4B model")
    assert text.index("name: Fetch the Atlas pair explanations\n") < step < publish
    body = text[step:publish]
    assert "gh release download atlas-pairs" in body
    assert "--pattern 'atlas-pairs-qwen3-4b.tar.gz'" in body
    assert "--pattern 'SHA256SUMS-qwen3-4b.txt'" in body
    assert "sha256sum -c --ignore-missing SHA256SUMS-qwen3-4b.txt" in body
    assert "set -euo pipefail" in body                 # a missing asset fails the job
    assert "test -s" in body and 'test "$count" -gt 0' in body
    assert "website/pages/atlas/pairs-qwen3-4b" in body
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in body
    # The 0.6B step neither loses nor gains a byte of its own set.
    first = text[text.index("name: Fetch the Atlas pair explanations\n"):step]
    assert "qwen3-4b" not in first


def test_the_4b_pairs_are_never_committed():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/website/pages/atlas/pairs-qwen3-4b/" in lines
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files",
                              "website/pages/atlas/pairs-qwen3-4b"],
                             capture_output=True, text=True, check=True).stdout
    assert tracked == ""


def test_the_4b_data_lists_the_same_instruments_as_the_default():
    default, second = atlas_data(), json.loads(DATA_4B.read_text(encoding="utf-8"))
    assert second["meta"]["model"] == "Qwen/Qwen3-Embedding-4B"
    assert default["meta"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert second["meta"]["unique_names"] is True
    assert list(second["projections"]) == ["4", "8", "16", "32"]
    assert list(second) == list(default) and list(second["meta"]) == list(default["meta"])
    assert len(second["instruments"]) == len(default["instruments"]) == 1303
    for position, (mine, theirs) in enumerate(zip(second["instruments"],
                                                  default["instruments"])):
        assert all(mine[key] == theirs[key] for key in ("c", "k", "n", "p")), position
        assert list(mine) == list(theirs)
    for key in ("instruments", "provisions", "heading_rows_excluded", "distinct_texts",
                "collections", "duplicates_dropped", "n_neighbors"):
        assert second["meta"][key] == default["meta"][key], key
    for layout, points in second["projections"].items():
        assert len(points) == 1303 and layout in default["projections"]


@pytest.mark.skipif(not PAIRS_4B.is_dir(), reason="website/pages/atlas/pairs-qwen3-4b/ "
                    "is not installed (export_atlas_pairs.py --install)")
def test_the_installed_4b_pairs_match_the_4b_data():
    data = json.loads(DATA_4B.read_text(encoding="utf-8"))
    checked = 0
    for i, entry in enumerate(data["instruments"]):
        for j, _ in entry["out"]:
            document = json.loads((PAIRS_4B / f"{i}-{j}.json").read_text(encoding="utf-8"))
            assert document["source"]["k"] == entry["k"] == data["instruments"][i]["k"]
            assert document["target"]["k"] == data["instruments"][j]["k"]
            checked += 1
    assert checked == sum(len(entry["out"]) for entry in data["instruments"])
    assert len(list(PAIRS_4B.glob("*-*.json"))) == checked


def test_the_publish_workflow_fetches_the_bm25_pair_explanations_before_publishing():
    text = WORKFLOW.read_text(encoding="utf-8")
    publish = text.index("quarto-dev/quarto-actions/publish")
    step = text.index("name: Fetch the Atlas pair explanations of the BM25 set")
    assert text.index("name: Fetch the Atlas pair explanations of the 4B model") < step < publish
    body = text[step:publish]
    assert "gh release download atlas-pairs" in body
    assert "--pattern 'atlas-pairs-bm25.tar.gz'" in body
    assert "--pattern 'SHA256SUMS-bm25.txt'" in body
    assert "sha256sum -c --ignore-missing SHA256SUMS-bm25.txt" in body
    assert "set -euo pipefail" in body                 # a missing asset fails the job
    assert "test -s" in body and 'test "$count" -gt 0' in body
    assert "website/pages/atlas/pairs-bm25" in body
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in body
    # The other two sets neither lose nor gain a byte of their own.
    earlier = text[text.index("name: Fetch the Atlas pair explanations\n"):step]
    assert "bm25" not in earlier


def test_the_bm25_pairs_are_never_committed():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/website/pages/atlas/pairs-bm25/" in lines
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files",
                              "website/pages/atlas/pairs-bm25"],
                             capture_output=True, text=True, check=True).stdout
    assert tracked == ""


def test_the_bm25_data_lists_the_same_instruments_as_the_default():
    default, second = atlas_data(), json.loads(DATA_BM25.read_text(encoding="utf-8"))
    assert second["meta"]["model"] == "bm25" and second["meta"]["method"] == "bm25"
    assert second["meta"]["bm25"]["k1"] == 1.5 and second["meta"]["bm25"]["b"] == 0.75
    assert second["meta"]["unique_names"] is True
    assert list(second["projections"]) == ["4", "8", "16", "32"]
    assert list(second) == list(default)
    assert len(second["instruments"]) == len(default["instruments"]) == 1303
    for position, (mine, theirs) in enumerate(zip(second["instruments"],
                                                  default["instruments"])):
        assert all(mine[key] == theirs[key] for key in ("c", "k", "n", "p")), position
        assert list(mine) == list(theirs)
    for key in ("instruments", "provisions", "heading_rows_excluded",
                "transitorio_rows_excluded", "collections", "duplicates_dropped",
                "n_neighbors"):
        assert second["meta"][key] == default["meta"][key], key
    for layout, points in second["projections"].items():
        assert len(points) == 1303 and layout in default["projections"]


@pytest.mark.skipif(not PAIRS_BM25.is_dir(), reason="website/pages/atlas/pairs-bm25/ "
                    "is not installed (export_atlas_pairs.py --install)")
def test_the_installed_bm25_pairs_match_the_bm25_data():
    data = json.loads(DATA_BM25.read_text(encoding="utf-8"))
    checked = 0
    for i, entry in enumerate(data["instruments"]):
        for j, _ in entry["out"]:
            document = json.loads((PAIRS_BM25 / f"{i}-{j}.json").read_text(encoding="utf-8"))
            assert document["source"]["k"] == entry["k"] == data["instruments"][i]["k"]
            assert document["target"]["k"] == data["instruments"][j]["k"]
            checked += 1
    assert checked == sum(len(entry["out"]) for entry in data["instruments"])
    assert len(list(PAIRS_BM25.glob("*-*.json"))) == checked


def test_the_qmd_describes_the_explanation_and_its_example():
    text = QMD.read_text(encoding="utf-8")
    assert "opens a table of the excerpts behind that number" in " ".join(text.split())
    data = atlas_data()
    constitution = instrument(data, "cpeum")
    j, weight = constitution["out"][0]
    pair = json.loads((PAIRS / f"{data['instruments'].index(constitution)}-{j}.json")
                      .read_text(encoding="utf-8")) if PAIRS.is_dir() else None
    flat = " ".join(text.replace("*", "").split())
    assert data["instruments"][j]["n"] in flat
    assert f"{weight:.1f}" in flat
    meta = data["meta"]
    # Issue #264/#265, moved into the Data box by issue #275: headings and
    # transitory articles are not compared at all, and the counts are the data's.
    data_box = " ".join(text[text.index("::: {.atlas-data}"):].split())
    for number in ("provisions", "heading_rows_excluded", "transitorio_rows_excluded",
                   "identical_shared_dropped", "counted_rows"):
        assert f"{meta[number]:,}" in data_box, number
    cards = text[text.index("### The numbers"):text.index("Searching for an instrument")]
    assert "Two kinds of excerpt" not in text
    assert "161,989" not in cards and "unique" not in cards
    assert "only the most recently issued one is shown" in data_box
    if pair is not None:
        assert f"lists {pair['provisions']} excerpts" in flat
        # No transitory article is compared, so none is in the table.
        assert not [r for r in pair["rows"] if r["label"].startswith("Transitory")]
        assert all(r["m"] == 1 for r in pair["rows"]) and "every one a whole 1" in flat


def test_the_qmd_chapingo_example_matches_the_default_data():
    """The example describes the 0.6B map the page opens on; the 4B says 9 to the
    UAM, which once read as an error (issue #275)."""
    text = QMD.read_text(encoding="utf-8")
    example = text[text.index("## An example"):text.index("## The neighbourhood size")]
    flat = " ".join(example.replace("*", "").split())
    assert "drawn from the 0.6B model" in flat
    data = atlas_data()
    law = instrument(data, "luach")
    assert law["n"] == CHAPINGO
    assert f"has {law['p']} excerpts, {law['pc']} of which count" in flat
    first, second, last = law["out"]
    assert data["instruments"][last[0]]["n"] in flat and last[1] == 1
    assert (f"For {first[1]:.0f} of them the closest text outside the law belongs to the "
            f"{data['instruments'][first[0]]['n']}") in flat
    assert f"for {second[1]:.0f} to the {data['instruments'][second[0]]['n']}" in flat
    assert f"{law['in']:.0f} excerpts of other instruments" in flat
    (_, w1), (_, w2) = law["inc"][:2]
    assert f"{w1:.0f} of them in Antonio Narro's and {w2:.0f} in the UAM's" in flat
    # The 4B's reading of the same law differs, which is why the page says whose it is.
    four = instrument(json.loads(DATA_4B.read_text(encoding="utf-8")), "luach")
    assert four["out"][0][1] != law["out"][0][1]


def test_the_qmd_has_the_evaluation_section_with_its_numbers():
    text = QMD.read_text(encoding="utf-8")
    assert text.index("## The similarity") < text.index("## Checking the map against known links") \
        < text.index("::: {.atlas-data}")
    section = text[text.index("## Checking the map against known links"):
                   text.index("::: {.atlas-data}")]
    flat = " ".join(section.split())
    for heading in ("### How the strong links are built", "### What is measured",
                    "### The instruments", "### The excerpts", "### The reading"):
        assert heading in section, heading
    assert "The first, A," in flat and "The second, B," in flat and "signal, C," in flat
    for example in ("REGLAMENTO DE LA LEY FEDERAL DE CORREDURÍA PÚBLICA",
                    "REGLAMENTO DE LA LEY DE AGUAS NACIONALES",
                    "GRUPOS DE CONSUMIDORES", "REGLAMENTO DE LA LEY FEDERAL DE TURISMO",
                    "artículo 38 Bis de la Ley General del Equilibrio Ecológico"):
        assert example in flat, example
    tables = [block for block in section.split("\n\n") if block.startswith("|")]
    assert len(tables) == 2
    for table in tables:
        header = table.splitlines()[0]
        assert "0.6B" in header and "4B" in header and "BM25" in header
    for literal in ("132", "160", "212", "55", "3,647", "2,010", "2,003", "0.273",
                    "0.253", "0.241", "21", "29", "5 October 2026"):
        assert literal in flat, literal
    assert "without the Constitution" in tables[1]
    assert forbidden_in(section) == []


def test_the_bib_has_the_atlas_citations():
    bib = (WEBSITE / "references.bib").read_text(encoding="utf-8")
    for key in ("McInnes2018UMAP", "Zhang2025Qwen3Embedding", "Robertson2009BM25"):
        assert re.search(r"@\w+\{" + key + ",", bib), key
    # The references page lists only its own `nocite` keys.
    assert "McInnes2018UMAP" not in (PAGES / "references.qmd").read_text(encoding="utf-8")


def test_no_prose_sentence_is_a_lead_phrase_and_a_colon():
    """The page's register is flowing prose (issue #275): outside the application
    block, the tables and the code, no line has a short lead phrase ended by a colon
    that then carries the real sentence."""
    text = QMD.read_text(encoding="utf-8")
    prose = text.replace(app_block(text), "").split("\n---\n", 1)[1]
    flat = " ".join(line for line in prose.splitlines() if not line.startswith(("|", "---")))
    assert not re.findall(r"[a-z)] ?: [A-Za-z]", re.sub(r"https?://\S+", "", flat))


def test_the_application_carries_the_dialog_and_its_two_messages():
    js = APP_JS.read_text(encoding="utf-8")
    for needle in (NOT_AVAILABLE, OTHER_VERSION, "showModal", "mount.dataset.pairs",
                   "mount.dataset.pageSize", "atlas-why"):
        assert needle in js, needle
    assert ".atlas-explain::backdrop" in APP_CSS.read_text(encoding="utf-8")


def test_the_data_file_is_the_244_export():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert len(data["instruments"]) == data["meta"]["instruments"] == sum(
        data["meta"]["collections"].values())
    # Unique instruments only (issue #259): no `duplicates_dropped` key without
    # `unique_names`, and no name repeated inside a reissued collection.
    assert data["meta"]["unique_names"] is True
    assert set(data["meta"]["duplicates_dropped"]) <= {"reglamentos", "lineamientos"}
    names = [(e["c"], e["n"]) for e in data["instruments"] if e["c"] != "leyes"]
    assert len(names) == len(set(names))
    assert sorted(data["projections"], key=int) == ["4", "8", "16", "32"]


# -- the browser ------------------------------------------------------------- #

HARNESS = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atlas harness</title>
<style>body {{ margin: 0; padding: 0 16px; background: #fdfdfb; }}</style>
</head>
<body>
<main>
{app}
</main>
</body>
</html>
"""


def serve(directory):
    """Start a quiet HTTP server over `directory` on a free port."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """`website/pages` over HTTP (Chromium blocks `fetch` from `file://`),
    plus the harness at `/harness.html`, written into a temporary directory
    and served from there."""
    harness = tmp_path_factory.mktemp("atlas") / "harness.html"
    harness.write_text(HARNESS.format(app=app_block(QMD.read_text(encoding="utf-8"))),
                       encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/harness.html":
                body = harness.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(PAGES)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/harness.html"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    playwright = sync_api.sync_playwright().start()
    try:
        chromium = playwright.chromium.launch()
    except Exception as error:  # a missing browser or system library
        playwright.stop()
        pytest.skip(f"Chromium cannot launch here: {error}")
    yield chromium
    chromium.close()
    playwright.stop()


def open_page(browser, server, width=1280, height=900):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server)
    page.wait_for_selector('#atlas[data-ready="true"]', timeout=30000)
    assert errors == []
    return page


@pytest.fixture
def page(browser, server):
    page = open_page(browser, server)
    yield page
    page.close()


NEIGHBOURHOOD = '[role="radiogroup"][aria-labelledby="atlas-neighbourhood-label"]'
MODEL = '[role="radiogroup"][aria-labelledby="atlas-model-label"]'
FOUR_B_FILE = "atlas-qwen3-4b.json"
BM25_FILE = "atlas-bm25.json"


def search(page, query):
    page.fill("#atlas-search", query)
    page.wait_for_selector("#atlas-suggestions li")
    return page.locator("#atlas-suggestions li")


def select_by_search(page, query, name):
    search(page, query).filter(has_text=name).first.dispatch_event("mousedown")
    page.wait_for_function(
        "name => document.querySelector('.atlas-panel h2')?.textContent === name", arg=name)


def panel_title(page):
    return page.locator(".atlas-panel h2").inner_text()


def test_every_instrument_is_one_circle_in_the_sites_palette(page):
    circles = page.locator("circle.atlas-point")
    assert circles.count() == atlas_data()["meta"]["instruments"]
    fills = set(page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('fill'))"))
    assert fills == set(PALETTE.values())
    for clave, collection in (("cpeum", "leyes"), ("luach", "leyes")):
        assert page.get_attribute(f'circle[data-k="{clave}"]', "fill") == PALETTE[collection]


def test_circle_area_is_proportional_to_counted_excerpts(page):
    """Issue #271: `pc`, not `p`. Above the 2.5 px floor a radius squared is a
    constant times `pc`; the Constitution (137 counted of 1,328) is no longer
    one of the big circles."""
    data = atlas_data()
    radii = page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => Number(n.getAttribute('r')))")
    assert len(set(radii)) > 100
    assert min(radii) >= 2.5
    biggest = max(data["instruments"], key=lambda entry: entry["pc"])
    largest = float(page.get_attribute(f'circle[data-k="{biggest["k"]}"]', "r"))
    checked = 0
    for entry in data["instruments"]:
        r = float(page.get_attribute(f'circle[data-k="{entry["k"]}"]', "r"))
        expected = largest * (entry["pc"] / biggest["pc"]) ** 0.5
        if expected > 2.5:
            assert r == pytest.approx(expected, rel=1e-3), entry["k"]
            checked += 1
        else:
            assert r == 2.5, entry["k"]
    assert checked > 100
    constitution = instrument(data, "cpeum")
    assert float(page.get_attribute('circle[data-k="cpeum"]', "r")) == pytest.approx(
        max(2.5, largest * (constitution["pc"] / biggest["pc"]) ** 0.5), rel=1e-3)


def test_constitucion_finds_the_constitution_first_and_enter_selects_it(page):
    suggestions = search(page, "constitucion")
    assert CONSTITUTION in suggestions.first.inner_text()
    page.press("#atlas-search", "Enter")
    page.wait_for_function(
        "name => document.querySelector('.atlas-panel h2')?.textContent === name",
        arg=CONSTITUTION)
    cpeum = instrument(atlas_data(), "cpeum")
    panel = page.locator(".atlas-panel").inner_text()
    assert f"{cpeum['pc']:,} counted excerpts" in panel
    assert f"{cpeum['p']:,}" not in panel          # the total is never displayed
    # Brought into view: after the pan, its centre is inside the map.
    page.wait_for_timeout(900)
    box = page.locator("svg.atlas-map").bounding_box()
    circle = page.locator('circle[data-k="cpeum"]').bounding_box()
    centre = (circle["x"] + circle["width"] / 2, circle["y"] + circle["height"] / 2)
    assert box["x"] <= centre[0] <= box["x"] + box["width"]
    assert box["y"] <= centre[1] <= box["y"] + box["height"]
    assert page.locator("circle.atlas-ring").count() == 1


def test_search_folds_accents_and_case_and_reads_the_abbreviation(page):
    for query in ("CONSTITUCIÓN", "Constitución política", "cpeum"):
        assert CONSTITUTION in search(page, query).first.inner_text(), query


def test_chapingo_shows_its_closest_instruments_and_a_line_to_each(page):
    # Under the rule of issue #264 Chapingo points at three instruments, not five.
    count = len(targets_of(atlas_data(), "luach"))
    assert 0 < count <= 5
    select_by_search(page, "chapingo", CHAPINGO)
    rows = page.locator(".atlas-out .atlas-target")
    assert rows.count() == count
    weights = rows.locator(".atlas-weight").all_inner_texts()
    names = rows.locator(".atlas-target-name").all_inner_texts()
    assert list(zip(weights, names)) == targets_of(atlas_data(), "luach")
    assert page.locator(".atlas-inc .atlas-target").count() == 5
    panel = page.locator(".atlas-panel").inner_text()
    luach = instrument(atlas_data(), "luach")
    assert f"{luach['pc']} counted excerpts" in panel
    assert "abbreviation luach" in panel
    assert f"{round(luach['in'])} excerpts of other instruments" in panel
    assert page.locator("line.atlas-link").count() == count
    badges = page.locator("g.atlas-badge text").all_text_contents()
    assert sorted(badges) == [str(n) for n in range(1, count + 1)]
    # Nothing clipped: every row is fully rendered.
    for n in range(count):
        assert rows.nth(n).is_visible()


def test_the_constitution_shows_five_closest_instruments_and_five_lines(page):
    select_by_search(page, "cpeum", CONSTITUTION)
    rows = page.locator(".atlas-out .atlas-target")
    assert rows.count() == 5
    weights = rows.locator(".atlas-weight").all_inner_texts()
    names = rows.locator(".atlas-target-name").all_inner_texts()
    assert list(zip(weights, names)) == targets_of(atlas_data(), "cpeum")
    assert page.locator(".atlas-inc .atlas-target").count() == 5
    assert page.locator("line.atlas-link").count() == 5
    assert sorted(page.locator("g.atlas-badge text").all_text_contents()) == \
        ["1", "2", "3", "4", "5"]


def test_a_name_in_the_panel_selects_that_instrument(page):
    select_by_search(page, "chapingo", CHAPINGO)
    page.locator(".atlas-out .atlas-target-name").first.click()
    page.wait_for_function(
        "name => document.querySelector('.atlas-panel h2')?.textContent === name",
        arg=targets_of(atlas_data(), "luach")[0][1])
    assert page.locator("circle.atlas-ring").count() == 1


def test_a_regulation_is_named_by_its_scjn_id(page):
    page.locator('circle.atlas-point[fill="#008300"]').first.dispatch_event("click")
    assert "SCJN id" in page.locator(".atlas-panel").inner_text()
    assert page.locator(".atlas-panel .atlas-badge-label").inner_text().lower() == "regulation"


def test_the_neighbourhood_control_moves_the_points_and_keeps_the_selection(page):
    group = page.locator(NEIGHBOURHOOD)
    assert group.get_attribute("aria-labelledby") == "atlas-neighbourhood-label"
    assert page.locator("#atlas-neighbourhood-label").inner_text().lower() \
        == "neighbourhood size"
    buttons = group.locator('[role="radio"]')
    assert buttons.all_inner_texts() == ["4", "8", "16", "32"]
    assert group.locator('[aria-checked="true"]').inner_text() == "16"
    assert "The layout changes, the data does not." in page.locator(".atlas").inner_text()

    select_by_search(page, "chapingo", CHAPINGO)
    page.wait_for_timeout(700)
    before = page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('cx'))")
    group.locator('[data-value="8"]').click()
    assert group.locator('[data-value="8"]').get_attribute("aria-checked") == "true"
    assert group.locator('[data-value="16"]').get_attribute("aria-checked") == "false"
    page.wait_for_timeout(250)
    # Mid-transition the points are moving ...
    during = page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('cx'))")
    page.wait_for_timeout(900)
    after = page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('cx'))")
    assert during != before and during != after
    assert after != before
    # ... and the selection survives the new layout.
    assert panel_title(page) == CHAPINGO
    assert page.locator("circle.atlas-ring").count() == 1
    assert page.locator("line.atlas-link").count() == len(targets_of(atlas_data(), "luach"))


def test_a_collection_button_dims_the_other_two(page):
    law = 'circle[data-k="cpeum"]'
    regulation = 'circle.atlas-point[fill="#008300"]'
    assert page.get_attribute(law, "opacity") == "0.8"
    assert page.locator(regulation).first.get_attribute("opacity") == "0.8"
    laws = page.locator('.atlas-collection[data-c="leyes"]')
    n = counts(atlas_data())
    assert f"{n['leyes']:,} laws" in laws.inner_text()
    assert f"{n['reglamentos']:,} regulations" in page.locator(".atlas-collections").inner_text()
    assert f"{n['lineamientos']:,} guidelines" in page.locator(".atlas-collections").inner_text()
    laws.click()
    assert laws.get_attribute("aria-pressed") == "true"
    assert page.get_attribute(law, "opacity") == "0.8"
    assert float(page.locator(regulation).first.get_attribute("opacity")) < 0.2
    laws.click()
    assert laws.get_attribute("aria-pressed") == "false"
    assert page.locator(regulation).first.get_attribute("opacity") == "0.8"


def test_escape_and_an_empty_click_clear_the_selection(page):
    select_by_search(page, "chapingo", CHAPINGO)
    page.locator("svg.atlas-map").click(position={"x": 3, "y": 3})
    assert page.locator(".atlas-panel h2").count() == 0
    assert "Search for an instrument or click a point" in \
        page.locator(".atlas-panel").inner_text()
    select_by_search(page, "chapingo", CHAPINGO)
    page.locator("svg.atlas-map").hover(position={"x": 3, "y": 3})
    page.keyboard.press("Escape")   # the first Escape closes nothing: the list is shut
    page.locator("body").press("Escape")
    assert page.locator("circle.atlas-ring").count() == 0


def test_hover_shows_a_name_label_the_page_owns(page):
    page.locator('circle[data-k="cpeum"]').hover(force=True)
    label = page.locator(".atlas-label")
    assert label.is_visible()
    assert label.inner_text() == CONSTITUTION


def test_the_rendered_page_says_nothing_forbidden(page):
    select_by_search(page, "chapingo", CHAPINGO)
    html = page.content()
    assert forbidden_in(html) == []
    commit = json.loads(DATA.read_text(encoding="utf-8"))["meta"]["commit"]
    assert commit not in html
    assert "UMAP" not in html


@pytest.mark.parametrize("width", [1280, 390])
def test_the_page_fits_the_width_and_stacks_on_a_phone(browser, server, width):
    page = open_page(browser, server, width=width, height=844 if width < 900 else 900)
    try:
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        select_by_search(page, "chapingo", CHAPINGO)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        mapbox = page.locator(".atlas-map-wrap").bounding_box()
        panel = page.locator(".atlas-panel").bounding_box()
        if width < 900:
            assert panel["y"] >= mapbox["y"] + mapbox["height"]      # stacked
            assert panel["width"] <= width
        else:
            assert panel["x"] >= mapbox["x"] + mapbox["width"]       # side by side
            assert 300 <= panel["width"] <= 380
    finally:
        page.close()


# -- the embedding model control (issue #262) -------------------------------- #

def data_4b() -> dict:
    return json.loads(DATA_4B.read_text(encoding="utf-8"))


def data_bm25() -> dict:
    return json.loads(DATA_BM25.read_text(encoding="utf-8"))


def open_counting(browser, server, width=1280, height=900, before=None):
    """`open_page`, with every request recorded from the first one. `before`
    may register routes on the page before it navigates."""
    page = browser.new_page(viewport={"width": width, "height": height})
    requests, errors = [], []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    if before is not None:
        before(page)
    page.goto(server)
    page.wait_for_selector('#atlas[data-ready="true"]', timeout=30000)
    assert errors == []
    return page, requests, errors


def model_radio(page, label):
    return page.locator(MODEL).locator('[role="radio"]').filter(has_text=label)


def choose_model(page, label, checked=True):
    model_radio(page, label).click()
    if checked:
        wait_for_model(page, label)


def wait_for_model(page, label):
    page.wait_for_function(
        """label => [...document.querySelectorAll(
             '[aria-labelledby="atlas-model-label"] [role="radio"]')]
             .find(n => n.textContent === label)?.getAttribute('aria-checked') === 'true'""",
        arg=label)
    page.wait_for_timeout(900)                     # the 700 ms transition


def checked_model(page):
    return page.locator(MODEL).locator('[aria-checked="true"]').inner_text()


def out_list(page):
    return list(zip(page.locator(".atlas-out .atlas-weight").all_inner_texts(),
                    page.locator(".atlas-out .atlas-target-name").all_inner_texts()))


def inc_list(page):
    return list(zip(page.locator(".atlas-inc .atlas-weight").all_inner_texts(),
                    page.locator(".atlas-inc .atlas-target-name").all_inner_texts()))


def circle_xs(page):
    """Every circle's `cx`, by instrument position."""
    return page.evaluate(
        "() => { const xs = []; document.querySelectorAll('circle.atlas-point')"
        ".forEach(n => { xs[Number(n.dataset.i)] = Number(n.getAttribute('cx')); }); return xs; }")


def layout_fit(page, data, layout):
    """How well the circles' x positions follow `data`'s `layout` (Pearson r);
    1.0 when they are that layout, whatever the scale."""
    import statistics
    return statistics.correlation(circle_xs(page),
                                  [point[0] for point in data["projections"][str(layout)]])


def test_the_model_control_starts_on_the_default_and_fetches_only_that_file(browser, server):
    page, requests, _ = open_counting(browser, server)
    try:
        group = page.locator(MODEL)
        assert page.locator("#atlas-model-label").inner_text().lower() == "similarity"
        assert group.locator('[role="radio"]').all_inner_texts() == ["0.6B", "4B", "BM25"]
        assert checked_model(page) == "0.6B"
        assert group.locator('[role="radio"]').evaluate_all(
            "nodes => nodes.map(n => n.tabIndex)") == [0, -1, -1]
        assert page.locator(".atlas-neighbourhood, .atlas-model").count() == 2
        text = page.locator(".atlas").inner_text()
        assert "The method that measured how similar two texts are; the 0.6B is the default." in text
        assert "embedding model" not in text.lower()
        assert "the neighbourhood size changes the layout only" in text
        assert [url.rsplit("/", 1)[1] for url in requests if url.endswith(".json")] \
            == ["atlas.json"]
        assert page.locator(".atlas-model-status").is_hidden()
    finally:
        page.close()


def test_the_4b_replaces_the_relations_and_the_layout_and_keeps_the_selection(browser, server):
    default, other = atlas_data(), data_4b()
    page, requests, errors = open_counting(browser, server)
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        page.wait_for_timeout(900)
        assert out_list(page) == targets_of(default, "luach")
        assert inc_list(page) == [(f"{w:.1f}", default["instruments"][j]["n"])
                                  for j, w in instrument(default, "luach")["inc"]]
        before = circle_xs(page)
        assert layout_fit(page, default, 16) > 0.999999

        choose_model(page, "4B")
        assert checked_model(page) == "4B"
        assert len([url for url in requests if url.endswith(FOUR_B_FILE)]) == 1
        assert panel_title(page) == CHAPINGO                       # the selection stays
        assert page.locator("circle.atlas-ring").count() == 1
        assert page.locator("line.atlas-link").count() == len(targets_of(other, "luach"))
        assert out_list(page) == targets_of(other, "luach")
        assert out_list(page) != targets_of(default, "luach")
        assert inc_list(page) == [(f"{w:.1f}", other["instruments"][j]["n"])
                                  for j, w in instrument(other, "luach")["inc"]]
        assert circle_xs(page) != before
        assert layout_fit(page, other, 16) > 0.999999
        assert layout_fit(page, default, 16) < 0.999999
        # The numbered lines end at the 4B's five targets.
        ranks = page.eval_on_selector_all(
            "g.atlas-badge text", "nodes => nodes.map(n => n.textContent)")
        assert sorted(ranks) == ["1", "2", "3", "4", "5"]

        choose_model(page, "0.6B")
        assert checked_model(page) == "0.6B"
        assert panel_title(page) == CHAPINGO
        assert out_list(page) == targets_of(default, "luach")
        assert circle_xs(page) == before
        choose_model(page, "4B")
        assert out_list(page) == targets_of(other, "luach")
        # The 4B file was fetched once, whatever the number of switches.
        assert len([url for url in requests if url.endswith(FOUR_B_FILE)]) == 1
        assert errors == []
    finally:
        page.close()


def test_the_model_control_moves_with_the_arrow_keys(browser, server):
    page, requests, _ = open_counting(browser, server)
    try:
        page.locator(MODEL).locator('[data-value="0"]').focus()
        page.keyboard.press("ArrowRight")
        wait_for_model(page, "4B")
        assert page.evaluate("() => document.activeElement.textContent") == "4B"
        assert page.locator(MODEL).locator('[role="radio"]').evaluate_all(
            "nodes => nodes.map(n => n.tabIndex)") == [-1, 0, -1]
        page.keyboard.press("ArrowRight")
        wait_for_model(page, "BM25")
        assert page.evaluate("() => document.activeElement.textContent") == "BM25"
        page.keyboard.press("ArrowLeft")
        wait_for_model(page, "4B")
        page.keyboard.press("ArrowLeft")
        wait_for_model(page, "0.6B")
        assert page.evaluate("() => document.activeElement.textContent") == "0.6B"
        # The neighbourhood control's own keys are untouched.
        page.locator(NEIGHBOURHOOD).locator('[data-value="16"]').focus()
        page.keyboard.press("ArrowRight")
        assert page.locator(NEIGHBOURHOOD).locator('[aria-checked="true"]').inner_text() == "32"
    finally:
        page.close()


def test_the_neighbourhood_size_on_the_4b_moves_to_the_4b_layout(browser, server):
    default, other = atlas_data(), data_4b()
    page, _, _ = open_counting(browser, server)
    try:
        choose_model(page, "4B")
        page.locator(NEIGHBOURHOOD).locator('[data-value="8"]').click()
        page.wait_for_timeout(900)
        assert layout_fit(page, other, 8) > 0.999999
        assert layout_fit(page, default, 8) < 0.999999
        # ... and the size chosen there carries back to the default model.
        choose_model(page, "0.6B")
        assert layout_fit(page, default, 8) > 0.999999
        assert page.locator(NEIGHBOURHOOD).locator('[aria-checked="true"]').inner_text() == "8"
    finally:
        page.close()


def serve_4b(page, edit=None, status=200):
    """Answer the 4B file from disk (optionally edited), or with `status`."""
    def handler(route):
        if status != 200:
            route.fulfill(status=status, body="broken")
            return
        data = data_4b()
        if edit:
            edit(data)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
    page.route(f"**/{FOUR_B_FILE}", handler)


def swap_two_keys(data):
    a, b = data["instruments"][3], data["instruments"][4]
    a["k"], b["k"] = b["k"], a["k"]


def drop_the_layout(data):
    del data["projections"]["16"]


@pytest.mark.parametrize("edit,reason", [(swap_two_keys, "lists different instruments"),
                                         (drop_the_layout, "has no such layout")])
def test_a_4b_file_that_does_not_fit_is_refused_and_the_page_stays_on_the_default(
        browser, server, edit, reason):
    default = atlas_data()
    page, requests, errors = open_counting(browser, server, before=lambda p: serve_4b(p, edit))
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        page.wait_for_timeout(900)
        before = circle_xs(page)
        choose_model(page, "4B", checked=False)
        status = page.locator(".atlas-model-status")
        status.wait_for(state="visible")
        page.wait_for_function(
            "() => document.querySelector('.atlas-model-status').textContent !== 'Loading the 4B map\u2026'")
        assert status.inner_text() == f"The 4B map cannot be used: it {reason}."
        assert checked_model(page) == "0.6B"
        assert out_list(page) == targets_of(default, "luach")
        assert panel_title(page) == CHAPINGO
        page.wait_for_timeout(900)
        assert circle_xs(page) == before
        # Choosing the default again clears the message.
        choose_model(page, "0.6B", checked=False)
        assert page.locator(".atlas-model-status").is_hidden()
        assert errors == []
    finally:
        page.close()


def test_a_failed_4b_fetch_says_so_and_can_be_retried(browser, server):
    other = data_4b()
    page, requests, errors = open_counting(browser, server,
                                           before=lambda p: serve_4b(p, status=500))
    try:
        choose_model(page, "4B", checked=False)
        status = page.locator(".atlas-model-status")
        page.wait_for_function(
            "() => /could not be loaded/.test(document.querySelector('.atlas-model-status').textContent)")
        assert "Choose it again to retry" in status.inner_text()
        assert checked_model(page) == "0.6B"
        page.unroute(f"**/{FOUR_B_FILE}")
        choose_model(page, "4B")
        assert checked_model(page) == "4B"
        assert page.locator(".atlas-model-status").is_hidden()
        select_by_search(page, "chapingo", CHAPINGO)
        assert out_list(page) == targets_of(other, "luach")
        assert errors == []
    finally:
        page.close()


def test_the_dialog_reads_the_pairs_of_the_model_on_screen(browser, server):
    if not (PAIRS.is_dir() and PAIRS_4B.is_dir()):
        pytest.skip(f"{PAIRS} and {PAIRS_4B} are not both installed: run "
                    "export_atlas_pairs.py --install for each work directory")
    default, other = atlas_data(), data_4b()
    page, requests, _ = open_counting(browser, server)
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        i = default["instruments"].index(instrument(default, "luach"))
        for label, data, directory in (("0.6B", default, "/atlas/pairs/"),
                                       ("4B", other, "/atlas/pairs-qwen3-4b/"),
                                       ("0.6B", default, None)):
            choose_model(page, label)
            j, weight = instrument(data, "luach")["out"][0]
            page.locator(".atlas-out .atlas-why").first.click()
            page.wait_for_selector(".atlas-explain-table tbody tr")
            pair = json.loads(((PAIRS if label == "0.6B" else PAIRS_4B)
                               / f"{i}-{j}.json").read_text(encoding="utf-8"))
            lead = page.locator(".atlas-explain-lead").inner_text()
            assert lead.startswith(f"{pair['provisions']} excerpts of ")
            assert f"they add up to {weight:.1f} of its" in lead
            assert lead.endswith(f"{data['instruments'][i]['pc']:,} counted excerpts.")
            assert weights_in_table(page) == pytest.approx(weight, abs=0.05)
            if directory is not None:
                assert requests[-1].endswith(f"{directory}{i}-{j}.json"), (label, requests[-1])
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
        # One file per model and pair: back on the 0.6B the pair is cached.
        assert len([url for url in requests if "/atlas/pairs" in url]) == 2
    finally:
        page.close()


# -- BM25 on the same control (issue #268) ------------------------------------ #

def test_bm25_replaces_the_relations_and_the_layout_and_keeps_the_selection(browser, server):
    default, lexical = atlas_data(), data_bm25()
    page, requests, errors = open_counting(browser, server)
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        page.wait_for_timeout(900)
        assert out_list(page) == targets_of(default, "luach")
        before = circle_xs(page)
        page.locator(NEIGHBOURHOOD).locator('[data-value="8"]').click()
        page.wait_for_timeout(900)

        choose_model(page, "BM25")
        assert checked_model(page) == "BM25"
        assert len([url for url in requests if url.endswith(BM25_FILE)]) == 1
        assert not [url for url in requests if url.endswith(FOUR_B_FILE)]
        assert panel_title(page) == CHAPINGO                       # the selection stays
        assert page.locator("circle.atlas-ring").count() == 1
        assert out_list(page) == targets_of(lexical, "luach")
        assert inc_list(page) == [(f"{w:.1f}", lexical["instruments"][j]["n"])
                                  for j, w in instrument(lexical, "luach")["inc"]]
        # The neighbourhood size stays at 8, now drawn from the BM25 layout.
        assert page.locator(NEIGHBOURHOOD).locator('[aria-checked="true"]').inner_text() == "8"
        assert layout_fit(page, lexical, 8) > 0.999999
        assert layout_fit(page, default, 8) < 0.999999

        choose_model(page, "0.6B")
        assert out_list(page) == targets_of(default, "luach")
        assert layout_fit(page, default, 8) > 0.999999
        choose_model(page, "BM25")
        assert out_list(page) == targets_of(lexical, "luach")
        assert len([url for url in requests if url.endswith(BM25_FILE)]) == 1
        assert errors == []
    finally:
        page.close()


def serve_bm25(page, edit=None, status=200):
    """Answer the BM25 file from disk (optionally edited), or with `status`."""
    def handler(route):
        if status != 200:
            route.fulfill(status=status, body="broken")
            return
        data = data_bm25()
        if edit:
            edit(data)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
    page.route(f"**/{BM25_FILE}", handler)


@pytest.mark.parametrize("edit,reason", [(swap_two_keys, "lists different instruments"),
                                         (drop_the_layout, "has no such layout")])
def test_a_bm25_file_that_does_not_fit_is_refused_and_the_page_stays_put(
        browser, server, edit, reason):
    default = atlas_data()
    page, requests, errors = open_counting(browser, server, before=lambda p: serve_bm25(p, edit))
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        page.wait_for_timeout(900)
        before = circle_xs(page)
        choose_model(page, "BM25", checked=False)
        status = page.locator(".atlas-model-status")
        status.wait_for(state="visible")
        page.wait_for_function(
            "() => document.querySelector('.atlas-model-status').textContent !== 'Loading the BM25 map\u2026'")
        assert status.inner_text() == f"The BM25 map cannot be used: it {reason}."
        assert checked_model(page) == "0.6B"
        assert out_list(page) == targets_of(default, "luach")
        page.wait_for_timeout(900)
        assert circle_xs(page) == before
        assert errors == []
    finally:
        page.close()


def test_a_failed_bm25_fetch_says_so_and_can_be_retried(browser, server):
    lexical = data_bm25()
    page, requests, errors = open_counting(browser, server,
                                           before=lambda p: serve_bm25(p, status=500))
    try:
        choose_model(page, "BM25", checked=False)
        page.wait_for_function(
            "() => /could not be loaded/.test(document.querySelector('.atlas-model-status').textContent)")
        assert page.locator(".atlas-model-status").inner_text() \
            == "The BM25 map could not be loaded. Choose it again to retry."
        assert checked_model(page) == "0.6B"
        page.unroute(f"**/{BM25_FILE}")
        choose_model(page, "BM25")
        assert checked_model(page) == "BM25"
        select_by_search(page, "chapingo", CHAPINGO)
        assert out_list(page) == targets_of(lexical, "luach")
        assert errors == []
    finally:
        page.close()


def test_the_dialog_reads_the_bm25_pairs_and_labels_a_score(browser, server):
    if not (PAIRS.is_dir() and PAIRS_BM25.is_dir()):
        pytest.skip(f"{PAIRS} and {PAIRS_BM25} are not both installed: run "
                    "export_atlas_pairs.py --install for each work directory")
    default, lexical = atlas_data(), data_bm25()
    page, requests, _ = open_counting(browser, server)
    try:
        select_by_search(page, "chapingo", CHAPINGO)
        i = default["instruments"].index(instrument(default, "luach"))
        for label, data, directory, header, decimals in (
                ("0.6B", default, "/atlas/pairs/", "Similarity", 3),
                ("BM25", lexical, "/atlas/pairs-bm25/", "Score", 1),
                ("0.6B", default, None, "Similarity", 3)):
            choose_model(page, label)
            j, weight = instrument(data, "luach")["out"][0]
            page.locator(".atlas-out .atlas-why").first.click()
            page.wait_for_selector(".atlas-explain-table tbody tr")
            assert page.locator("th.atlas-explain-similarity").text_content() == header
            cells = page.locator("td.atlas-explain-similarity")
            assert cells.first.get_attribute("data-column") == header
            first = cells.first.inner_text()
            assert len(first.split(".")[1]) == decimals, (label, first)
            assert weights_in_table(page) == pytest.approx(weight, abs=0.05)
            if directory is not None:
                assert requests[-1].endswith(f"{directory}{i}-{j}.json"), (label, requests[-1])
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
    finally:
        page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_the_two_controls_sit_side_by_side_on_a_desktop_and_stack_on_a_phone(
        browser, server, width):
    page, _, _ = open_counting(browser, server, width=width, height=844 if width < 900 else 900)
    try:
        neighbourhood = page.locator(".atlas-neighbourhood").bounding_box()
        model = page.locator(".atlas-model").bounding_box()
        search_box = page.locator(".atlas-search").bounding_box()
        if width >= 900:
            assert abs(neighbourhood["y"] - model["y"]) < 4          # side by side
            assert neighbourhood["x"] + neighbourhood["width"] <= model["x"] + 1
        else:
            assert model["y"] >= neighbourhood["y"] + neighbourhood["height"] - 1     # stacked
        for box in (neighbourhood, model):
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
            assert box["y"] >= search_box["y"] + search_box["height"] - 1 \
                or box["x"] >= search_box["x"] + search_box["width"] - 1     # not over the search box
        choose_model(page, "4B")
        assert page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    finally:
        page.close()


def test_the_page_with_the_model_control_says_nothing_forbidden(browser, server):
    page, _, _ = open_counting(browser, server)
    try:
        choose_model(page, "4B")
        select_by_search(page, "chapingo", CHAPINGO)
        assert forbidden_in(page.content()) == []
    finally:
        page.close()


def test_a_screenshot_with_chapingo_selected(page):
    select_by_search(page, "chapingo", CHAPINGO)
    page.wait_for_timeout(900)
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert SCREENSHOT.stat().st_size > 10_000


# -- the page Quarto renders (issue #247) ------------------------------------ #

def find_quarto():
    """Quarto on `PATH`, else the newest one installed under `~/.local/opt`."""
    found = shutil.which("quarto")
    if found:
        return found
    installed = sorted(Path.home().glob(".local/opt/quarto-*/bin/quarto"),
                       key=lambda path: [int(part) if part.isdigit() else part
                                         for part in re.split(r"[.-]", path.parts[-3])])
    return str(installed[-1]) if installed else None


@pytest.fixture(scope="module")
def rendered_site():
    """`pages/atlas.qmd` rendered by Quarto into `website/_site/`, served over
    HTTP; the URL of the rendered Atlas page."""
    quarto = find_quarto()
    if quarto is None:
        pytest.skip("Quarto not found: put it on PATH or install it under "
                    "~/.local/opt/quarto-<version>/bin/quarto (CI uses 1.9.38)")
    subprocess.run([quarto, "render", "pages/atlas.qmd"], cwd=WEBSITE, check=True,
                   capture_output=True, timeout=300)
    assert (SITE / "pages" / "atlas.html").exists()
    assert (SITE / "pages" / "atlas" / "atlas.js").read_text(encoding="utf-8") \
        == APP_JS.read_text(encoding="utf-8")
    httpd = serve(SITE)
    yield f"http://127.0.0.1:{httpd.server_address[1]}/pages/atlas.html"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def rendered_page(browser, rendered_site):
    page = open_page(browser, rendered_site)
    yield page
    page.close()


def rendered_svg_box(page):
    return page.locator("svg.atlas-map").bounding_box()


def visible_circles_inside(page, box):
    """Circles with a non-zero box whose centre lies inside `box` (both in
    viewport coordinates, as Playwright's `bounding_box` reports them)."""
    return page.eval_on_selector_all(
        "circle.atlas-point",
        """(nodes, box) => nodes.filter(node => {
             const r = node.getBoundingClientRect();
             const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
             return r.width > 0 && r.height > 0
               && cx >= box.x && cx <= box.x + box.width
               && cy >= box.y && cy <= box.y + box.height;
           }).length""",
        box)


def test_rendered_map_has_width_and_visible_points(rendered_page):
    box = rendered_svg_box(rendered_page)
    assert box["width"] > 600
    assert visible_circles_inside(rendered_page, box) >= 0.9 * atlas_data()["meta"]["instruments"]
    tracks = rendered_page.evaluate(
        "() => getComputedStyle(document.querySelector('.atlas-main'))"
        ".gridTemplateColumns")
    widths = [float(track.removesuffix("px")) for track in tracks.split()]
    assert len(widths) == 2, tracks
    assert all(width > 0 for width in widths), tracks
    RENDERED_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    rendered_page.screenshot(path=str(RENDERED_SCREENSHOT), full_page=True)
    assert RENDERED_SCREENSHOT.stat().st_size > 10_000


def test_rendered_panel_is_not_in_quartos_margin(rendered_page):
    assert rendered_page.locator("#atlas aside").count() == 0
    panel = rendered_page.locator(".atlas-panel")
    assert panel.get_attribute("role") == "complementary"
    style = panel.evaluate(
        "node => { const s = getComputedStyle(node);"
        " return {column: s.gridColumnStart + ' / ' + s.gridColumnEnd, color: s.color}; }")
    assert style["column"] == "auto / auto"
    assert style["color"] != QUARTO_MARGIN_COLOR
    assert style["color"] == "rgb(31, 30, 27)"            # --atlas-ink
    # Quarto's heading rule and serif font do not reach the panel's headings.
    select_by_search(rendered_page, "chapingo", CHAPINGO)
    assert rendered_page.eval_on_selector(
        ".atlas-panel h2", "n => getComputedStyle(n).borderBottomWidth") == "0px"
    assert "sans-serif" in rendered_page.eval_on_selector(
        ".atlas-panel h3", "n => getComputedStyle(n).fontFamily")


def test_rendered_prose_is_as_wide_as_the_map_and_lists_its_references(rendered_page):
    box = lambda selector: rendered_page.locator(selector).bounding_box()
    assert abs(box(".atlas-prose")["width"] - box("#atlas")["width"]) <= 2
    fronts = rendered_page.eval_on_selector_all(
        ".atlas-prose .front", "nodes => nodes.map(n => n.getBoundingClientRect().y)")
    assert len(fronts) == 3 and max(fronts) - min(fronts) < 2     # side by side
    html = rendered_page.content()
    assert 'id="ref-McInnes2018UMAP"' in html and 'id="ref-Zhang2025Qwen3Embedding"' in html
    assert 'id="ref-Robertson2009BM25"' in html
    assert "[@" not in html                      # every citation key resolved
    assert rendered_page.locator("#refs").count() == 1
    heading = rendered_page.locator("#references > h2").bounding_box()
    assert heading["y"] > box(".atlas-prose")["y"]


def test_rendered_page_at_phone_width(browser, rendered_site):
    page = open_page(browser, rendered_site, width=390, height=800)
    try:
        box = rendered_svg_box(page)
        assert box["width"] > 300
        panel = page.locator(".atlas-panel").bounding_box()
        assert panel["y"] >= box["y"] + box["height"]
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    finally:
        page.close()


def test_rendered_selection_draws_five_lines(rendered_page):
    search(rendered_page, "chapingo")
    rendered_page.press("#atlas-search", "Enter")
    rendered_page.wait_for_function(
        "name => document.querySelector('.atlas-panel h2')?.textContent === name",
        arg=CHAPINGO)
    count = len(targets_of(atlas_data(), "luach"))      # three since issue #264
    assert rendered_page.locator(".atlas-out .atlas-target").count() == count
    rendered_page.wait_for_timeout(900)
    box = rendered_svg_box(rendered_page)
    lines = rendered_page.locator("line.atlas-link")
    assert lines.count() == count
    for n in range(count):
        line = lines.nth(n).bounding_box()
        assert line is not None
        assert box["x"] - 1 <= line["x"] and line["x"] + line["width"] <= box["x"] + box["width"] + 1
        assert box["y"] - 1 <= line["y"] and line["y"] + line["height"] <= box["y"] + box["height"] + 1
    RENDERED_CHAPINGO_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    rendered_page.screenshot(path=str(RENDERED_CHAPINGO_SCREENSHOT), full_page=True)
    assert RENDERED_CHAPINGO_SCREENSHOT.stat().st_size > 10_000


# -- the explanation dialog over a toy site (issue #250) ---------------------- #

def quiet(*args):
    pass


@pytest.fixture
def toy_site(with_matrix, stub_umap, cache, tmp_path):
    """A directory laid out like `website/pages/`: the real `atlas.js` and
    `atlas.css`, and the toy corpus' own `atlas.json` and `atlas/pairs/`."""
    import export_atlas_data
    import export_atlas_pairs

    site = tmp_path / "site"
    (site / "atlas").mkdir(parents=True)
    shutil.copy(APP_JS, site / "atlas" / "atlas.js")
    shutil.copy(APP_CSS, site / "atlas" / "atlas.css")
    export_atlas_data.export(with_matrix, site / "atlas" / "atlas.json", log=quiet)
    out_dir = tmp_path / "atlas-pairs"
    export_atlas_pairs.export(with_matrix, out_dir=out_dir, cache_dir=cache, log=quiet)
    export_atlas_pairs.install(out_dir, site / "atlas" / "pairs", log=quiet)
    return {"site": site, "index": index_of(with_matrix),
            "atlas": json.loads((site / "atlas" / "atlas.json").read_text(encoding="utf-8"))}


class Toy:
    """One page over the toy site, with every request it made."""

    def __init__(self, browser, toy_site, page_size=None, width=1280, height=900,
                 models=False):
        block = app_block(QMD.read_text(encoding="utf-8"))
        if not models:
            # A mount without `data-models` builds the page with no model control.
            block = re.sub(r" data-models='[^']*'", "", block)
            assert "data-models" not in block
        if page_size is not None:
            block = block.replace('data-pairs="atlas/pairs/"',
                                  f'data-pairs="atlas/pairs/" data-page-size="{page_size}"')
        site = toy_site["site"]
        (site / "harness.html").write_text(HARNESS.format(app=block), encoding="utf-8")
        self.index = toy_site["index"]
        self.atlas = toy_site["atlas"]
        self.site = site
        self.httpd = serve(site)
        self.requests = []
        self.errors = []
        self.page = browser.new_page(viewport={"width": width, "height": height})
        self.page.on("request", lambda request: self.requests.append(request.url))
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(f"http://127.0.0.1:{self.httpd.server_address[1]}/harness.html")
        self.page.wait_for_selector('#atlas[data-ready="true"]', timeout=30000)

    def pair_file(self, source, target):
        return self.site / "atlas" / "pairs" / f"{self.index[source]}-{self.index[target]}.json"

    def pair(self, source, target):
        return json.loads(self.pair_file(source, target).read_text(encoding="utf-8"))

    def pair_requests(self):
        return [url for url in self.requests if "/atlas/pairs/" in url]

    def second_pair_requests(self):
        return [url for url in self.requests if "/atlas/pairs-qwen3-4b/" in url]

    def select(self, clave, name):
        self.page.locator(f'circle[data-k="{clave}"]').dispatch_event("click")
        self.page.wait_for_function(
            "name => document.querySelector('.atlas-panel h2')?.textContent === name",
            arg=name)

    def why(self, target):
        return self.page.locator(f'.atlas-out .atlas-why[data-i="{self.index[target]}"]')

    def open(self, target):
        self.why(target).click()
        self.page.wait_for_selector("dialog.atlas-explain[open]")
        self.page.wait_for_function(
            "() => !document.querySelector('.atlas-explain-status')"
            " || document.querySelector('.atlas-explain-status').textContent !== 'Loading\u2026'")

    def is_open(self):
        return self.page.evaluate("() => document.querySelector('dialog.atlas-explain').open")

    def close(self):
        self.page.close()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def toy(browser, toy_site):
    toy = Toy(browser, toy_site)
    yield toy
    toy.close()


def weights_in_table(page):
    total = 0.0
    for text in page.locator(".atlas-explain-table tbody .atlas-explain-fraction").all_inner_texts():
        numerator, _, denominator = text.partition("/")
        total += float(numerator) / float(denominator or 1)
    return total


def test_toy_weight_opens_the_excerpts_behind_it(toy):
    """`b -> a` (1.5): two counted unit rows — the shared transitorio and the
    tie — whose weights add up to the panel's number. `b`'s heading and its
    three-owner boilerplate are not among them."""
    toy.select("b", "Ley B")
    panel_weight = toy.why("a").inner_text()
    assert panel_weight == "1.5"
    toy.open("a")
    page = toy.page
    assert page.locator("#atlas-explain-title").inner_text() == "Why Ley B is close to Ley A"
    pair = toy.pair("b", "a")
    rows = page.locator(".atlas-explain-table tbody tr")
    assert rows.count() == pair["provisions"] == 2
    lead = page.locator(".atlas-explain-lead").inner_text()
    b = toy.atlas["instruments"][toy.index["b"]]
    assert b["pc"] < b["p"]                 # the lead's denominator is the counted number
    assert lead == ("2 excerpts of Ley B have their closest text outside it in Ley A; "
                    f"they add up to 1.5 of its {b['pc']} counted excerpts.")
    assert weights_in_table(page) == pytest.approx(float(panel_weight), abs=0.05)
    fractions = page.locator(".atlas-explain-fraction").all_inner_texts()
    assert fractions.count("1") == 1 and "1/2" in fractions and "1/3" not in fractions
    assert "this text is shared by 2 instruments" in page.locator(".atlas-explain-table").inner_text()
    assert page.locator(".atlas-explain-similarity").nth(1).inner_text() == "1.000"
    # Both sides, in full.
    first = page.locator(".atlas-explain-table tbody tr").first
    assert "transitorio compartido" in first.locator(".atlas-explain-source").inner_text()
    assert "transitorio compartido" in first.locator(".atlas-explain-target").inner_text()
    assert page.locator(".atlas-explain-count").inner_text() == "Showing 2 of 2"
    assert not page.locator(".atlas-explain-more").is_visible()
    assert toy.errors == []


@pytest.mark.parametrize("how", ["escape", "button", "backdrop"])
def test_toy_dialog_closes_and_leaves_the_map_alone(toy, how):
    toy.select("b", "Ley B")
    page = toy.page
    page.wait_for_timeout(300)
    positions = page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('cx'))")
    links = page.locator("line.atlas-link").count()
    toy.open("a")
    if how == "escape":
        page.keyboard.press("Escape")
    elif how == "button":
        page.locator(".atlas-explain-close").click()
    else:
        page.mouse.click(5, 5)
    page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
    assert panel_title(page) == "Ley B"
    assert page.locator("circle.atlas-ring").count() == 1
    assert page.locator("line.atlas-link").count() == links
    assert page.eval_on_selector_all(
        "circle.atlas-point", "nodes => nodes.map(n => n.getAttribute('cx'))") == positions
    assert page.evaluate(
        "i => document.activeElement.classList.contains('atlas-why')"
        " && document.activeElement.dataset.i === String(i)", toy.index["a"])
    assert toy.errors == []


def test_toy_points_here_weights_are_plain_numbers(toy):
    toy.select("a", "Ley A")
    page = toy.page
    assert page.locator(".atlas-inc .atlas-target").count() > 0
    assert page.locator(".atlas-inc .atlas-why").count() == 0
    assert page.locator(".atlas-inc button.atlas-weight").count() == 0
    page.locator(".atlas-inc .atlas-weight").first.click()
    page.wait_for_timeout(200)
    assert not toy.is_open()
    assert toy.pair_requests() == []
    # Every closest instrument's weight is a button.
    assert page.locator(".atlas-out .atlas-why").count() == \
        page.locator(".atlas-out .atlas-target").count() == 2


def test_toy_fetches_on_click_only_and_once_per_pair(toy):
    toy.select("b", "Ley B")
    assert toy.pair_requests() == []
    for _ in range(2):
        toy.open("a")
        toy.page.keyboard.press("Escape")
    toy.open("900")
    toy.page.keyboard.press("Escape")
    requested = sorted(url.rsplit("/", 1)[1] for url in toy.pair_requests())
    assert requested == sorted([f"{toy.index['b']}-{toy.index['a']}.json",
                                f"{toy.index['b']}-{toy.index['900']}.json"])


def test_toy_missing_pair_says_not_available_and_the_page_keeps_working(toy):
    toy.pair_file("b", "a").unlink()
    toy.select("b", "Ley B")
    toy.open("a")
    page = toy.page
    assert page.locator(".atlas-explain-status").inner_text() == NOT_AVAILABLE
    assert page.locator("#atlas-explain-title").inner_text() == "Why Ley B is close to Ley A"
    page.keyboard.press("Escape")
    toy.select("c", "Ley C")
    assert page.locator("circle.atlas-ring").count() == 1
    toy.open("a")
    assert page.locator(".atlas-explain-table tbody tr").count() == toy.pair("c", "a")["provisions"]
    assert toy.errors == []


def test_toy_pair_built_for_another_map_says_so(toy):
    path = toy.pair_file("b", "a")
    pair = json.loads(path.read_text(encoding="utf-8"))
    pair["source"]["k"] = "zzz"
    path.write_text(json.dumps(pair, ensure_ascii=False), encoding="utf-8")
    toy.select("b", "Ley B")
    toy.open("a")
    assert toy.page.locator(".atlas-explain-status").inner_text() == OTHER_VERSION
    assert toy.page.locator(".atlas-explain-table").count() == 0
    assert toy.errors == []


def test_toy_table_pages_by_the_mounts_page_size(browser, toy_site):
    toy = Toy(browser, toy_site, page_size=1)
    try:
        toy.select("b", "Ley B")
        toy.open("a")
        page = toy.page
        rows = page.locator(".atlas-explain-table tbody tr")
        assert rows.count() == 1
        assert page.locator(".atlas-explain-count").inner_text() == "Showing 1 of 2"
        more = page.locator(".atlas-explain-more")
        assert more.inner_text() == "Show 1 more (1 left)"
        more.click()
        assert rows.count() == 2
        assert page.locator(".atlas-explain-count").inner_text() == "Showing 2 of 2"
        assert not more.is_visible()
        assert rows.locator(".atlas-explain-n").all_inner_texts() == ["1", "2"]
    finally:
        toy.close()


def test_toy_text_renders_bold_and_paragraphs_and_nothing_else(toy):
    path = toy.pair_file("b", "a")
    pair = json.loads(path.read_text(encoding="utf-8"))
    first = pair["rows"][0]
    pair["texts"][str(first["text"])] = "**Primero.-** Uno <b>dos</b> **suelto\n\nTres"
    path.write_text(json.dumps(pair, ensure_ascii=False), encoding="utf-8")
    toy.select("b", "Ley B")
    toy.open("a")
    cell = toy.page.locator(".atlas-explain-table tbody tr").first.locator(".atlas-explain-source")
    paragraphs = cell.locator("p.atlas-explain-text")
    assert paragraphs.count() == 2
    assert paragraphs.first.locator("strong").all_inner_texts() == ["Primero.-"]
    assert paragraphs.first.inner_text() == "Primero.- Uno <b>dos</b> **suelto"
    assert cell.locator("b").count() == 0
    assert paragraphs.nth(1).inner_text() == "Tres"


def test_toy_dialog_fills_a_phone_and_stacks_its_rows(browser, toy_site):
    toy = Toy(browser, toy_site, width=390, height=844)
    try:
        toy.select("b", "Ley B")
        toy.why("a").scroll_into_view_if_needed()
        toy.open("a")
        page = toy.page
        box = page.locator("dialog.atlas-explain").bounding_box()
        assert box["width"] == pytest.approx(390, abs=1)
        assert box["height"] == pytest.approx(844, abs=1)
        cells = page.locator(".atlas-explain-table tbody tr").first.locator("td")
        tops = [cells.nth(n).bounding_box()["y"] for n in range(cells.count())]
        assert tops == sorted(tops) and len(set(tops)) == len(tops)      # stacked
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        assert page.evaluate(
            "() => { const d = document.querySelector('dialog.atlas-explain');"
            " return d.scrollWidth <= d.clientWidth; }")
    finally:
        toy.close()


# -- more data sets over the toy site (issues #262, #268) ---------------------- #

@pytest.fixture
def toy_two_models(toy_site):
    """The toy site with a second model: the same instruments (`k` at every
    position), a different relation for `b` and a different layout, and its
    own pair directory whose `b -> a` file says which model it belongs to.
    A third set (issue #268) is a BM25 one: `meta.method` says so, its pair
    directory `pairs-bm25/` holds raw scores, and its relation for `b` differs
    again."""
    site = toy_site["site"]
    index = toy_site["index"]
    second = json.loads((site / "atlas" / "atlas.json").read_text(encoding="utf-8"))
    second["meta"]["model"] = "Qwen/Qwen3-Embedding-4B"
    b = second["instruments"][index["b"]]
    assert len(b["out"]) == 2
    b["out"] = [[j, weight + 1.0] for j, weight in reversed(b["out"])]
    for layout, points in second["projections"].items():
        second["projections"][layout] = [[1 - x, y] for x, y in points]
    (site / "atlas" / FOUR_B_FILE).write_text(json.dumps(second), encoding="utf-8")
    shutil.copytree(site / "atlas" / "pairs", site / "atlas" / "pairs-qwen3-4b")
    path = site / "atlas" / "pairs-qwen3-4b" / f"{index['b']}-{index['a']}.json"
    pair = json.loads(path.read_text(encoding="utf-8"))
    pair["texts"][str(pair["rows"][0]["text"])] = "texto del segundo modelo"
    path.write_text(json.dumps(pair, ensure_ascii=False), encoding="utf-8")

    third = json.loads((site / "atlas" / "atlas.json").read_text(encoding="utf-8"))
    third["meta"]["model"] = "bm25"
    third["meta"]["method"] = "bm25"
    b = third["instruments"][index["b"]]
    b["out"] = [[j, weight + 2.0] for j, weight in b["out"]]
    # BM25 counts a different number of excerpts for one instrument (issue #271: `pc` is
    # per model), so its circle changes radius on the switch.
    # The instrument chosen is one below the largest, bumped by one, so the
    # scale's domain stays put and only its own circle moves.
    largest = max(entry["pc"] for entry in third["instruments"])
    changed = next(entry for entry in third["instruments"] if entry["pc"] < largest)
    changed["pc"] += 1
    for layout, points in third["projections"].items():
        third["projections"][layout] = [[x, 1 - y] for x, y in points]
    (site / "atlas" / BM25_FILE).write_text(json.dumps(third), encoding="utf-8")
    shutil.copytree(site / "atlas" / "pairs", site / "atlas" / "pairs-bm25")
    path = site / "atlas" / "pairs-bm25" / f"{index['b']}-{index['a']}.json"
    pair = json.loads(path.read_text(encoding="utf-8"))
    pair["texts"][str(pair["rows"][0]["text"])] = "texto del baseline lexico"
    for n, row in enumerate(pair["rows"]):
        row["similarity"] = 12.3456 + n          # a raw BM25 score, not a cosine
    path.write_text(json.dumps(pair, ensure_ascii=False), encoding="utf-8")
    return {**toy_site, "second": second, "second_file": site / "atlas" / FOUR_B_FILE,
            "third": third, "third_file": site / "atlas" / BM25_FILE,
            "pc_changed": changed["k"]}


def toy_out(page):
    return [(weight, name) for weight, name in zip(
        page.locator(".atlas-out .atlas-weight").all_inner_texts(),
        page.locator(".atlas-out .atlas-target-name").all_inner_texts())]


def test_toy_circle_area_is_proportional_to_counted_excerpts(browser, toy_site):
    """Issue #271: `pc`, not `p`, sizes a circle -- the toy table has an
    instrument whose two numbers differ, and the legend says what area means."""
    toy = Toy(browser, toy_site)
    try:
        entries = toy.atlas["instruments"]
        assert any(entry["pc"] < entry["p"] for entry in entries)
        radius = {entry["k"]: float(toy.page.get_attribute(f'circle[data-k="{entry["k"]}"]', "r"))
                  for entry in entries}
        biggest = max(entries, key=lambda entry: entry["pc"])
        for entry in entries:
            if radius[entry["k"]] > 2.5:
                assert radius[entry["k"]] ** 2 / radius[biggest["k"]] ** 2 == pytest.approx(
                    entry["pc"] / biggest["pc"], rel=1e-3), entry["k"]
        assert toy.page.get_attribute(".atlas-sizes", "aria-label") == \
            "Circle area is proportional to counted excerpts"
        assert toy.page.locator(".atlas-sizes").text_content().endswith("counted excerpts")
        toy.select("b", "Ley B")
        panel = toy.page.locator(".atlas-panel").inner_text()
        b = entries[toy.index["b"]]
        assert f"{b['pc']} counted excerpt" in panel
        assert f"{b['p']} excerpt" not in panel
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_a_model_switch_resizes_the_circle_whose_counted_excerpts_differ(
        browser, toy_two_models):
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        k = toy_two_models["pc_changed"]
        position = next(i for i, entry in enumerate(toy_two_models["third"]["instruments"])
                        if entry["k"] == k)
        first = toy_two_models["atlas"]["instruments"][position]
        third = toy_two_models["third"]["instruments"][position]
        assert third["pc"] == first["pc"] + 1
        radius = lambda key: float(page.get_attribute(f'circle[data-k="{key}"]', "r"))
        before = radius(k)
        legend = page.locator(".atlas-sizes").text_content()
        choose_model(page, "BM25")
        assert radius(k) > before
        assert radius(k) ** 2 / before ** 2 == pytest.approx(third["pc"] / first["pc"], rel=1e-3)
        choose_model(page, "0.6B")
        assert radius(k) == pytest.approx(before)
        assert page.locator(".atlas-sizes").text_content() == legend
        toy.select(k, first["n"])
        for label, entry in (("BM25", third), ("0.6B", first)):
            choose_model(page, label)
            assert f"{entry['pc']} counted excerpt" in page.locator(".atlas-panel").inner_text()
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_without_data_models_has_no_model_control(browser, toy_site):
    toy = Toy(browser, toy_site)
    try:
        assert toy.page.locator(MODEL).count() == 0
        assert toy.page.locator(".atlas-model").count() == 0
        assert toy.page.locator(NEIGHBOURHOOD).count() == 1
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_dialog_reads_the_pair_directory_of_the_model_on_screen(browser, toy_two_models):
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        toy.select("b", "Ley B")
        toy.open("a")
        assert "texto del segundo modelo" not in page.locator(".atlas-explain-table").inner_text()
        page.keyboard.press("Escape")
        assert toy.second_pair_requests() == []

        choose_model(page, "4B")
        assert [url.rsplit("/", 1)[1] for url in toy.requests if url.endswith(FOUR_B_FILE)] \
            == [FOUR_B_FILE]
        instruments = toy_two_models["second"]["instruments"]
        assert toy_out(page)[0][1] == instruments[
            instruments[toy.index["b"]]["out"][0][0]]["n"]           # the reversed relation
        toy.open("a")
        assert "texto del segundo modelo" in page.locator(".atlas-explain-table").inner_text()
        assert [url.rsplit("/", 1)[1] for url in toy.second_pair_requests()] \
            == [f"{toy.index['b']}-{toy.index['a']}.json"]
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")

        # Back on the first model, the same pair is its own file again, cached.
        choose_model(page, "0.6B")
        toy.open("a")
        assert "texto del segundo modelo" not in page.locator(".atlas-explain-table").inner_text()
        assert len(toy.pair_requests()) == 1 and len(toy.second_pair_requests()) == 1
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_the_dialog_header_and_decimals_follow_the_set_on_screen(browser, toy_two_models):
    """`meta.method` drives the column, not the option's label: a cosine reads
    "Similarity" with three decimals, a BM25 score "Score" with one."""
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        header = lambda: page.locator("th.atlas-explain-similarity").text_content()
        column = lambda: page.locator("td.atlas-explain-similarity").first.get_attribute(
            "data-column")
        toy.select("b", "Ley B")
        for label, expected, first_cell in (("0.6B", "Similarity", "1.000"),
                                            ("BM25", "Score", "12.3"),
                                            ("4B", "Similarity", "1.000"),
                                            ("BM25", "Score", "12.3"),
                                            ("0.6B", "Similarity", "1.000")):
            choose_model(page, label)
            toy.open("a")
            assert header() == expected, label
            assert column() == expected
            cells = page.locator("td.atlas-explain-similarity").all_inner_texts()
            assert cells[0] == first_cell, (label, cells)
            if expected == "Score":
                assert cells == ["12.3", "13.3"]
                assert "texto del baseline lexico" in page.locator(".atlas-explain-table").inner_text()
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
        assert [url.rsplit("/", 1)[1] for url in toy.requests if url.endswith(BM25_FILE)] \
            == [BM25_FILE]
        assert len([url for url in toy.requests if "/atlas/pairs-bm25/" in url]) == 1
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_a_bm25_file_with_another_instrument_table_is_refused(browser, toy_two_models):
    third = toy_two_models["third"]
    third["instruments"][0]["k"] = "zzz"
    toy_two_models["third_file"].write_text(json.dumps(third), encoding="utf-8")
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        toy.select("b", "Ley B")
        before = toy_out(page)
        choose_model(page, "BM25", checked=False)
        page.wait_for_function(
            "() => /cannot be used/.test(document.querySelector('.atlas-model-status').textContent)")
        assert page.locator(".atlas-model-status").inner_text() \
            == "The BM25 map cannot be used: it lists different instruments."
        assert checked_model(page) == "0.6B"
        assert toy_out(page) == before
        toy.open("a")
        assert page.locator("th.atlas-explain-similarity").text_content() == "Similarity"
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_switching_while_the_dialog_loads_drops_the_old_answer(browser, toy_two_models):
    """A pair still on its way when the model changes must not fill the dialog."""
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        toy.select("b", "Ley B")
        held = []
        page.route("**/atlas/pairs/*", lambda route: held.append(route))
        toy.why("a").click()
        page.wait_for_selector("dialog.atlas-explain[open]")
        assert page.locator(".atlas-explain-status").inner_text() == "Loading\u2026"
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
        choose_model(page, "4B")
        assert held
        for route in held:
            route.continue_()
        page.wait_for_timeout(300)
        assert not toy.is_open()
        assert page.locator(".atlas-explain-table").count() == 0
        assert toy.errors == []
    finally:
        toy.close()


def test_toy_second_file_with_another_instrument_table_is_refused(browser, toy_two_models):
    second = toy_two_models["second"]
    second["instruments"][0]["k"] = "zzz"
    toy_two_models["second_file"].write_text(json.dumps(second), encoding="utf-8")
    toy = Toy(browser, toy_two_models, models=True)
    try:
        page = toy.page
        toy.select("b", "Ley B")
        before = toy_out(page)
        choose_model(page, "4B", checked=False)
        page.wait_for_function(
            "() => /cannot be used/.test(document.querySelector('.atlas-model-status').textContent)")
        assert page.locator(".atlas-model-status").inner_text() \
            == "The 4B map cannot be used: it lists different instruments."
        assert checked_model(page) == "0.6B"
        assert toy_out(page) == before
        assert panel_title(page) == "Ley B"
        assert toy.errors == []
    finally:
        toy.close()


# -- the real Constitution pair, when installed ------------------------------- #

def test_the_constitution_explains_its_heaviest_weight(page):
    if not PAIRS.is_dir():
        pytest.skip(f"{PAIRS} is not installed: run export_atlas_pairs.py --install "
                    "website/pages/atlas/pairs, or unpack the atlas-pairs release there")
    data = atlas_data()
    cpeum = instrument(data, "cpeum")
    target_index, weight = cpeum["out"][0]
    target = data["instruments"][target_index]["n"]
    name = f"{data['instruments'].index(cpeum)}-{target_index}.json"
    pair = json.loads((PAIRS / name).read_text(encoding="utf-8"))
    total = pair["provisions"]
    shared = max(r["m"] for r in pair["rows"])

    requests = []
    page.on("request", lambda request: requests.append(request.url))
    select_by_search(page, "constitucion", CONSTITUTION)
    assert not [url for url in requests if "/atlas/pairs/" in url]
    row = page.locator(".atlas-out .atlas-target").filter(has_text=target)
    button = row.locator(".atlas-why")
    assert button.inner_text() == f"{weight:.1f}"
    button.click()
    page.wait_for_selector(".atlas-explain-table tbody tr")
    title = page.locator("#atlas-explain-title").inner_text()
    assert CONSTITUTION in title and target in title
    lead = page.locator(".atlas-explain-lead").inner_text()
    assert lead.startswith(f"{total} excerpts of ")
    assert f"they add up to {weight:.1f} of its {cpeum['pc']:,} counted excerpts" in lead
    assert f"{cpeum['p']:,}" not in lead
    rows = page.locator(".atlas-explain-table tbody tr")
    assert rows.count() == min(50, total)
    more = page.locator(".atlas-explain-more")
    if total > 50:                # paging; the toy site's tests cover it in any case
        assert more.inner_text() == f"Show {min(50, total - 50)} more ({total - 50} left)"
        for shown in [*range(100, total, 50), total]:
            more.click()
            assert rows.count() == shown
    assert not more.is_visible()
    assert page.locator(".atlas-explain-count").inner_text() == f"Showing {total} of {total}"
    assert weights_in_table(page) == pytest.approx(weight, abs=0.05)
    fractions = page.locator(".atlas-explain-fraction").all_inner_texts()
    table = page.locator(".atlas-explain-table").inner_text()
    if shared > 1:
        assert f"1/{shared}" in fractions and "1" in fractions
        assert f"this text is shared by {shared} instruments" in table
    else:                        # every row is a whole 1: nothing is shared
        assert set(fractions) == {"1"}
        assert "this text is shared by" not in table
    # The whole text, both sides: the file's own, rendered without its `**`.
    assert [url.rsplit("/", 1)[1] for url in requests if "/atlas/pairs/" in url] == [name]
    longest = max(pair["rows"], key=lambda r: len(pair["texts"][str(r["text"])]))
    expected = pair["texts"][str(longest["text"])].replace("**", "")
    assert expected[-60:] in table
    assert forbidden_in(page.content()) == []
    page.locator(".atlas-explain-close").click()
    page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
    assert panel_title(page) == CONSTITUTION
    button.click()
    page.wait_for_selector(".atlas-explain-table tbody tr")
    page.wait_for_timeout(300)
    EXPLAIN_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EXPLAIN_SCREENSHOT))
    assert EXPLAIN_SCREENSHOT.stat().st_size > 10_000
    assert len([url for url in requests if "/atlas/pairs/" in url]) == 1


def test_rendered_dialog_heading_has_no_quarto_rule(rendered_page):
    """The #247 lesson, for the dialog: Quarto's h2 rule must not reach it."""
    select_by_search(rendered_page, "chapingo", CHAPINGO)
    rendered_page.locator(".atlas-out .atlas-why").first.click()
    rendered_page.wait_for_selector("dialog.atlas-explain[open] h2")
    assert rendered_page.eval_on_selector(
        ".atlas-explain h2", "n => getComputedStyle(n).borderBottomWidth") == "0px"
    box = rendered_page.locator("dialog.atlas-explain").bounding_box()
    assert box["width"] > 600
    rendered_page.keyboard.press("Escape")
    rendered_page.wait_for_function("() => !document.querySelector('dialog.atlas-explain').open")
    assert panel_title(rendered_page) == CHAPINGO


# -- the embedding model control in the page Quarto renders (issue #262) ------- #

def test_rendered_model_control_switches_after_quartos_css(rendered_page):
    default, other = atlas_data(), data_4b()
    page = rendered_page
    assert page.locator(MODEL).locator('[role="radio"]').all_inner_texts() == ["0.6B", "4B", "BM25"]
    assert page.locator("#atlas-model-label").inner_text().lower() == "similarity"
    assert page.eval_on_selector(
        "#atlas-model-label", "n => getComputedStyle(n).textTransform") == "uppercase"
    generated = page.locator("[data-atlas-generated]").inner_text()
    select_by_search(page, "chapingo", CHAPINGO)
    assert out_list(page) == targets_of(default, "luach")
    choose_model(page, "4B")
    assert out_list(page) == targets_of(other, "luach")
    # The page shows the date of the map on screen: it changes with the model
    # when the two files were generated on different days, and only then.
    days = {data["meta"]["generated"][:10] for data in (default, other)}
    assert (page.locator("[data-atlas-generated]").inner_text() != generated) == (len(days) == 2)
    assert panel_title(page) == CHAPINGO
    assert forbidden_in(page.locator("#atlas").inner_text()) == []
    choose_model(page, "BM25")
    assert out_list(page) == targets_of(data_bm25(), "luach")
    assert panel_title(page) == CHAPINGO
    assert forbidden_in(page.locator("#atlas").inner_text()) == []
    choose_model(page, "0.6B")
    assert out_list(page) == targets_of(default, "luach")
    assert page.locator("[data-atlas-generated]").inner_text() == generated
    assert page.locator("#atlas aside").count() == 0


def test_rendered_page_lays_the_two_controls_side_by_side_on_a_desktop(rendered_page):
    neighbourhood = rendered_page.locator(".atlas-neighbourhood").bounding_box()
    model = rendered_page.locator(".atlas-model").bounding_box()
    assert abs(neighbourhood["y"] - model["y"]) < 4
    assert neighbourhood["x"] + neighbourhood["width"] <= model["x"] + 1
    assert rendered_page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0


def test_rendered_page_stacks_the_two_controls_on_a_phone(browser, rendered_site):
    page = open_page(browser, rendered_site, width=390, height=800)
    try:
        neighbourhood = page.locator(".atlas-neighbourhood").bounding_box()
        model = page.locator(".atlas-model").bounding_box()
        assert model["y"] >= neighbourhood["y"] + neighbourhood["height"] - 1
        assert model["x"] + model["width"] <= 390
        choose_model(page, "4B")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    finally:
        page.close()
