"""The website's Instruments page (issues #290, #297), in a real browser.

    uv run --group viz playwright install chromium     # once
    uv run --group viz pytest scripts/embeddings/tests/test_instruments_page.py -q

The page is a hand-written application (`website/pages/instruments/instruments.js`,
`instruments.css`) over the committed index `website/pages/instruments/instruments.json`
and the per-instrument files of issue #289, wrapped by `website/pages/instruments.qmd`.

Three layers, like `test_atlas_page.py`, whose helpers this module imports:

* static checks, always run: front matter, the mount block, the prose, the navbar,
  the committed index against the Atlas' own data. Since issue #297 the page is a reading
  page for an end user: none of the review apparatus of #290 (parse flags, badges, *Raw*
  mode, report links, excerpt tags, outline marks, marker lines) may come back, and these
  tests assert its absence;
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
the rendered ones also skip without a Quarto binary; the ones that open a real
document skip when `website/pages/instruments/files/` was not installed.
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

TITLE = "The Instruments of the Atlas"
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
    for needle in ("compared excerpts", "Nearest texts", "Compared", "Qwen3 Embedding 4B",
                   "atlas-instruments", "instruments.html?c=leyes&k=ccf#art_1",
                   "Diario Oficial de la Federación", "back button"):
        assert needle in flat, needle
    # Nothing of the review apparatus of issue #290 is explained to the reader any more.
    for word in ("flag", "Raw", "Akoma Ntoso", "badge", "parse", "md2akn", "Loose",
                 "Repeated number", "Number gap", "Over window"):
        assert word.lower() not in flat.lower(), word
    assert "<strong>" not in prose and "instruments-flag-meanings" not in prose


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
    for attribute in ("dataset.index", "dataset.files"):
        assert attribute in js
    assert "instruments/instruments.json" in js           # only as the documented default
    # `data-repo` stays in the mount block (pinned verbatim) and is no longer read: the
    # reports it fed were removed in issue #297, and the file comment says so.
    assert "dataset.repo" not in js and "data-repo" in js
    assert "https://github.com/INGEOTEC/LegalIA" not in js


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
    assert "warning" not in css and "#b45309" not in css       # no parse flag, no amber
    assert 'data-compared="true"' in css and "Compared" in css


def test_the_review_apparatus_of_issue_290_is_gone_from_the_script_and_the_stylesheet():
    js = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    for name in ("instruments-badge", "instruments-raw-toggle", "instruments-raw-text",
                 "instruments-rawdoc", "instruments-report", "instruments-summary",
                 "instruments-numbering", "instruments-flag", "instruments-tag",
                 "instruments-excerpt-bar", "instruments-mode", "instruments-mark",
                 "instruments-compared", "instruments-segment", "instruments-uncovered",
                 "instruments-range", "instruments-annotation", "instruments-frontmatter"):
        assert name not in js and name not in css, name
    for name in ("FLAG_ORDER", "FLAG_LABELS", "reportUrl", "reportLink", "setMode", "buildRaw",
                 "tagFor", "radiogroup", "flag_meanings", "data-unit-type"):
        assert name not in js, name
    # The marker lines are stripped on display with a documented copy of `scjn.outline.MARKER`.
    assert "stripMarkers" in js and "scjn.outline.MARKER" in js
    assert r"/^<!-- scjn:block .*? -->[ \t]*$(?:\r?\n)?/gm" in js


def test_the_marker_expression_is_a_copy_of_the_one_scjn_writes():
    """The page is build-less, so its regular expression is a copy of `scjn.outline.MARKER`:
    the same line grammar, with the attributes group made anonymous."""
    scjn_outline = pytest.importorskip("scjn.outline")
    line = '<!-- scjn:block orden=4 numero=0 kind=other label="RATIFICACIÓN Y DEPÓSITO" -->'
    assert scjn_outline.MARKER.match(line)
    copy = scjn_outline.MARKER.pattern.replace("(?P<attrs>.*?)", ".*?")
    assert f"/{copy}(?:" in APP_JS.read_text(encoding="utf-8")


def test_the_navbar_lists_the_instruments_page_right_after_the_atlas_and_the_page_ships(tmp_path):
    # By decision of the repository owner on 2026-10-22 (issue #297) the page, which had served
    # its purpose as a review tool for the parse, is a reading page listed after the Atlas.
    text = (WEBSITE / "_quarto.yml").read_text(encoding="utf-8")
    entries = re.findall(r"- href: (\S+)\n\s+text: (.+)", text)
    names = [name.strip() for _, name in entries]
    assert names.index("Instruments") == names.index("Atlas") + 1
    assert ("pages/instruments.qmd", "Instruments") in [(h, n.strip()) for h, n in entries]
    resources = re.search(r"resources:\s*\n((?:\s+(?:-|#).*\n)+)", text).group(1)
    listed = [line.strip().lstrip("- ").strip('"') for line in resources.splitlines()
              if not line.strip().startswith("#")]
    assert listed[:2] == ["pages/atlas/**", "pages/instruments/**"]
    render = [line for line in text[text.index("render:"):text.index("resources:")].splitlines()
              if line.strip().startswith("-")]
    assert render and not any("instruments" in line for line in render)
    assert "draft" not in front_matter(QMD.read_text(encoding="utf-8"))
    assert "no navbar entry" not in text


def test_the_atlas_page_ends_with_a_link_to_the_instruments_page():
    atlas = (PAGES / "atlas.qmd").read_text(encoding="utf-8")
    assert atlas.count("(instruments.qmd)") == 1
    link = atlas.index("(instruments.qmd)")
    assert link > atlas.index("{{< include atlas/_gold-links.md >}}")      # after the callout
    assert link > atlas.index("::: {.atlas-prose}")
    tail = atlas[link:]
    assert tail.rstrip().endswith(":::") and tail.count("\n:::") == 1      # inside .atlas-prose
    paragraph = atlas[:link].rsplit("\n\n", 1)[1] + tail.split("\n\n", 1)[0]
    assert "article by article" in paragraph and "nearest texts" in paragraph
    assert "parse" not in paragraph and "find the places" not in paragraph
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

def test_the_landing_lists_every_instrument_by_name_with_collection_date_and_articles(page):
    assert page.locator("#instruments").get_attribute("data-view") == "landing"
    rows = page.locator("table.instruments-ranking tbody tr")
    assert rows.count() == 5
    caption = page.locator(".instruments-caption").inner_text()
    assert "5 instruments" in caption and "name opens" in caption
    headers = page.locator("table.instruments-ranking thead th").all_inner_texts()
    assert headers == ["Instrument", "Collection", "Text as of", "Articles"]
    # By name, ascending: no flag, score, excerpt or compared column.
    assert page.locator('th[data-key="n"]').get_attribute("aria-sort") == "ascending"
    names = [e.inner_text() for e in page.locator("tbody td.instruments-name").all()]
    assert names == sorted(names)
    first = rows.first.locator("td").all_inner_texts()
    assert first == ["LEY ALFA", "Law", "1 February 2020", first[3]]
    assert first[3].replace(",", "").isdigit()
    assert not page.locator(".instruments-view").is_visible()


def test_a_header_sorts_and_a_second_click_reverses_it(page):
    name = page.locator('th[data-key="n"] button')
    names = lambda: [e.inner_text() for e in page.locator("tbody td.instruments-name").all()]  # noqa: E731
    ascending = names()
    name.click()
    assert page.locator('th[data-key="n"]').get_attribute("aria-sort") == "descending"
    assert names() == ascending[::-1]
    page.locator('th[data-key="a"] button').click()
    counts = [int(e.inner_text().replace(",", ""))
              for e in page.locator("tbody tr td:nth-child(4)").all()]
    assert counts == sorted(counts, reverse=True)
    page.locator('th[data-key="t"] button').click()
    assert page.locator('th[data-key="t"]').get_attribute("aria-sort") == "descending"
    page.locator('th[data-key="c"] button').click()
    kinds = [e.inner_text() for e in page.locator("tbody tr td:nth-child(2)").all()]
    assert kinds == sorted(kinds)


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

def test_opening_an_instrument_shows_its_header_outline_and_document(page):
    open_from_table(page, "alfa")
    assert page.url.endswith("?c=leyes&k=alfa")
    assert title(page) == "LEY ALFA"
    head = page.locator("header.instruments-head")
    facts = head.locator(".instruments-facts").inner_text()
    document = read_file_of_toy(page, "alfa")
    assert facts.startswith("Abbreviation alfa")
    assert "text as of 1 February 2020" in facts and facts.rstrip().endswith("articles")
    # No snapshot file name, no parser version, no counts of excerpts.
    for word in ("01-02-2020", "md2akn", "excerpts", "compared", ".md"):
        assert word not in facts, word
    assert head.locator(".instruments-collection").inner_text() == "Law"
    assert not page.locator(".instruments-ranking").is_visible()
    assert page.locator(".instruments-summary, .instruments-mode").count() == 0
    outline = page.locator('div[role="complementary"].instruments-outline')
    assert outline.is_visible()
    links = outline.locator("button.instruments-outline-link")
    assert links.count() >= 6
    assert page.locator(".instruments-document section[data-eid]").count() >= 10
    assert document["meta"]["k"] == "alfa"


def test_the_facts_of_a_treaty_name_the_scjn_id(browser, server):
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    facts = page.locator(".instruments-facts").inner_text()
    assert facts.startswith("SCJN id 700") and "text as of" in facts
    assert page.locator(".instruments-collection").inner_text() == "Treaty"
    page.close()


def test_containers_are_headings_and_articles_are_paragraphs_under_them(page):
    open_from_table(page, "alfa")
    title_node = page.locator('section[data-eid="tit_PRIMERO"]')
    assert title_node.get_attribute("data-type") == "title"
    heading = title_node.locator("> h3.instruments-heading")
    assert heading.count() == 1 and heading.inner_text().strip()
    chapter = page.locator('section[data-eid="tit_PRIMERO__cap_I"]')
    assert chapter.locator("> h4.instruments-heading").count() == 1
    # Every container the toy law has opens with a heading element h3 to h6, never a paragraph.
    for node in page.locator('section[data-type="title"], section[data-type="chapter"]').all():
        assert node.locator("> h3, > h4, > h5, > h6").count() == 1
    article = page.locator(f'section[data-eid="{ALFA}art_1"]')
    assert article.get_attribute("data-type") == "article"
    # The text is the Markdown rendered with bold only.
    assert "Texto compartido del primero." in article.inner_text()
    assert article.locator("strong").first.inner_text().startswith("Artículo 1")
    assert article.locator("h3, h4, h5, h6").count() == 0
    paragraph = page.locator('section[data-type="paragraph"]').first
    assert paragraph.inner_text().strip()
    # Under the root a heading is an h3, and none is deeper than an h6.
    assert page.locator(".instruments-document h1, .instruments-document h2, "
                        ".instruments-document h7").count() == 0


def test_no_review_apparatus_is_on_an_opened_instrument(page):
    open_from_table(page, "alfa")
    for name in ("badge", "raw-toggle", "raw-text", "rawdoc", "report", "summary", "flag",
                 "flag-tag", "tag", "excerpt-bar", "mode", "mark", "compared", "segmented",
                 "uncovered", "range", "node-head"):
        assert page.locator(f".instruments-{name}").count() == 0, name
    assert page.locator("a[href*='issues/new']").count() == 0
    assert page.locator("[data-unit-type]").count() == 0
    text = page.inner_text("#instruments")
    for word in ("Raw", "Loose", "Repeated number", "Number gap", "Over window", "Score",
                 "md2akn", "Akoma", "report"):
        assert word not in text, word


def test_no_marker_line_and_no_identifier_is_visible_in_an_instrument(browser, server):
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    document = read_file_of_toy(page, "700", "tratados")
    assert "<!-- scjn:block" in document["markdown"]            # the file keeps them
    shown = page.locator(".instruments-document").text_content()
    assert "<!--" not in shown and "scjn:block" not in shown
    for eid in ("hc_DECRETO_PROMULGATORIO", "cap_3__att_3-A", "att_I__app_1", "sec_RESOLUCI"):
        assert eid not in shown, eid
    assert "El Plenipotenciario firmó el Tratado." in shown     # the text of the blocks stays
    assert "<!--" not in page.locator(".instruments-outline").text_content()
    titles = [b.get_attribute("title") or ""
              for b in page.locator(".instruments-outline button.instruments-outline-link").all()]
    assert titles and not any("__" in t or "art_" in t or "hc_" in t for t in titles)
    assert page.errors == []
    page.close()


def test_an_introduction_is_a_compared_excerpt_with_an_outline_entry_and_nearest_texts(page):
    open_from_table(page, "alfa")
    intro = page.locator(f'section[data-eid="{ALFA}intro"]')
    assert intro.get_attribute("data-type") == "intro"
    wrapper = excerpt(page, f"{ALFA}intro")
    assert wrapper.get_attribute("data-compared") == "true"
    assert "Texto suelto antes de los artículos." in wrapper.inner_text()
    assert wrapper.locator("> details.instruments-near").count() == 1
    expand_outline(page)
    link = page.locator(".instruments-outline button.instruments-outline-link",
                        has_text="Introduction").first
    assert link.locator("*").count() == 0                       # no mark, no dot
    link.click()
    assert page.locator(".instruments-target").get_attribute("data-eid").endswith("__intro")


def test_a_table_is_drawn_as_an_html_table_in_parsed_mode_with_its_rows_and_cells(page):
    open_from_table(page, "alfa")
    node = page.locator(f'section[data-eid="{ALFA}intro__table_1"]')
    assert node.get_attribute("data-type") == "table"
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
    assert wrapper.get_attribute("data-compared") == "true"
    assert wrapper.locator("> details.instruments-near").count() == 1


def test_a_table_inside_an_article_is_drawn_as_one_html_table_and_the_article_stays_one_excerpt(page):
    """Review fix-6 of #291: a table an article prints is not a node of the tree (the article
    is one excerpt whatever it holds), but its rows are not paragraphs either: the page draws
    the run of rows as one HTML table, for display only."""
    open_from_table(page, "alfa")
    article = excerpt(page, f"{ALFA}art_5")
    assert article.get_attribute("data-compared") == "true"
    tables = article.locator("table.instruments-table")
    assert tables.count() == 1
    rows = tables.locator("tr")
    assert rows.count() == 3
    assert [c.inner_text() for c in rows.nth(0).locator("td").all()] == ["Tramo", "Tasa"]
    assert rows.nth(1).locator("td").nth(1).locator("strong").inner_text() == "1.5"
    # The rows are not also printed as paragraphs, and the article is still one excerpt.
    assert article.locator("p.instruments-paragraph", has_text="Hasta 100").count() == 0
    assert page.locator(f'.instruments-excerpt[data-excerpt^="{ALFA}art_5"]').count() == 1


def test_a_long_table_is_one_html_table_per_excerpt_each_addressable_by_its_piece(browser, server):
    chapter = "tit_PRIMERO__cap_II__intro__table_1"
    page = open_page(browser, f"{server}?c=leyes&k=alfa#{chapter}.2")
    wait_rendered(page)
    node = page.locator(f'section[data-eid="{chapter}"]')
    first, second = excerpt(page, f"{chapter}.1"), excerpt(page, f"{chapter}.2")
    assert first.count() == second.count() == 1
    for piece in (first, second):
        assert piece.get_attribute("data-compared") == "true"
        assert piece.locator("table.instruments-table").count() == 1
        assert piece.locator("> details.instruments-near").count() == 1
    # Every one of the thirty rows is on the page exactly once, in order.
    labels = [c.inner_text() for c in node.locator("tr td:first-child").all()]
    assert labels == [f"fila {n:02d}" for n in range(1, 31)]
    assert first.locator("tr").count() + second.locator("tr").count() == 30
    assert first.locator("tr").first.locator("td").first.inner_text() == "fila 01"
    # The address names the piece, and the page marks that piece, not the whole table.
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == f"{chapter}.2"
    assert page.errors == []
    page.close()


def test_every_unit_of_the_toy_file_is_addressable_once_it_is_rendered(browser, server):
    page = open_page(browser, f"{server}?c=leyes&k=alfa")
    wait_rendered(page)
    document = read_file_of_toy(page, "alfa")
    ids = {(f"{u['e']}.{u['p']}" if u.get("p") else u["e"]) for u in document["units"]}
    # An excerpt is a wrapper, or a heading element addressed through its node.
    present = set(page.evaluate(
        "Array.from(document.querySelectorAll('[data-excerpt]'), e => e.dataset.excerpt)"))
    nodes = set(page.evaluate(
        "Array.from(document.querySelectorAll('section[data-eid]'), e => e.dataset.eid)"))
    assert {i for i in ids if i not in present} <= nodes
    assert page.locator(
        '.instruments-excerpt[data-excerpt="tit_PRIMERO__cap_II__intro__table_1.2"]').count() == 1
    assert page.errors == []
    page.close()


def test_only_compared_excerpts_are_marked_and_carry_nearest_texts(page):
    open_from_table(page, "alfa")
    document = read_file_of_toy(page, "alfa")
    searched = sum(1 for u in document["units"] if u.get("x"))
    assert searched > 5
    assert page.locator('[data-compared="true"]').count() == searched
    assert page.locator("details.instruments-near").count() == searched
    # Every marked excerpt carries the disclosure, and nothing else does.
    assert page.locator('[data-compared="true"] > details.instruments-near').count() == searched
    assert page.locator('div.instruments-excerpt:not([data-compared]) details').count() == 0
    transitory = excerpt(page, "sec_transitorios__art_PRIMERO")
    assert transitory.count() == 1 and transitory.locator("details").count() == 0
    assert transitory.get_attribute("data-compared") is None
    # A heading is not an excerpt wrapper: it is an element of its node, with no mark.
    assert page.locator('section[data-eid="tit_PRIMERO"] > h3').count() == 1
    assert page.locator('div.instruments-excerpt[data-excerpt="tit_PRIMERO"]').count() == 0
    summary = page.locator('[data-compared="true"]').first.locator("> details.instruments-near summary")
    assert re.fullmatch(r"Nearest texts \(\d+\)", summary.inner_text())


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
    assert link.get_attribute("title") == "Article 1"            # a label, never the eId
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


def test_a_navigation_puts_the_start_of_its_target_at_the_top_of_the_window(browser, server):
    """Review fix-1 of #297: `mark` used to centre its target, which leaves the middle of an
    element taller than the window on screen; the start of the target is what must land at the top
    (with a small scroll margin for its outline), for the outline and for a deep link alike."""
    page = open_page(browser, f"{server}?c=leyes&k=alfa", height=400)
    wait_rendered(page)
    expand_outline(page)
    page.locator(".instruments-outline button.instruments-outline-link",
                 has_text="Art. 5").click()
    target = page.locator(".instruments-target")
    assert target.get_attribute("data-eid") == f"{ALFA}art_5"
    page.wait_for_timeout(100)
    top = target.bounding_box()["y"]
    assert 0 <= top <= 40, top
    # A node near the end of the document, well below the fold from the top.
    page.evaluate("window.scrollTo(0, 0)")
    page.locator(".instruments-outline button.instruments-outline-link",
                 has_text="Capítulo").last.click()
    last = page.locator(".instruments-target")
    assert last.get_attribute("data-eid").startswith("tit_")
    assert 0 <= last.bounding_box()["y"] <= 40
    # A deep link opens at the same place.
    deep = open_page(browser, f"{server}?c=leyes&k=alfa#{ALFA}art_5", height=400)
    deep.wait_for_selector('.instruments-document[data-rendered="true"]')
    deep.wait_for_timeout(100)
    assert 0 <= deep.locator(".instruments-target").bounding_box()["y"] <= 40
    assert page.errors == [] and deep.errors == []
    deep.close()
    page.close()


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


def test_the_outline_scrolls_to_a_node_and_has_no_marks(page):
    open_from_table(page, "alfa")
    outline = page.locator(".instruments-outline")
    link = outline.locator("button.instruments-outline-link", has_text="Art. 5")
    assert not link.is_visible()                                  # below the first level: folded
    expand_outline(page)
    assert link.locator("*").count() == 0 and "⚑" not in outline.inner_text()
    assert link.get_attribute("title") == "Artículo 5"       # a label, never the eId
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


def outline_entry(outline, label):
    """The `li` of the outline whose own button reads `label`."""
    return outline.locator("li.instruments-outline-item", has=outline.page.locator(
        f'xpath=./button[contains(@class, "instruments-outline-link")][normalize-space(.)="{label}"]'
    )).first


def test_the_outline_nests_an_annex_under_its_chapter_and_appendices_under_their_annex(browser, server):
    """Review fix-3 of #291: the toy treaty has a chapter with two annexes (`3-A`, `3-B`), then
    an annex with no owner that holds two appendices. The outline mirrors the tree and names
    each by its kind, from `refersTo` and never from the eId."""
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    outline = page.locator(".instruments-outline")
    expand_outline(page)
    names = [b.inner_text().strip() for b in outline.locator("button.instruments-outline-link").all()]
    assert "Annex 3-A" in names and "Annex 3-B" in names and "Annex I" in names
    assert "Appendix 1" in names and "Appendix 2" in names
    assert not any(n.startswith("Attachment") for n in names)

    def children_of(label):
        item = outline_entry(outline, label)
        return [b.inner_text().strip() for b in item.locator(
            "xpath=./ul/li/button[contains(@class, 'instruments-outline-link')]").all()]

    assert children_of("Capítulo 3") == ["Annex 3-A", "Annex 3-B"]
    assert children_of("Annex I") == ["Introduction", "Appendix 1", "Appendix 2"]
    # collapsing and expanding work at every level
    annex = outline_entry(outline, "Annex I")
    fold = annex.locator("xpath=./button[contains(@class, 'instruments-outline-fold')]")
    inner = annex.locator("xpath=./ul")
    assert inner.is_visible()
    fold.click()
    assert not inner.is_visible()
    fold.click()
    assert inner.is_visible()
    # the document carries the same nesting and the same kinds
    nested = page.locator('section[data-eid="cap_3__att_3-A"]')
    assert nested.get_attribute("data-type") == "attachment"
    app = page.locator('section[data-eid="att_I__app_1"]')
    assert app.get_attribute("data-type") == "attachment"
    assert page.errors == []
    page.close()


def test_the_outline_nests_a_resolutions_annex_chapters_and_parts(browser, server):
    """Review fix-5 of #291: a block is the root of its hierarchy. The toy treaty's resolution
    shows its introduction and its annex; the annex holds its chapters and each chapter its
    parts, and nothing of the block hangs from the instrument itself."""
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    outline = page.locator(".instruments-outline")
    expand_outline(page)

    def children_of(label):
        item = outline_entry(outline, label)
        return [b.inner_text().strip() for b in item.locator(
            "xpath=./ul/li/button[contains(@class, 'instruments-outline-link')]").all()]

    assert children_of("Sección RESOLUCIÓN MSC.1(1)") == ["Introduction", "Annex"]
    assert children_of("Annex") == ["Capítulo II-1", "Capítulo II-2"]
    assert children_of("Capítulo II-1") == ["Part A", "Part B"]
    assert children_of("Capítulo II-2") == ["Part A"]
    nested = page.locator('section[data-eid="sec_RESOLUCIÓN_MSC.1(1)__att_attachment"]')
    assert nested.locator('section[data-eid="sec_RESOLUCIÓN_MSC.1(1)__att_attachment__cap_II-2'
                          '__part_A"]').count() == 1
    assert page.errors == []
    page.close()


def test_the_outline_names_every_labelled_block_by_its_role(browser, server):
    """Review fix-4 of #291: a block the Court labels `other` is an `hcontainer` of the tree, and
    the outline shows it, named in English by its `refersTo` (the promulgation decree's blocks
    grouped under one entry, a note, the closing formulas) or by the Court's own label."""
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    outline = page.locator(".instruments-outline")
    expand_outline(page)
    names = [b.inner_text().strip() for b in outline.locator("button.instruments-outline-link").all()]
    assert "Note: NOTA EXPLICATIVA" in names
    assert "Block: RESERVAS AL TRATADO" in names
    assert "Promulgation decree" in names
    assert not any(n.startswith("hcontainer") for n in names)
    # the two steps of the decree sit inside its one entry, each named by its own label
    decree = outline_entry(outline, "Promulgation decree")
    inner = [b.inner_text().strip() for b in decree.locator(
        "xpath=./ul/li/button[contains(@class, 'instruments-outline-link')]").all()]
    assert inner == ["Block: FIRMA", "Block: PROMULGACIÓN"]
    # it folds like every container, and the document carries the same nesting
    fold = decree.locator("xpath=./button[contains(@class, 'instruments-outline-fold')]")
    nested = decree.locator("xpath=./ul")
    assert nested.is_visible()
    fold.click()
    assert not nested.is_visible()
    fold.click()
    assert nested.is_visible()
    node = page.locator('section[data-eid="hc_DECRETO_PROMULGATORIO"]')
    assert node.get_attribute("data-type") == "hcontainer"
    assert node.locator('section[data-eid="hc_DECRETO_PROMULGATORIO__hc_FIRMA"]').count() == 1
    assert page.errors == []
    page.close()


