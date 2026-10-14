"""The website's Instruments page (issue #290), in a real browser.

    uv run --group viz playwright install chromium     # once
    uv run --group viz pytest scripts/embeddings/tests/test_instruments_page.py -q

The page is a hand-written application (`website/pages/instruments/instruments.js`,
`instruments.css`) over the committed index `website/pages/instruments/instruments.json`
and the per-instrument files of issue #289, wrapped by `website/pages/instruments.qmd`.

Three layers, like `test_atlas_page.py`, whose helpers this module imports:

* static checks, always run: front matter, the mount block, the prose, the navbar,
  the committed index against the Atlas' own data;
* browser tests over a **toy site**: the five-instrument corpus of
  `test_export_instrument_files.py`, exported by the real exporter into a temporary
  directory laid out like `website/pages/`, with the real `instruments.js` and
  `instruments.css` and a harness page whose body is the qmd's own
  `<!-- instruments:app -->` block, copied verbatim. Fast, needs no Quarto and no
  release;
* rendered-page tests, which render `pages/instruments.qmd` with Quarto into the
  gitignored `website/_site/` and drive what Quarto wrote (the harness carries no site
  stylesheet, which is how issue #247 reached the Atlas unseen).

The browser tests skip, with the reason, when Playwright or its Chromium is missing;
the rendered ones also skip without a Quarto binary; the one that opens a real
3,589-article document skips when `website/pages/instruments/files/` was not installed.
"""

import functools
import http.server
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_atlas_page import (  # noqa: E402,F401
    HARNESS, PAGES, PALETTE, PLURAL, SITE, WEBSITE, REPO, browser, find_quarto, forbidden_in,
    front_matter, lead_phrases_in, serve)
from test_export_instrument_files import (  # noqa: E402,F401
    DOCUMENTS, exported, js_slice, read_file, world)
from test_instrument_matrix import cache, index_of, prepared, stub_umap, with_matrix  # noqa: E402,F401

QMD = PAGES / "instruments.qmd"
APP_JS = PAGES / "instruments" / "instruments.js"
APP_CSS = PAGES / "instruments" / "instruments.css"
INDEX = PAGES / "instruments" / "instruments.json"
FILES = PAGES / "instruments" / "files"
ATLAS_4B = PAGES / "atlas" / "atlas-qwen3-4b.json"
SCREENSHOT = REPO / "output" / "instruments-alfa.png"
RENDERED_SCREENSHOT = REPO / "output" / "instruments-rendered.png"

TITLE = "The Instruments of the Atlas, as md2akn Reads Them"
REPO_URL = "https://github.com/INGEOTEC/LegalIA"
NOT_AVAILABLE = "This instrument's file is not available."
OTHER_VERSION = "a different version of the data"

EXPECTED_BLOCK = (
    '<!-- instruments:app -->\n'
    '<link rel="stylesheet" href="instruments/instruments.css">\n'
    '<div id="instruments" class="instruments" data-index="instruments/instruments.json" '
    'data-files="instruments/files/" '
    'data-models=\'[{"label":"4B","index":"instruments/instruments.json",'
    '"files":"instruments/files/"}]\' '
    f'data-repo="{REPO_URL}"></div>\n'
    '<script src="instruments/instruments.js"></script>\n'
    '<!-- /instruments:app -->')


def index_data() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def app_block(text: str) -> str:
    match = re.search(r"<!-- instruments:app -->.*?<!-- /instruments:app -->", text, re.S)
    assert match, "instruments.qmd has no <!-- instruments:app --> block"
    return match.group(0)


def prose_of(text: str) -> str:
    return text.split("<!-- /instruments:app -->", 1)[1]


def subtitle_of(data: dict) -> str:
    parts = [f"{n:,} {PLURAL[name]}" for name, n in data["meta"]["collections"].items()]
    return f"{', '.join(parts[:-1])} and {parts[-1]}, article by article"


# -- static checks, always run -------------------------------------------------------- #

def test_the_qmd_has_the_agreed_front_matter_and_no_code():
    text = QMD.read_text(encoding="utf-8")
    fields = front_matter(text)
    assert fields["title"] == TITLE
    assert fields["subtitle"] == subtitle_of(index_data())
    assert fields["page-layout"] == "full"
    assert fields["toc"] == "false"
    assert fields["number-sections"] == "false"
    assert not re.search(r"^```\{(python|r|julia|ojs)", text, re.M)
    assert "freeze" not in text


def test_the_qmd_mounts_the_application_exactly_as_agreed_and_loads_no_library():
    block = app_block(QMD.read_text(encoding="utf-8"))
    assert block == EXPECTED_BLOCK
    assert "d3" not in block and "http" not in block.replace(REPO_URL, "")


def test_the_prose_says_what_the_page_is_and_nothing_forbidden():
    prose = prose_of(QMD.read_text(encoding="utf-8"))
    assert prose.lstrip().startswith("::: {.instruments-prose}")
    assert forbidden_in(prose) == []
    assert "UMAP" not in prose
    assert lead_phrases_in(prose) == []
    flat = " ".join(prose.split())
    for needle in ("fracción", "inciso", "apartado", "one introduction", "one table",
                   "compared excerpts", "Nearest texts",
                   "Qwen3 Embedding 4B", "atlas-instruments", "Raw"):
        assert needle in flat, needle
    # One sentence per flag, under the label the page prints.
    for label in ("Loose", "Repeated number", "Number gap", "Over window", "No articles",
                  "Uncovered", "No transitory section"):
        assert f"<strong>{label}</strong>" in prose, label