def test_a_block_the_court_labels_but_prints_no_heading_for_shows_its_label_once(browser, server):
    """Review fix-1 of #301: some nodes get their name only from the Court's outline marker
    (`ARREGLO PROVISIONAL`, `FIRMA`), with no heading line of their own in the text. The
    document draws that label as a heading at the node's depth, styled as an editorial label
    with a `title`; a node whose own text already starts with its heading line (a
    `CONSIDERANDO` block that prints `**CONSIDERANDO**`, a chapter) is not given a second one,
    and the group of decree blocks, whose name is ours, gets none."""
    page = open_page(browser, f"{server}?c=tratados&k=700")
    wait_rendered(page)
    labels = page.locator(".instruments-document .instruments-label")
    shown = [label.inner_text().strip() for label in labels.all()]
    assert "ARREGLO PROVISIONAL" in shown
    for expected in ("RESERVAS AL TRATADO", "FIRMA", "PROMULGACIÓN", "NOTA EXPLICATIVA"):
        assert expected in shown, expected
    assert "CONSIDERANDO" not in shown            # its own first line is already that heading
    assert "DECRETO PROMULGATORIO" not in shown   # the group's name is md2akn's, not the Court's
    assert len(shown) == len(set(shown))          # once each
    first = labels.filter(has_text="ARREGLO PROVISIONAL").first
    assert first.get_attribute("title") == "Section label from the SCJN's database"
    assert first.evaluate("e => e.tagName") in {"H3", "H4", "H5", "H6"}
    # the label heads its node, ahead of the text that follows it
    node = page.locator('section[data-eid="hc_ARREGLO_PROVISIONAL"]')
    assert node.locator(":scope > .instruments-label").count() == 1
    assert "El arreglo provisional que las Partes aplican" in node.inner_text()
    # no label heading repeats a heading the text itself prints
    texts = [t.strip() for t in page.locator(".instruments-document .instruments-heading").all_inner_texts()]
    assert len([t for t in texts if t == "CAPÍTULO 3"]) <= 1
    assert "<!--" not in page.locator(".instruments-document").inner_text()
    assert page.errors == []
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
    else:
        assert not toggle.is_visible() and links.is_visible()
        box, document = outline.bounding_box(), page.locator(".instruments-document").bounding_box()
        assert 250 <= box["width"] <= 320 and document["width"] > 500
        assert box["x"] < document["x"]
        SCREENSHOT.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert page.errors == []
    page.close()


def read_file_of_toy(page, key, collection="leyes"):
    """The toy file of `key` as the page fetched it (served by the harness server)."""
    base = page.url.split("/harness.html")[0]
    response = page.request.get(f"{base}/instruments/files/{collection}/{key}.json")
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


def test_the_rendered_page_is_in_the_navbar_after_the_atlas_the_search_index_and_the_atlas_links_it(
        rendered_site):
    html = (SITE / "pages" / "instruments.html").read_text(encoding="utf-8")
    navbar = html[html.index('<nav class="navbar'):html.index("</nav>")]
    links = re.findall(r'<a class="nav-link[^"]*" href="([^"]+)"', navbar)
    assert links.index("../pages/instruments.html") == links.index("../pages/atlas.html") + 1
    assert "Instruments" in navbar
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


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    """The real script, stylesheet, index and installed files, served the way the toy site
    is, with no Quarto: the browser tests over the 2,676 documents need only these."""
    root = tmp_path_factory.mktemp("instruments-real-site")
    folder = root / "instruments"
    folder.mkdir()
    for name in ("instruments.js", "instruments.css", "instruments.json"):
        shutil.copy(PAGES / "instruments" / name, folder / name)
    (folder / "files").symlink_to(FILES)
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
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/harness.html"
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.skipif(not (FILES / "leyes" / "ligie-2022.json").exists(),
                    reason="website/pages/instruments/files/ has not been installed")