def test_the_application_files_say_nothing_forbidden_in_what_they_print():
    # The JS names an excerpt `unit` in its own variables and reads `data-unit-type`
    # (both the agreed contract), so the check is over the strings it prints.
    js = APP_JS.read_text(encoding="utf-8")
    literals = re.findall(r'"([^"\n]*)"|`([^`\n]*)`', js)
    printed = " ".join(double or backtick for double, backtick in literals)
    for word in ("tooltip", "n_neighbors", "Generated by", "build_instrument_umap_html",
                 "UMAP"):
        assert word not in printed, word
    assert "provision" not in printed.lower()
    css = APP_CSS.read_text(encoding="utf-8")
    assert "<aside" not in js and "aside" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert "d3." not in js and "import " not in js


def test_the_script_hard_codes_no_path_and_reads_only_its_mount():
    js = APP_JS.read_text(encoding="utf-8")
    for attribute in ("dataset.index", "dataset.files", "dataset.repo"):
        assert attribute in js
    assert "instruments/instruments.json" in js           # only as the documented default
    assert "https://github.com/INGEOTEC/LegalIA" in js


def test_the_css_documents_its_palette_and_resets_quartos_grid_rules():
    css = APP_CSS.read_text(encoding="utf-8")
    for color in PALETTE.values():
        assert color in APP_JS.read_text(encoding="utf-8")
    assert "grid-column: auto" in css
    assert "border-bottom: 0" in css and "padding-bottom: 0" in css
    assert "max-width: 900px" in css and "max-width: 640px" in css
    prose = css[css.index(".instruments-prose"):]
    assert "max-width: 100%" in prose and "overflow-x: auto" in prose
    block = css[css.index(".instruments-prose {"):].split("}")[0]
    assert "max-width" not in block
    assert "warning" in css and "#b45309" in css


def test_the_navbar_has_no_instruments_entry_but_the_page_still_renders_and_ships(tmp_path):
    # By decision of the repository owner on 2026-10-13 (review fix-1 of #290) the page is
    # reached through the link at the end of the Atlas page, not from the navbar; it is
    # neither a draft nor excluded from the render.
    text = (WEBSITE / "_quarto.yml").read_text(encoding="utf-8")
    entries = re.findall(r"- href: (\S+)\n\s+text: (.+)", text)
    assert "pages/instruments.qmd" not in [href for href, _ in entries]
    assert "Instruments" not in [name.strip() for _, name in entries]
    resources = re.search(r"resources:\s*\n((?:\s+(?:-|#).*\n)+)", text).group(1)
    listed = [line.strip().lstrip("- ").strip('"') for line in resources.splitlines()
              if not line.strip().startswith("#")]
    assert listed[:2] == ["pages/atlas/**", "pages/instruments/**"]
    render = [line for line in text[text.index("render:"):text.index("resources:")].splitlines()
              if line.strip().startswith("-")]
    assert render and not any("instruments" in line for line in render)
    assert "draft" not in front_matter(QMD.read_text(encoding="utf-8"))


def test_the_atlas_page_ends_with_a_link_to_the_instruments_page():
    atlas = (PAGES / "atlas.qmd").read_text(encoding="utf-8")
    assert atlas.count("(instruments.qmd)") == 1
    link = atlas.index("(instruments.qmd)")
    assert link > atlas.index("{{< include atlas/_gold-links.md >}}")      # after the callout
    assert link > atlas.index("::: {.atlas-prose}")
    tail = atlas[link:]
    assert tail.rstrip().endswith(":::") and tail.count("\n:::") == 1      # inside .atlas-prose
    paragraph = atlas[:link].rsplit("\n\n", 1)[1] + tail.split("\n\n", 1)[0]
    assert "excerpts" in paragraph and "parse" in paragraph
    assert forbidden_in(paragraph) == []
    assert lead_phrases_in(paragraph) == []


def test_the_citation_hook_and_the_references_page_are_unchanged():
    hook = (WEBSITE / "scripts" / "fix_citation_links.py").read_text(encoding="utf-8")
    assert "instruments" not in hook
    references = (PAGES / "references.qmd").read_text(encoding="utf-8")
    assert "instruments" not in references.lower()


def test_the_committed_index_lists_the_atlas_instruments_in_the_atlas_order():
    index = index_data()
    atlas = json.loads(ATLAS_4B.read_text(encoding="utf-8"))
    assert [(e["c"], e["k"], e["n"]) for e in index["instruments"]] \
        == [(e["c"], e["k"], e["n"]) for e in atlas["instruments"]]
    assert index["meta"]["instruments"] == len(atlas["instruments"])
    assert set(index["meta"]["flag_meanings"]) >= {
        "loose", "duplicate_number", "numbering_gap", "over_window", "no_articles",
        "uncovered", "no_transitorios"}


def test_the_files_folder_is_never_committed():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/website/pages/instruments/files/" in ignored


# -- the toy site --------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def site(exported, tmp_path_factory):
    """`website/pages` in miniature: the toy index and files, the real script and
    stylesheet. Returns the directory."""
    root = tmp_path_factory.mktemp("instruments-site")
    folder = root / "instruments"
    shutil.copytree(exported["out_dir"] / "files", folder / "files")
    shutil.copy(exported["out_dir"] / "instruments.json", folder / "instruments.json")
    shutil.copy(APP_JS, folder / "instruments.js")
    shutil.copy(APP_CSS, folder / "instruments.css")
    return root


@pytest.fixture(scope="module")
def server(site, tmp_path_factory):
    harness = HARNESS.format(app=app_block(QMD.read_text(encoding="utf-8")))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/harness.html":
                body = harness.encode("utf-8")
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
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/harness.html"
    httpd.shutdown()
    httpd.server_close()