def test_ligie_shows_its_section_introduction_and_its_tariff_as_html_tables(browser, real_server):
    """Issue #294's acceptance: the real LIGIE, parsed by md2akn 0.5.1, has `sec_I__intro` as
    an introduction and the tariff of each chapter as tables, in pieces, each addressable."""
    page = open_page(browser, f"{real_server}?c=leyes&k=ligie-2022#sec_I__intro")
    wait_rendered(page)
    intro = page.locator('section[data-eid="sec_I__intro"]')
    assert intro.get_attribute("data-type") == "intro"
    assert excerpt(page, "sec_I__intro").get_attribute("data-compared") == "true"
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == "sec_I__intro"
    document = json.loads((FILES / "leyes" / "ligie-2022.json").read_text(encoding="utf-8"))
    tables = [u for u in document["units"] if u["u"] == "table"]
    assert len(tables) > 100
    assert page.locator("section.instruments-type-table").count() \
        == len({u["e"] for u in tables})
    wanted = {f"{u['e']}.{u['p']}" if u.get("p") else u["e"] for u in tables}
    present = set(page.evaluate(
        "Array.from(document.querySelectorAll('div.instruments-excerpt'), e => e.dataset.excerpt)"))
    assert wanted <= present
    cells = page.locator("table.instruments-table tr").first.locator("td").count()
    assert cells >= 2
    # The tariff is no longer made of loose paragraphs and, since review fix-6 of #291, no
    # excerpt of the law is `loose` at all (the transitory text it used to leave is placed).
    assert not document["meta"]["flags"].get("loose")
    piece = next(u for u in tables if u.get("p", 0) == 2)
    page.goto(f"{real_server}?c=leyes&k=ligie-2022#{piece['e']}.2")
    wait_rendered(page)
    assert page.locator(".instruments-target").get_attribute("data-excerpt") == f"{piece['e']}.2"
    assert page.errors == []
    page.close()


@pytest.mark.skipif(not (FILES / "tratados" / "137355.json").exists(),
                    reason="website/pages/instruments/files/ has not been installed")
def test_the_tmec_shows_decimal_article_numbers_and_no_repeated_number_from_a_split_lead(
        browser, real_server):
    page = open_page(
        browser,
        f"{real_server}?c=tratados&k=137355#cap_1__sec_A__art_1.1")
    wait_rendered(page)
    document = json.loads((FILES / "tratados" / "137355.json").read_text(encoding="utf-8"))
    eids = [u["e"] for u in document["units"] if u["u"] == "article"]
    assert [e.split("__")[-1] for e in eids if "__art_1." in e][:3] == ["art_1.1", "art_1.2", "art_1.3"]
    article = page.locator('section[data-eid="cap_1__sec_A__art_1.1"]')
    assert article.get_attribute("data-type") == "article"
    assert "1.1" in article.inner_text()
    assert page.locator(".instruments-target").count() == 1
    # Review fix-1 of #291: the annex articles (`Artículo 2.A.1`) are read whole too now (the
    # writer used to bold only `Artículo 2.`), so no article of the treaty carries the repeated
    # number flag, neither a decimal one nor an annex one.
    flagged = [u["e"] for u in document["units"] if "duplicate_number" in u.get("f", [])]
    assert flagged == []
    assert any(re.search(r"__art_\d+\.[A-Z]\.\d+$", e) for e in eids)
    assert page.errors == []
    page.close()