def open_page(browser, url, width=1280, height=900, before=None, ready="true"):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    if before:
        before(page)
    page.goto(url)
    page.wait_for_selector(f'#instruments[data-ready="{ready}"]', timeout=30000)
    return page


@pytest.fixture
def page(browser, server):
    page = open_page(browser, server)
    yield page
    assert page.errors == []
    page.close()


def row(page, key):
    return page.locator(f'table.instruments-ranking tbody tr[data-key="{key}"]')


def open_from_table(page, key):
    row(page, key).locator("button.instruments-open").click()
    wait_rendered(page)


def wait_rendered(page):
    page.wait_for_selector('#instruments[data-view="instrument"]')
    page.wait_for_selector('.instruments-document[data-rendered="true"]', timeout=30000)


def title(page):
    return page.locator(".instruments-view h2.instruments-title").inner_text()


def excerpt(page, eid):
    return page.locator(f'[data-excerpt="{eid}"]')


ALFA = "tit_PRIMERO__cap_I__"


# -- the landing view ----------------------------------------------------------------- #

def test_the_landing_lists_every_instrument_ranked_by_score_with_its_totals(page):
    assert page.locator("#instruments").get_attribute("data-view") == "landing"
    rows = page.locator("table.instruments-ranking tbody tr")
    assert rows.count() == 5
    # Default order: score, highest first; `alfa` carries the five flagged excerpts.
    assert rows.first.get_attribute("data-key") == "alfa"
    caption = page.locator(".instruments-caption").inner_text()
    assert "5 instruments" in caption
    headers = page.locator("table.instruments-ranking thead th").all_inner_texts()
    assert headers[:4] == ["Instrument", "Excerpts", "Compared", "Articles"]
    assert headers[-1] == "Score"
    for label in ("Loose", "Repeated number", "Number gap", "Over window", "No articles",
                  "Uncovered", "No transitory section"):
        assert label in headers
    assert page.locator('th[data-key="s"]').get_attribute("aria-sort") == "descending"
    first = rows.first.locator("td").all_inner_texts()
    assert first[0] == "LEY ALFA"
    assert not page.locator(".instruments-view").is_visible()


def test_a_header_sorts_and_a_second_click_reverses_it(page):
    name = page.locator('th[data-key="n"] button')
    name.click()
    keys = lambda: [e.get_attribute("data-key")  # noqa: E731
                    for e in page.locator("table.instruments-ranking tbody tr").all()]
    names = [e.inner_text() for e in page.locator("tbody td.instruments-name").all()]
    assert names == sorted(names, key=str.casefold) or names == sorted(names)
    assert page.locator('th[data-key="n"]').get_attribute("aria-sort") == "ascending"
    name.click()
    assert page.locator('th[data-key="n"]').get_attribute("aria-sort") == "descending"
    assert keys()[0] != ""
    page.locator('th[data-key="u"] button').click()
    counts = [int(e.inner_text().replace(",", ""))
              for e in page.locator("tbody tr td:nth-child(2)").all()]
    assert counts == sorted(counts, reverse=True)


def test_a_collection_button_filters_the_table_and_pressing_it_again_clears_it(page):
    button = page.locator('button.instruments-filter[data-collection="leyes"]')
    assert button.get_attribute("aria-pressed") == "false"
    button.click()
    assert button.get_attribute("aria-pressed") == "true"
    keys = [e.get_attribute("data-collection")
            for e in page.locator("table.instruments-ranking tbody tr").all()]
    assert keys == ["leyes"] * 3
    button.click()
    assert page.locator("table.instruments-ranking tbody tr").count() == 5
    assert page.locator("button.instruments-filter").count() == 3      # no regulations in the toy


def test_fifty_rows_at_a_time_and_show_more(browser, server):
    def inflate(page):
        def handler(route):
            response = route.fetch()
            data = response.json()
            base = data["instruments"]
            data["instruments"] = [dict(e, k=f"{e['k']}-{n}", n=f"{e['n']} {n}")
                                   for n in range(13) for e in base]
            data["meta"]["instruments"] = len(data["instruments"])
            route.fulfill(response=response, json=data)
        page.route("**/instruments/instruments.json", handler)

    page = open_page(browser, server, before=inflate)
    assert page.locator("table.instruments-ranking tbody tr").count() == 50
    more = page.locator("button.instruments-more")
    assert more.is_visible() and "15" in more.inner_text()
    more.click()
    assert page.locator("table.instruments-ranking tbody tr").count() == 65
    assert not more.is_visible()
    assert page.errors == []
    page.close()


def test_an_index_that_cannot_be_read_says_so_and_marks_the_page(browser, server):
    page = open_page(browser, server, ready="error",
                     before=lambda p: p.route("**/instruments/instruments.json",
                                              lambda route: route.fulfill(status=404, body="")))
    assert "could not be loaded" in page.locator("p.instruments-error").inner_text()
    assert page.errors == []
    page.close()


def test_an_index_with_an_unknown_collection_stops_with_a_message_naming_it(browser, server):
    def doctor(page):
        def handler(route):
            response = route.fetch()
            data = response.json()
            data["instruments"][0]["c"] = "decretos"
            route.fulfill(response=response, json=data)
        page.route("**/instruments/instruments.json", handler)

    page = open_page(browser, server, ready="error", before=doctor)
    assert "decretos" in page.locator("p.instruments-error").inner_text()
    page.close()


# -- search ----------------------------------------------------------------------------- #