@pytest.mark.skipif(not (FILES / "leyes" / "ccf.json").exists(),
                    reason="website/pages/instruments/files/ has not been installed")
def test_the_real_civil_code_reads_as_a_document_with_nearest_texts(browser, rendered_site):
    page = open_page(browser, f"{rendered_site}?c=leyes&k=ccf#tit_PRIMERO__cap_I__art_1")
    page.wait_for_selector('.instruments-document[data-rendered="true"]', timeout=120000)
    outline = page.locator(".instruments-outline").bounding_box()
    document = page.locator(".instruments-document").bounding_box()
    assert outline["width"] > 200 and document["width"] > 400      # Quarto's grid rule (#247)
    assert page.locator("div.instruments-excerpt").count() > 3000
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    facts = page.locator(".instruments-facts").inner_text()
    assert facts.startswith("Abbreviation ccf") and "14 November 2025" in facts
    assert re.search(r"[\d,]+ articles$", facts.strip())
    assert_reads_as_a_document(page)
    first = page.locator('[data-compared="true"]').first
    summary = first.locator("> details.instruments-near summary")
    assert summary.inner_text() == "Nearest texts (5)"
    summary.click()
    items = first.locator("li.instruments-near-item")
    items.first.wait_for()
    assert items.count() == 5
    for item in items.all():
        assert re.fullmatch(r"\d\.\d{3}", item.locator(".instruments-similarity").inner_text())
        assert " — " in item.locator("a.instruments-near-link").first.inner_text()
    assert page.errors == []
    page.close()