def search(page, query):
    page.fill("#instruments-search", query)
    return page.locator('#instruments-suggestions li[role="option"]')


def test_the_search_is_a_combobox_that_folds_accents_and_case(page):
    box = page.locator("#instruments-search")
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-expanded") == "false"
    assert box.get_attribute("aria-controls") == "instruments-suggestions"
    assert box.get_attribute("placeholder") == "Find a law, regulation, guideline or treaty"
    items = search(page, "lineamientos p")
    assert items.count() == 1 and items.first.get_attribute("role") == "option"
    assert box.get_attribute("aria-expanded") == "true"
    assert page.locator("#instruments-suggestions").get_attribute("role") == "listbox"
    assert search(page, "CÓDIGO").count() == 0
    assert "No instrument matches." in page.locator("#instruments-suggestions").inner_text()
    assert search(page, "tratado").first.inner_text().startswith("TRATADO X")
    # The abbreviation or the id also finds it.
    assert "LINEAMIENTOS P" in search(page, "900").first.inner_text()
    assert "LEY BETA" in search(page, "beta").first.inner_text()


def test_the_arrow_keys_move_the_selection_and_enter_opens_the_instrument(page):
    items = search(page, "ley")
    assert items.count() == 3
    assert items.nth(0).get_attribute("aria-selected") == "true"
    page.press("#instruments-search", "ArrowDown")
    assert items.nth(1).get_attribute("aria-selected") == "true"
    active = page.locator("#instruments-search").get_attribute("aria-activedescendant")
    assert active == items.nth(1).get_attribute("id")
    page.press("#instruments-search", "ArrowUp")
    page.press("#instruments-search", "ArrowUp")                    # wraps around
    assert items.nth(2).get_attribute("aria-selected") == "true"
    page.press("#instruments-search", "Enter")
    wait_rendered(page)
    assert title(page) == page.locator("#instruments-search").input_value()
    assert page.url.endswith("?c=leyes&k=" + parse_qs(urlparse(page.url).query)["k"][0])


def test_a_mouse_press_on_a_suggestion_opens_it(page):
    search(page, "gamma").first.dispatch_event("mousedown")
    wait_rendered(page)
    assert title(page) == "LEY GAMMA"


def test_escape_closes_the_list_and_then_clears_the_search(page):
    search(page, "alfa")
    assert page.locator("#instruments-suggestions").is_visible()
    page.press("#instruments-search", "Escape")
    assert not page.locator("#instruments-suggestions").is_visible()
    assert page.locator("#instruments-search").input_value() == "alfa"
    page.press("#instruments-search", "Escape")
    assert page.locator("#instruments-search").input_value() == ""


# -- one instrument -------------------------------------------------------------------- #

def test_opening_an_instrument_shows_its_header_summary_outline_and_document(page):
    open_from_table(page, "alfa")
    assert page.url.endswith("?c=leyes&k=alfa")
    assert title(page) == "LEY ALFA"
    head = page.locator("header.instruments-head")
    facts = head.locator(".instruments-facts").inner_text()
    assert "alfa" in facts and "01-02-2020" in facts and "md2akn" in facts
    assert head.locator(".instruments-collection").inner_text() == "Law"
    assert not page.locator(".instruments-ranking").is_visible()
    summary = page.locator("div.instruments-summary")
    assert "Numbering: articulo" in summary.inner_text()
    flags = summary.locator("li.instruments-flag-row")
    assert flags.count() == 7
    assert flags.filter(has_text="Loose").locator("button").inner_text() == "1"
    assert flags.filter(has_text="Repeated number").locator("button").inner_text() == "2"
    assert flags.filter(has_text="No articles").locator(".instruments-flag-none").count() == 1
    outline = page.locator('div[role="complementary"].instruments-outline')
    assert outline.is_visible()
    links = outline.locator("button.instruments-outline-link")
    assert links.count() >= 6
    assert page.locator(".instruments-document section[data-eid]").count() >= 10


def test_every_node_has_a_badge_with_its_type_identifier_and_number(page):
    open_from_table(page, "alfa")
    article = page.locator(f'section[data-eid="{ALFA}art_1"]')
    assert article.get_attribute("data-type") == "article"
    badge = article.locator("> .instruments-node-head .instruments-badge")
    assert badge.inner_text() == f"article · {ALFA}art_1 · 1"
    assert badge.get_attribute("title") == "artículo"
    chapter = page.locator('section[data-eid="tit_PRIMERO__cap_I"]')
    assert chapter.get_attribute("data-type") == "chapter"
    assert chapter.locator("> .instruments-node-head .instruments-badge").get_attribute(
        "title") == "capítulo"
    paragraph = page.locator('section[data-type="paragraph"]').first
    assert paragraph.locator(".instruments-badge").first.get_attribute("title") == "fracción"
    # The text is the Markdown rendered with bold only.
    assert "Texto compartido del primero." in article.inner_text()
    assert article.locator("strong").first.inner_text().startswith("Artículo 1")


def test_each_excerpt_is_wrapped_with_its_type_and_the_flagged_ones_say_why(page):
    open_from_table(page, "alfa")
    # A container's opening text is an introduction since md2akn 0.5.1; what stays loose is
    # the text under the transitory section.
    loose = excerpt(page, "sec_transitorios__p_content")
    assert loose.get_attribute("data-unit-type") == "loose"
    assert loose.locator(".instruments-flag-tag").first.inner_text() == "Loose"
    assert loose.locator(".instruments-flag-tag").first.get_attribute("title").startswith(
        "Text that no rule placed")
    big = excerpt(page, f"{ALFA}art_5")
    assert [t.inner_text() for t in big.locator(".instruments-flag-tag").all()] == [
        "Number gap", "Over window"]
    assert excerpt(page, "preamble").get_attribute("data-unit-type") == "preamble"


def test_an_introduction_is_a_node_with_a_badge_an_excerpt_an_outline_entry_and_nearest_texts(page):
    open_from_table(page, "alfa")
    intro = page.locator(f'section[data-eid="{ALFA}intro"]')
    assert intro.get_attribute("data-type") == "intro"
    badge = intro.locator("> .instruments-node-head .instruments-badge")
    assert badge.inner_text() == f"intro · {ALFA}intro"
    wrapper = excerpt(page, f"{ALFA}intro")
    assert wrapper.get_attribute("data-unit-type") == "intro"
    assert wrapper.locator(".instruments-tag").first.inner_text() == "intro"
    assert "Texto suelto antes de los artículos." in wrapper.inner_text()
    assert wrapper.locator("> details.instruments-near").count() == 1      # a compared excerpt
    assert wrapper.locator(".instruments-flag-tag").count() == 0           # nothing is loose about it
    expand_outline(page)
    link = page.locator(".instruments-outline button.instruments-outline-link",
                        has_text="Introduction").first
    assert link.locator(".instruments-compared").count() == 1
    link.click()
    assert page.locator(".instruments-target").get_attribute("data-eid").endswith("__intro")


def test_a_table_is_drawn_as_an_html_table_in_parsed_mode_with_its_rows_and_cells(page):
    open_from_table(page, "alfa")
    node = page.locator(f'section[data-eid="{ALFA}intro__table_1"]')
    assert node.get_attribute("data-type") == "table"
    assert node.locator("> .instruments-node-head .instruments-badge").inner_text() \
        == f"table · {ALFA}intro__table_1 · 1"
    table = node.locator("table.instruments-table")
    assert table.count() == 1
    rows = table.locator("tr")
    assert rows.count() == 3
    assert [c.inner_text() for c in rows.nth(0).locator("td").all()] == ["Concepto", "Cuota"]
    # A pipe written `\|` inside a cell is a character of the cell, and bold is kept.
    cells = rows.nth(2).locator("td")
    assert cells.count() == 2
    assert cells.nth(0).inner_text() == "Dos | con barra"
    assert cells.nth(1).locator("strong").inner_text() == "2.5"
    # The markdown rows are not also printed as paragraphs.
    assert node.locator("p.instruments-paragraph").count() == 0
    wrapper = excerpt(page, f"{ALFA}intro__table_1")
    assert wrapper.get_attribute("data-unit-type") == "table"
    assert wrapper.locator("> details.instruments-near").count() == 1
    # Raw still shows exactly what md2akn read.
    node.locator("> .instruments-node-head button.instruments-raw-toggle").click()
    assert node.locator("pre.instruments-raw-text").inner_text().startswith("| Concepto | Cuota |")


def test_a_long_table_is_one_html_table_per_excerpt_each_addressable_by_its_piece(browser, server):
    chapter = "tit_PRIMERO__cap_II__intro__table_1"
    page = open_page(browser, f"{server}?c=leyes&k=alfa#{chapter}.2")
    wait_rendered(page)
    node = page.locator(f'section[data-eid="{chapter}"]')
    first, second = excerpt(page, f"{chapter}.1"), excerpt(page, f"{chapter}.2")
    assert first.count() == second.count() == 1
    for piece in (first, second):
        assert piece.get_attribute("data-unit-type") == "table"
        assert piece.locator("table.instruments-table").count() == 1
        assert piece.locator("> details.instruments-near").count() == 1
    assert [t.inner_text() for t in first.locator(".instruments-tag").all()] == ["table", "part 1"]
    assert [t.inner_text() for t in second.locator(".instruments-tag").all()] == ["table", "part 2"]
    # Every one of the thirty rows is on the page exactly once, in order.
    labels = [c.inner_text() for c in node.locator("tr td:first-child").all()]
    assert labels == [f"fila {n:02d}" for n in range(1, 31)]
    assert first.locator("tr").count() + second.locator("tr").count() == 30
    assert first.locator("tr").first.locator("td").first.inner_text() == "fila 01"
    # The address names the piece, and the page marks that piece, not the whole table.
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == f"{chapter}.2"
    assert page.errors == []
    page.close()


def test_the_flags_still_find_the_excerpts_that_are_pieces_of_a_table(browser, server):
    page = open_page(browser, f"{server}?c=leyes&k=alfa")
    wait_rendered(page)
    summary = page.locator("div.instruments-summary")
    # Nothing but the transitory text is loose any more, and raw mode is untouched.
    assert summary.locator('li[data-flag="loose"] button').inner_text() == "1"
    page.locator("button.instruments-segment", has_text="Raw").click()
    ranges = page.locator(".instruments-rawdoc .instruments-range")
    document = read_file_of_toy(page, "alfa")
    assert ranges.count() == sum(1 for u in document["units"] if u["l"] > u["s"])
    assert page.locator(
        f'.instruments-range[data-excerpt="tit_PRIMERO__cap_II__intro__table_1.2"]').count() == 1
    assert page.errors == []
    page.close()


def test_only_compared_excerpts_carry_nearest_texts(page):
    open_from_table(page, "alfa")
    document = read_file_of_toy(page, "alfa")
    searched = sum(1 for u in document["units"] if u.get("x"))
    assert page.locator("details.instruments-near").count() == searched
    transitory = excerpt(page, "sec_transitorios__art_PRIMERO")
    assert transitory.count() == 1 and transitory.locator("details").count() == 0
    # A heading is not an excerpt wrapper: its tag sits in the node's own head.
    heading = page.locator('section[data-eid="tit_PRIMERO"] > .instruments-node-head')
    assert "heading" in heading.inner_text()
    assert page.locator('div.instruments-excerpt[data-unit-type="heading"]').count() == 0