def assert_reads_as_a_document(page):
    """No review apparatus, no marker line and no identifier in a fully rendered instrument."""
    for name in ("badge", "raw-toggle", "report", "summary", "flag", "tag", "excerpt-bar",
                 "mode", "mark", "compared"):
        assert page.locator(f".instruments-{name}").count() == 0, name
    shown = page.locator(".instruments-document").text_content()
    assert "<!--" not in shown and "scjn:block" not in shown
    assert not re.search(r"\b(?:art|cap|tit|sec|hc|att|app)_[\w.-]+__", shown)
    assert page.locator(".instruments-document h3").count() > 0


def a_treaty_with_decree_blocks():
    for path in sorted((FILES / "tratados").glob("*.json")):
        if '"hc_DECRETO_PROMULGATORIO"' in path.read_text(encoding="utf-8"):
            return path.stem
    return None


@pytest.mark.skipif(not (FILES / "tratados").exists() or a_treaty_with_decree_blocks() is None,
                    reason="website/pages/instruments/files/ has not been installed")
def test_a_real_treaty_with_promulgation_decree_blocks_shows_no_marker_line(browser, real_server):
    key = a_treaty_with_decree_blocks()
    page = open_page(browser, f"{real_server}?c=tratados&k={key}")
    page.wait_for_selector('.instruments-document[data-rendered="true"]', timeout=120000)
    assert page.locator('section[data-eid="hc_DECRETO_PROMULGATORIO"]').count() == 1
    assert_reads_as_a_document(page)
    markdown = json.loads((FILES / "tratados" / f"{key}.json").read_text(encoding="utf-8"))["markdown"]
    assert "<!-- scjn:block" in markdown                            # the file keeps its markers
    assert page.errors == []
    page.close()