def test_nearest_texts_open_lazily_and_each_entry_names_instrument_label_and_similarity(page):
    open_from_table(page, "alfa")
    near = excerpt(page, f"{ALFA}art_1").locator("details.instruments-near")
    assert near.locator("li").count() == 0                             # built on first open
    near.locator("summary").click()
    items = near.locator("li.instruments-near-item")
    items.first.wait_for()
    assert 1 <= items.count() <= 5
    first = items.first
    link = first.locator("a.instruments-near-link").first
    assert "LEY BETA" in link.inner_text() and "Article 1" in link.inner_text()
    assert link.get_attribute("href") == "?c=leyes&k=beta#art_1"
    assert link.get_attribute("title") == "art_1"
    assert first.locator(".instruments-similarity").inner_text() == "1.000"
    assert link.locator(".instruments-dot").get_attribute("style").startswith("background:#2a78d6")


def test_following_a_nearest_text_opens_the_other_instrument_at_that_excerpt(page):
    open_from_table(page, "alfa")
    excerpt(page, f"{ALFA}art_1").locator("details.instruments-near summary").click()
    excerpt(page, f"{ALFA}art_1").locator("a.instruments-near-link").first.click()
    page.wait_for_function("document.querySelector('h2.instruments-title')?.textContent === 'LEY BETA'")
    wait_rendered(page)
    assert page.url.endswith("?c=leyes&k=beta#art_1")
    target = page.locator(".instruments-target")
    assert target.count() == 1 and target.get_attribute("data-excerpt") == "art_1"
    # Back to the instrument it came from, and back again to the table.
    page.go_back()
    page.wait_for_function("document.querySelector('h2.instruments-title')?.textContent === 'LEY ALFA'")
    assert page.url.endswith("?c=leyes&k=alfa")
    page.go_back()
    page.wait_for_selector("table.instruments-ranking", state="visible")
    assert "k=" not in page.url
    page.go_forward()
    page.wait_for_function("document.querySelector('h2.instruments-title')?.textContent === 'LEY ALFA'")


def test_a_deep_link_opens_the_instrument_and_highlights_the_excerpt(browser, server):
    page = open_page(browser, f"{server}?c=leyes&k=alfa#{ALFA}art_5")
    page.wait_for_selector('.instruments-document[data-rendered="true"]')
    assert title(page) == "LEY ALFA"
    target = page.locator(".instruments-target")
    assert target.count() == 1
    assert target.get_attribute("data-excerpt") == f"{ALFA}art_5"
    assert page.errors == []
    page.close()


def test_an_unknown_instrument_in_the_address_says_so_and_shows_the_landing(browser, server):
    page = open_page(browser, f"{server}?c=leyes&k=nope")
    assert "nope" in page.locator("p.instruments-status").inner_text()
    assert page.locator("table.instruments-ranking").is_visible()
    page.close()
    page = open_page(browser, f"{server}?c=decretos&k=alfa")
    assert page.locator("p.instruments-status").is_visible()
    assert page.locator("table.instruments-ranking").is_visible()
    page.close()


def test_a_flag_count_jumps_to_the_first_flagged_excerpt_and_cycles(page):
    open_from_table(page, "alfa")
    button = page.locator('li[data-flag="duplicate_number"] button')
    position = page.locator('li[data-flag="duplicate_number"] .instruments-flag-position')
    button.click()
    assert position.inner_text() == "1 of 2"
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == f"{ALFA}art_2"
    button.click()
    assert position.inner_text() == "2 of 2"
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == f"{ALFA}art_2_2"
    button.click()
    assert position.inner_text() == "1 of 2"


def test_the_outline_scrolls_to_a_node_and_marks_compared_and_flagged_articles(page):
    open_from_table(page, "alfa")
    outline = page.locator(".instruments-outline")
    link = outline.locator("button.instruments-outline-link", has_text="Art. 5")
    assert not link.is_visible()                                  # below the first level: folded
    expand_outline(page)
    assert link.locator(".instruments-compared").count() == 1
    assert link.locator(".instruments-mark").count() == 1
    link.click()
    assert page.locator(".instruments-target").get_attribute("data-eid") == f"{ALFA}art_5"
    chapter = outline.locator("button.instruments-outline-link", has_text="Capítulo")
    chapter.first.click()
    assert page.locator(".instruments-target").get_attribute("data-eid") == "tit_PRIMERO__cap_I"
    # A container folds.
    fold = outline.locator("button.instruments-outline-fold").first
    before = outline.locator("button.instruments-outline-link:visible").count()
    fold.click()
    assert outline.locator("button.instruments-outline-link:visible").count() < before


def test_the_raw_button_of_a_node_shows_exactly_its_markdown(page):
    open_from_table(page, "alfa")
    document = read_file_of_toy(page, "alfa")
    node = page.locator(f'section[data-eid="{ALFA}art_2"]')
    button = node.locator("> .instruments-node-head button.instruments-raw-toggle")
    assert node.locator("pre").count() == 0
    button.click()
    shown = node.locator("> pre.instruments-raw-text").inner_text()
    start = int(node.get_attribute("data-start"))
    end = int(node.get_attribute("data-end"))
    assert shown == js_slice(document["markdown"], start, end)
    assert "Fracción uno" in shown
    button.click()
    assert not node.locator("> pre.instruments-raw-text").is_visible()


def test_the_report_link_names_the_instrument_snapshot_version_and_excerpt(page):
    open_from_table(page, "alfa")
    link = page.locator(f'section[data-eid="{ALFA}art_1"] > .instruments-node-head a.instruments-report')
    assert link.get_attribute("title") == "Report a parsing problem"
    href = link.get_attribute("href")
    assert href.startswith(f"{REPO_URL}/issues/new?")
    query = parse_qs(urlparse(href).query)
    assert query["title"] == [f"md2akn: alfa 01-02-2020.md {ALFA}art_1"]
    body = query["body"][0]
    for line in ("Collection: leyes", "Key: alfa", "Name: LEY ALFA", "Snapshot: 01-02-2020.md",
                 "md2akn version: ", f"{ALFA}art_1", "What is wrong:"):
        assert line in body, line
    assert "?c=leyes&k=alfa#" + ALFA + "art_1" in unquote(body)
    assert " " not in href
    header = page.locator("header.instruments-head a.instruments-report").get_attribute("href")
    assert f"md2akn: alfa 01-02-2020.md" in unquote(header)
    assert page.locator("a.instruments-report").count() > 10


def test_raw_mode_shows_the_whole_document_with_every_excerpt_marked(page):
    open_from_table(page, "alfa")
    document = read_file_of_toy(page, "alfa")
    mode = page.locator('[role="radiogroup"][aria-labelledby="instruments-mode-label"]')
    assert mode.locator('[role="radio"][aria-checked="true"]').inner_text() == "Parsed"
    mode.locator('[role="radio"]', has_text="Raw").click()
    raw = page.locator("pre.instruments-rawdoc")
    assert raw.is_visible() and not page.locator(".instruments-document").is_visible()
    assert raw.inner_text().replace("\r", "") == document["markdown"]
    ranges = raw.locator("span.instruments-range")
    assert ranges.count() == len(document["units"])
    kinds = {r.get_attribute("data-unit-type") for r in ranges.all()}
    assert {"article", "heading", "loose", "preamble"} <= kinds
    assert ranges.first.get_attribute("data-excerpt")
    assert raw.locator("span.instruments-uncovered").count() == 0
    mode.locator('[role="radio"]', has_text="Parsed").click()
    assert page.locator(".instruments-document").is_visible() and not raw.is_visible()
    # Arrow keys move the radio like the Atlas' control.
    mode.locator('[role="radio"][aria-checked="true"]').press("ArrowRight")
    assert page.locator("pre.instruments-rawdoc").is_visible()


def test_a_character_in_no_excerpt_is_marked_uncovered_in_the_raw_view(browser, server):
    def doctor(page):
        def handler(route):
            response = route.fetch()
            data = response.json()
            data["markdown"] += "\n\nTEXT NOBODY OWNS\n"
            route.fulfill(response=response, json=data)
        page.route("**/files/leyes/beta.json", handler)

    page = open_page(browser, f"{server}?c=leyes&k=beta", before=doctor)
    page.wait_for_selector('.instruments-document[data-rendered="true"]')
    page.locator('[role="radio"]', has_text="Raw").click()
    assert "TEXT NOBODY OWNS" in page.locator("span.instruments-uncovered").first.inner_text()
    page.close()


def test_a_missing_file_is_a_message_and_the_page_stays_usable_and_retryable(browser, server):
    state = {"fail": True}

    def before(page):
        def handler(route):
            if state["fail"]:
                route.fulfill(status=404, body="")
            else:
                route.continue_()
        page.route("**/files/leyes/beta.json", handler)

    page = open_page(browser, server, before=before)
    open_from_table_unchecked(page, "beta")
    page.wait_for_function(
        "t => document.querySelector('.instruments-status')?.textContent === t", arg=NOT_AVAILABLE)
    assert page.locator("table.instruments-ranking").is_visible()
    state["fail"] = False
    open_from_table(page, "beta")
    assert title(page) == "LEY BETA"
    assert page.locator("p.instruments-status").is_visible() is False
    assert page.errors == []
    page.close()


def expand_outline(page):
    """Open every folded container of the outline (the deeper ones start folded)."""
    folds = page.locator('button.instruments-outline-fold[aria-expanded="false"]')
    while folds.count():
        folds.first.click()


def open_from_table_unchecked(page, key):
    row(page, key).locator("button.instruments-open").click()


def test_a_file_built_for_another_instrument_is_refused_with_a_message(browser, server):
    def before(page):
        def handler(route):
            response = route.fetch()
            data = response.json()
            data["meta"]["k"] = "other"
            route.fulfill(response=response, json=data)
        page.route("**/files/leyes/beta.json", handler)

    page = open_page(browser, server, before=before)
    open_from_table_unchecked(page, "beta")
    page.wait_for_function(
        "t => document.querySelector('.instruments-status')?.textContent.includes(t)",
        arg=OTHER_VERSION)
    assert page.locator("table.instruments-ranking").is_visible()
    assert page.errors == []
    page.close()


def test_a_nearest_text_whose_file_is_missing_says_so_and_stays_on_the_instrument(browser, server):
    def before(page):
        page.route("**/files/leyes/beta.json",
                   lambda route: route.fulfill(status=404, body=""))

    page = open_page(browser, server, before=before)
    open_from_table(page, "alfa")
    excerpt(page, f"{ALFA}art_1").locator("details.instruments-near summary").click()
    excerpt(page, f"{ALFA}art_1").locator("a.instruments-near-link").first.click()
    page.wait_for_function(
        "t => document.querySelector('.instruments-status')?.textContent === t", arg=NOT_AVAILABLE)
    assert title(page) == "LEY ALFA"
    assert page.errors == []
    page.close()