def treaty_11374_is_read_by_position():
    path = FILES / "tratados" / "11374.json"
    return path.exists() and "hc_DECRETO_PROMULGATORIO__hc_APROBACI" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(not treaty_11374_is_read_by_position(),
                    reason="website/pages/instruments/files/ has not been rebuilt for review fix-1 of #301")
def test_the_real_treaty_11374_shows_the_courts_labels_once(browser, real_server):
    """Review fix-1 of #301: the labels of 11374's blocks -- the decree's opening, its own
    preamble, the testimonium `FIRMA`, the closing clause and the provisional arrangement --
    are shown in the document, each once, and the group of decree blocks gets none."""
    page = open_page(browser, f"{real_server}?c=tratados&k=11374")
    page.wait_for_selector('.instruments-document[data-rendered="true"]', timeout=120000)
    shown = [label.inner_text().strip()
             for label in page.locator(".instruments-document .instruments-label").all()]
    for expected in ("APROBACIÓN DE INSTRUMENTOS", "PREÁMBULO", "FIRMA", "PARTE FINAL DEL ACTA",
                     "ARREGLO PROVISIONAL"):
        assert expected in shown, expected
    assert "DECRETO PROMULGATORIO" not in shown
    assert len([t for t in shown if t == "FIRMA"]) == 1
    assert_reads_as_a_document(page)
    assert page.errors == []
    page.close()