def test_the_visible_page_says_nothing_forbidden(page):
    open_from_table(page, "alfa")
    excerpt(page, f"{ALFA}art_1").locator("details.instruments-near summary").click()
    text = page.inner_text("#instruments")
    assert forbidden_in(text) == []
    page.locator('[role="radio"]', has_text="Raw").click()
    assert forbidden_in(page.inner_text("#instruments")) == []


@pytest.mark.parametrize("width", [1280, 375])
def test_the_page_fits_the_width_and_the_outline_collapses_on_a_phone(browser, server, width):
    page = open_page(browser, server, width=width, height=800)
    fits = lambda: page.evaluate(  # noqa: E731
        "document.documentElement.scrollWidth <= window.innerWidth")
    assert fits()
    open_from_table(page, "alfa")
    assert fits()
    outline = page.locator(".instruments-outline")
    links = outline.locator("> ul.instruments-outline-list")
    toggle = outline.locator("button.instruments-outline-toggle")
    if width == 375:
        assert toggle.is_visible() and not links.is_visible()
        toggle.click()
        assert links.is_visible()
        expand_outline(page)
        links.locator("button.instruments-outline-link", has_text="Art. 1").first.click()
        assert not links.is_visible()
        reading = page.locator(".instruments-reading").bounding_box()
        assert reading["width"] <= width
        page.locator('[role="radio"]', has_text="Raw").click()
        assert fits()
    else:
        assert not toggle.is_visible() and links.is_visible()
        box, document = outline.bounding_box(), page.locator(".instruments-document").bounding_box()
        assert 250 <= box["width"] <= 320 and document["width"] > 500
        assert box["x"] < document["x"]
        SCREENSHOT.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert page.errors == []
    page.close()


def read_file_of_toy(page, key):
    """The toy file of `key` as the page fetched it (served by the harness server)."""
    base = page.url.split("/harness.html")[0]
    response = page.request.get(f"{base}/instruments/files/leyes/{key}.json")
    assert response.ok
    return response.json()


# -- the rendered page ------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def rendered_site():
    quarto = find_quarto()
    if quarto is None:
        pytest.skip("Quarto not found: put it on PATH or install it under "
                    "~/.local/opt/quarto-<version>/bin/quarto (CI uses 1.9.38)")
    subprocess.run([quarto, "render", "pages/instruments.qmd"], cwd=WEBSITE, check=True,
                   capture_output=True, timeout=300)
    html = SITE / "pages" / "instruments.html"
    assert html.exists()
    for name in ("instruments.js", "instruments.css", "instruments.json"):
        assert (SITE / "pages" / "instruments" / name).read_bytes() \
            == (PAGES / "instruments" / name).read_bytes()
    httpd = serve(SITE)
    yield f"http://127.0.0.1:{httpd.server_address[1]}/pages/instruments.html"
    httpd.shutdown()
    httpd.server_close()


def test_the_rendered_page_has_no_navbar_entry_but_is_in_the_search_index_and_linked_from_the_atlas(
        rendered_site):
    html = (SITE / "pages" / "instruments.html").read_text(encoding="utf-8")
    navbar = html[html.index('<nav class="navbar'):html.index("</nav>")]
    assert 'href="../pages/atlas.html"' in navbar
    assert "instruments.html" not in navbar and "Instruments" not in navbar
    search_index = json.loads((SITE / "search.json").read_text(encoding="utf-8"))
    assert any(entry["href"].startswith("pages/instruments.html") for entry in search_index)
    # Rendering the Atlas is cheap next to the browser tests and proves the link opens it.
    subprocess.run([find_quarto(), "render", "pages/atlas.qmd"], cwd=WEBSITE, check=True,
                   capture_output=True, timeout=300)
    atlas = (SITE / "pages" / "atlas.html").read_text(encoding="utf-8")
    assert 'href="../pages/instruments.html"' in atlas or 'href="./instruments.html"' in atlas


def test_the_rendered_landing_fits_and_searches(browser, rendered_site):
    page = open_page(browser, rendered_site)
    assert page.locator("table.instruments-ranking tbody tr").count() == 50
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.fill("#instruments-search", "constitucion politica")
    page.wait_for_selector("#instruments-suggestions li")
    assert "CONSTITUCIÓN" in page.locator("#instruments-suggestions li").first.inner_text()
    # The prose is as wide as the application, never narrower, and a table in it scrolls.
    app = page.locator("#instruments").bounding_box()
    prose = page.locator(".instruments-prose").bounding_box()
    assert prose["width"] >= app["width"] - 2
    page.screenshot(path=str(RENDERED_SCREENSHOT), full_page=False)
    assert page.errors == []
    page.close()


@pytest.mark.skipif(not (FILES / "leyes" / "ccf.json").exists(),
                    reason="website/pages/instruments/files/ has not been installed")
def test_a_real_3589_article_document_renders_in_chunks_with_a_visible_outline(browser, rendered_site):
    page = open_page(browser, f"{rendered_site}?c=leyes&k=ccf#tit_PRIMERO__cap_I__art_1")
    page.wait_for_selector('.instruments-document[data-rendered="true"]', timeout=120000)
    outline = page.locator(".instruments-outline").bounding_box()
    document = page.locator(".instruments-document").bounding_box()
    assert outline["width"] > 200 and document["width"] > 400      # Quarto's grid rule (#247)
    assert page.locator("div.instruments-excerpt").count() > 3000
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []
    page.close()
