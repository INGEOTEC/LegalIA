"""The website's Topics page and its exporter (issue #286).

    uv run --group viz pytest scripts/embeddings/tests/test_topics_page.py -q

`export_topics_page.py` turns #285's `topics/topics.json` into two committed files,
`website/pages/topics/topics.json` and `_topics.md`, which `website/pages/topics.qmd`
includes. The first half of this file runs the exporter over a toy work directory in
`tmp_path` (seven instruments over three collections, three clusters whose #285 ids
are not in size order, a name full of Markdown syntax). The second half checks the
committed files against each other and against `atlas.json`, the qmd's prose against
the committed data. Since review fix-2 the page is a draft the site does not render,
link or ship: the last section asserts that, statically and, when a local Quarto is found
the way `test_atlas_page.py` finds it, on the rendered site.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cluster_instruments  # noqa: E402
import export_topics_page as exporter  # noqa: E402
from test_atlas_page import (  # noqa: E402
    DATA as ATLAS_DATA, PAGES, SITE, WEBSITE, find_quarto, forbidden_in, front_matter,
    lead_phrases_in)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
QMD = PAGES / "topics.qmd"
TOPICS_DIR = PAGES / "topics"
DATA = TOPICS_DIR / "topics.json"
MARKDOWN = TOPICS_DIR / "_topics.md"

#: A name the Markdown would mangle: a link, an emphasis pair, a pair of straight
#: quotes, a double hyphen (an en dash in pandoc's smart typography), three dots (an
#: ellipsis), a pipe, and a line break the corpus really carries.
AWKWARD = 'REGLAMENTO [DEL] *CONSEJO* "A" -- ANTES B ... | C_D\r\n  FIN'

#: `(coleccion, clave, nombre)` per instrument, `i` = position.
TOY = [
    ("leyes", "lalfa", "LEY Alfa"),                 # 0
    ("leyes", "lbeta", "LEY Beta"),                 # 1
    ("reglamentos", "101", "REGLAMENTO Uno"),       # 2
    ("reglamentos", "102", AWKWARD),                # 3
    ("reglamentos", "103", "REGLAMENTO Tres"),      # 4
    ("tratados", "201", "CONVENIO Uno"),            # 5
    ("tratados", "202", "CONVENIO Dos"),            # 6
]

#: #285's clusters, as `topics.json` writes them: ids by *its* own order, members
#: as `[i, distance]` nearest first. Cluster 0 is the smaller one, cluster 1 the
#: larger, and `-1` is the noise.
RAW_TOPICS = [
    {"id": 0, "size": 2, "terms": ["convenio", "partes", "tratado"],
     "representatives": [[5, 0.1], [6, 0.2]],
     "members": [[5, 0.1], [6, 0.2]], "collections": {"tratados": 2}},
    {"id": 1, "size": 4, "terms": ["reglamento", "ley", "artículo", "articulo", "caso"],
     "representatives": [[4, 0.05], [0, 0.1], [2, 0.2]],
     # The law comes second by distance but its group comes first on the page; the
     # regulations keep their own order (4, 2, 3 by distance).
     "members": [[4, 0.05], [0, 0.1], [2, 0.2], [3, 0.3]],
     "collections": {"leyes": 1, "reglamentos": 3}},
    {"id": -1, "size": 1, "terms": ["resto"],
     "representatives": [[1, 0.0]], "members": [[1, 0.0]], "collections": {"leyes": 1}},
]

RAW_META = {
    "generated": "2026-10-12T00:00:00+00:00", "commit": "abc1234",
    "model": "Qwen/Qwen3-Embedding-0.6B", "method": "dense",
    "algorithm": "louvain", "parameter_name": "resolution", "parameter": 1.0,
    "selection": {"mode": "rule", "finished": 22, "passing_the_gates": 4},
    "gates": {"min_clusters": 20, "max_clusters": 80, "max_noise": 0.15, "max_largest": 0.25},
    "measures": {"clusters": 2, "noise": 1 / 7, "largest": 4 / 7, "silhouette": 0.1,
                 "comembership": 0.5},
    "gold_links": 4,
    "gold": {"path": "emb-run-atlas/gold-links/gold.json", "commit": "a6324cd9", "instruments": 7},
    "labels": {"method": "c-TF-IDF", "ngram_range": [1, 2], "min_instruments": 2,
               "top_terms": 10, "representatives": 3, "stopwords": ["de"], "vocabulary": 9},
}


@pytest.fixture
def toy_work(tmp_path):
    work_dir = tmp_path / "work"
    out = cluster_instruments.output_dir(work_dir)
    out.mkdir(parents=True)
    pq.write_table(pa.table({
        "i": pa.array(range(len(TOY)), type=pa.int32()),
        "coleccion": pa.array([row[0] for row in TOY]),
        "clave": pa.array([row[1] for row in TOY]),
        "nombre": pa.array([row[2] for row in TOY]),
    }), work_dir / "instruments.parquet")
    (out / "topics.json").write_text(
        json.dumps({"meta": RAW_META, "topics": RAW_TOPICS}), encoding="utf-8")
    (out / cluster_instruments.DONE_MARKER).write_text("done\n", encoding="utf-8")
    return work_dir


@pytest.fixture
def exported(toy_work, tmp_path):
    output = tmp_path / "site" / "topics"
    data = exporter.export(toy_work, output, now=NOW, commit="deadbee", log=lambda *a: None)
    return {"data": data, "output": output,
            "markdown": (output / "_topics.md").read_text(encoding="utf-8"),
            "json": json.loads((output / "topics.json").read_text(encoding="utf-8"))}


# -- the exporter, over the toy work directory ------------------------------ #

def test_a_work_directory_without_done_or_topics_is_refused_by_name(toy_work, tmp_path):
    (cluster_instruments.output_dir(toy_work) / ".done").unlink()
    with pytest.raises(SystemExit, match=r"\.done is missing"):
        exporter.export(toy_work, tmp_path / "x", log=lambda *a: None)
    (cluster_instruments.output_dir(toy_work) / ".done").write_text("done\n", encoding="utf-8")
    (cluster_instruments.output_dir(toy_work) / "topics.json").unlink()
    with pytest.raises(SystemExit, match=r"topics\.json is missing"):
        exporter.export(toy_work, tmp_path / "x", log=lambda *a: None)
    assert not (tmp_path / "x").exists()          # nothing is written on a refusal


def test_the_files_are_written_and_the_schema_is_meta_topics_instruments(exported):
    assert sorted(path.name for path in exported["output"].iterdir()) == [
        "_topics.md", "topics.json"]
    assert list(exported["json"]) == ["meta", "topics", "instruments"]
    meta = exported["json"]["meta"]
    assert meta["generated"] == NOW.isoformat(timespec="seconds") and meta["commit"] == "deadbee"
    assert (meta["algorithm"], meta["parameter_name"], meta["parameter"]) == (
        "louvain", "resolution", 1.0)
    assert meta["instruments"] == 7 and meta["topics"] == 2 and meta["noise_instruments"] == 1
    assert meta["collections"] == {"leyes": 2, "reglamentos": 3, "tratados": 2}
    assert meta["measures"] == RAW_META["measures"] and meta["gold_links"] == 4
    assert meta["gold"] == RAW_META["gold"]
    assert meta["model"] == "Qwen/Qwen3-Embedding-0.6B" and meta["method"] == "dense"
    assert exported["json"]["instruments"] == [list(row) for row in TOY]


def test_topics_are_numbered_by_size_from_one_and_the_noise_is_last(exported):
    topics = exported["json"]["topics"]
    assert [(t["id"], t["cluster"], t["size"]) for t in topics] == [
        (1, 1, 4), (2, 0, 2), (-1, -1, 1)]
    assert topics[0]["terms"] == RAW_TOPICS[1]["terms"]
    assert topics[0]["reps"] == [4, 0, 2] and topics[0]["members"] == [4, 0, 2, 3]
    assert topics[0]["by"] == {"leyes": 1, "reglamentos": 3}
    assert topics[2]["members"] == [1]


def test_every_instrument_is_in_exactly_one_topic(exported):
    members = [i for topic in exported["json"]["topics"] for i in topic["members"]]
    assert sorted(members) == list(range(7))


def test_an_instrument_in_two_topics_or_none_is_refused(toy_work, tmp_path):
    path = cluster_instruments.output_dir(toy_work) / "topics.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["topics"][0]["members"] = [[5, 0.1], [4, 0.2]]       # 4 twice, 6 nowhere
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SystemExit, match="exactly one topic"):
        exporter.export(toy_work, tmp_path / "x", log=lambda *a: None)


def test_the_summary_table_anchors_are_the_section_ids_and_its_sizes_add_up(exported):
    markdown = exported["markdown"]
    rows = re.findall(r"^\| \[([^\]]+)\]\(#([^)]+)\) \|(.*)$", markdown, re.M)
    assert [(label, target) for label, target, _ in rows] == [
        ("Topic 1", "topic-1"), ("Topic 2", "topic-2"),
        ("Instruments in no topic", "topic-none")]
    ids = re.findall(r"^### .* \{#([^}]+)\}$", markdown, re.M)
    assert ids == ["topic-1", "topic-2", "topic-none"]
    sizes = [int(rest.split("|")[1].replace(",", "")) for _, _, rest in rows]
    assert sizes == [4, 2, 1] and sum(sizes) == 7
    first = rows[0][2].split("|")
    assert [cell.strip() for cell in first] == [
        "reglamento · ley · artículo", "4", "1", "3", "0", "0", ""]


def test_the_markdown_is_led_by_a_generated_comment(exported):
    assert exported["markdown"].startswith("<!-- Generated by scripts/embeddings/export_topics_page.py")
    assert "do not edit" in exported["markdown"].splitlines()[0]


def sections_of(markdown: str) -> list[str]:
    return re.split(r"(?m)^(?=### )", markdown)[1:]


def test_each_section_has_the_heading_terms_sentence_representatives_and_callout(exported):
    first, second, noise = sections_of(exported["markdown"])
    assert first.startswith("### Topic 1: reglamento · ley · artículo {#topic-1}\n")
    assert "`reglamento` `ley` `artículo` `articulo` `caso`" in first
    assert "The topic holds 4 instruments, namely 1 law and 3 regulations." in first
    assert "Its three most representative instruments, from the nearest to the centre of the topic" in first
    bullets = re.findall(r"(?m)^- (.*), a (law|regulation|treaty)$", first)
    assert [kind for _, kind in bullets] == ["regulation", "law", "regulation"]
    assert '::: {.callout-note collapse="true" title="All 4 members"}' in first
    assert first.rstrip().endswith(":::")
    assert "The topic holds 2 instruments, namely 2 treaties." in second
    assert "Its two most representative instruments" in second
    assert noise.startswith("### Instruments in no topic {#topic-none}\n")
    assert "1 instrument belong to no topic, namely 1 law." in noise
    assert "Its most representative instrument, the nearest" in noise


def test_members_are_grouped_by_collection_and_ordered_by_distance(exported):
    first = sections_of(exported["markdown"])[0]
    callout = first[first.index("::: {.callout-note"):]
    assert re.findall(r"(?m)^\*\*(.*)\*\*$", callout) == ["Laws (1)", "Regulations (3)"]
    members = re.findall(r"(?m)^- (.*)$", callout)
    # The law first (its group is first), then the regulations by distance, the
    # nearest overall (4, the representative) heading its group.
    assert [member.rsplit(" `", 1)[1] for member in members] == [
        "lalfa`", "103`", "101`", "102`"]
    assert members[0].startswith("LEY Alfa `")


def test_a_name_is_written_literally(exported):
    awkward = next(line for line in exported["markdown"].splitlines()
                   if line.startswith("- ") and "`102`" in line and not line.endswith("a regulation"))
    assert awkward == (r'- REGLAMENTO \[DEL\] \*CONSEJO\* \"A\" \-\- ANTES B \.\.\. \| C\_D FIN `102`')


def test_escape_keeps_what_pandoc_would_rewrite():
    assert exporter.escape("a -- b") == r"a \-\- b"
    assert exporter.escape("a - b") == "a - b"
    assert exporter.escape("a ... b") == r"a \.\.\. b"
    assert exporter.escape("a. b..") == "a. b.."
    assert exporter.escape('"x"') == r'\"x\"'
    assert exporter.escape("a\n\r\n  b") == "a b"
    assert exporter.escape("[x](y) *z* _w_ <t> #h $") == r"\[x\](y) \*z\* \_w\_ \<t\> \#h \$"


def test_inline_code_survives_a_backtick():
    assert exporter.inline_code("abc") == "`abc`"
    assert exporter.inline_code("a`b") == "``a`b``"
    assert exporter.inline_code("`ab") == "`` `ab ``"


def test_the_composition_sentence_counts_in_words():
    phrase = exporter.count_phrase
    assert phrase({"leyes": 9, "reglamentos": 121, "lineamientos": 12}) == (
        "9 laws, 121 regulations and 12 guidelines")
    assert phrase({"leyes": 1, "tratados": 1}) == "1 law and 1 treaty"
    assert phrase({"tratados": 1_373}) == "1,373 treaties"
    assert phrase({"reglamentos": 0, "leyes": 2}) == "2 laws"


def test_a_second_export_is_identical_apart_from_generated(toy_work, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    exporter.export(toy_work, one, now=NOW, commit="c", log=lambda *a: None)
    exporter.export(toy_work, two, now=datetime(2030, 1, 1, tzinfo=timezone.utc), commit="c",
                    log=lambda *a: None)
    first = json.loads((one / "topics.json").read_text(encoding="utf-8"))
    second = json.loads((two / "topics.json").read_text(encoding="utf-8"))
    assert first["meta"].pop("generated") != second["meta"].pop("generated")
    assert first == second
    assert (one / "_topics.md").read_bytes() == (two / "_topics.md").read_bytes()
    assert not list(one.glob("*.parcial"))


def test_the_command_line_writes_into_output_dir(toy_work, tmp_path):
    assert exporter.main(["--work-dir", str(toy_work), "--output-dir", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "topics.json").exists()


# -- the committed files -------------------------------------------------------- #

@pytest.fixture(scope="module")
def committed():
    return json.loads(DATA.read_text(encoding="utf-8"))


def callouts(markdown: str) -> list[list[str]]:
    """Every collapsed member list's lines, one list per topic."""
    return [re.findall(r"(?m)^- .*$", block.split("\n:::\n")[0])
            for block in re.split(r'(?m)^::: \{\.callout-note collapse="true"[^\n]*\n', markdown)[1:]]


def test_the_committed_data_lists_the_atlas_instruments_at_every_i(committed):
    atlas = json.loads(ATLAS_DATA.read_text(encoding="utf-8"))["instruments"]
    assert len(committed["instruments"]) == len(atlas) == committed["meta"]["instruments"]
    for i, (triple, entry) in enumerate(zip(committed["instruments"], atlas)):
        assert triple == [entry["c"], entry["k"], entry["n"]], i


def test_every_instrument_is_in_exactly_one_committed_topic(committed):
    members = [i for topic in committed["topics"] for i in topic["members"]]
    assert sorted(members) == list(range(committed["meta"]["instruments"]))
    assert committed["meta"]["instruments"] == 2677
    topics = committed["topics"]
    assert [t["size"] for t in topics if t["id"] != -1] == sorted(
        (t["size"] for t in topics if t["id"] != -1), reverse=True)
    assert [t["id"] for t in topics if t["id"] != -1] == list(range(1, committed["meta"]["topics"] + 1))
    for topic in topics:
        assert topic["size"] == len(topic["members"]) == sum(topic["by"].values())
        assert len(topic["terms"]) == 10
        assert topic["reps"] == topic["members"][:min(3, topic["size"])]


def test_the_committed_markdown_is_the_committed_data(committed):
    markdown = MARKDOWN.read_text(encoding="utf-8")
    assert markdown == exporter.build_markdown(committed)
    instruments = committed["instruments"]
    lists = callouts(markdown)
    assert len(lists) == len(committed["topics"])
    for topic, lines in zip(committed["topics"], lists):
        expected = []
        for name in exporter.COLLECTIONS:
            expected += [exporter.member_line(instruments[i]) for i in topic["members"]
                         if instruments[i][0] == name]
        assert lines == expected, topic["id"]
        # Representatives lead their own group.
        for i in topic["reps"]:
            group = [j for j in topic["members"] if instruments[j][0] == instruments[i][0]]
            assert group.index(i) < len(topic["reps"])
    keys = [re.search(r"`([^`]+)`$", line).group(1) for lines in lists for line in lines]
    assert len(keys) == len(set(keys)) == 2677
    rows = re.findall(r"(?m)^\| \[[^\]]+\]\(#(topic-[^)]+)\) \|.*\| ([\d,]+) \| [\d,]+ \| [\d,]+ \| "
                      r"[\d,]+ \| [\d,]+ \|$", markdown)
    assert sum(int(size.replace(",", "")) for _, size in rows) == 2677
    assert [anchor for anchor, _ in rows] == re.findall(r"(?m)^### .* \{#(topic-[^}]+)\}$", markdown)


# -- the page's prose ---------------------------------------------------------------- #

def money(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f} %"


def test_the_qmd_has_the_agreed_front_matter_the_include_and_no_code(committed):
    text = QMD.read_text(encoding="utf-8")
    fields = front_matter(text)
    meta = committed["meta"]
    assert fields["title"] == "Topics of the Atlas of Mexican Federal Law"
    assert fields["subtitle"] == (f"{meta['instruments']:,} instruments in {meta['topics']} topics, "
                                  "named by the words that distinguish their excerpts")
    assert fields["page-layout"] == "article" and fields["toc"] == "true"
    assert fields["number-sections"] == "false"
    assert "```" not in text and "<script" not in text and "freeze" not in text
    assert text.count("{{< include topics/_topics.md >}}") == 1
    assert text.rstrip().endswith("{{< include topics/_topics.md >}}")


def test_the_qmd_prose_says_nothing_forbidden_and_has_no_lead_phrase_and_colon():
    text = QMD.read_text(encoding="utf-8")
    prose = text.split("\n---\n", 1)[1]
    assert forbidden_in(text) == []
    assert lead_phrases_in(prose) == []
    for word in ("export_topics_page", "cluster_instruments", "commit", ".py"):
        assert word not in text, word
    assert "excerpts" in text


def test_the_qmd_literals_agree_with_the_committed_data(committed):
    text = " ".join(QMD.read_text(encoding="utf-8").split())
    meta = committed["meta"]
    measures = meta["measures"]
    assert (meta["algorithm"], meta["parameter_name"], meta["parameter"]) == (
        "louvain", "resolution", 1.0)
    assert meta["selection"] == {"mode": "rule", "finished": 22, "passing_the_gates": 4}
    gates = meta["gates"]
    assert f"between {gates['min_clusters']} and {gates['max_clusters']} topics" in text
    assert f"at most {gates['max_noise'] * 100:.0f} % of the instruments in no topic" in text
    assert f"larger than a quarter" in text and gates["max_largest"] == 0.25
    assert f"{meta['selection']['finished']} configurations in all" in text
    assert f"a resolution of {meta['parameter']}" in text
    assert f"gives {meta['topics']} topics and leaves no instrument outside a topic" in text
    assert meta["noise_instruments"] == 0
    largest = max(t["size"] for t in committed["topics"])
    assert f"The largest topic has {largest} instruments, {money(measures['largest'])} of the total" in text
    links = meta["gold_links"]
    together = round(measures["comembership"] * links)
    assert abs(together / links - measures["comembership"]) < 1e-9
    assert f"{together} of the {links} strong links, {money(together / links)}," in text
    assert f"the {links} links in which an instrument names" in text
    assert f"The silhouette of the partition is {measures['silhouette']:.3f}" in text
    counts = meta["collections"]
    assert (f"{counts['leyes']} laws, {counts['reglamentos']} regulations, {counts['lineamientos']} "
            f"guidelines and {counts['tratados']:,} treaties") in text
    assert f"the same {meta['instruments']:,} unique instruments" in text
    assert f"The table lists the {meta['topics']} topics" in text and (
        f"add up to the {meta['instruments']:,} instruments" in text)
    assert measures["clusters"] == meta["topics"] == 30


FOUR_B_SECTION = "### Measured, topics, the 4B, 2026-10-26 (issue #301)"


def four_b_rows() -> list[dict]:
    """The 22 configurations of the 4B run, as the README's own table prints them
    (the work directory is gitignored, so the literals are checked where they were
    copied from; review fix-1 moved the page from the 0.6B run to this one)."""
    readme = (WEBSITE.parent / "scripts" / "embeddings" / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index(FOUR_B_SECTION):]
    section = section[:section.index("\n\n", section.index("| algorithm |"))]
    rows = re.findall(r"(?m)^\| (\w+) \| (\w+) (\d+(?:\.\d)?) \| (\d+) \| ([\d.]+)% \| ([\d.]+)% "
                      r"\| ([\d.]+) \| ([\d.]+) \| (pass|fail[^|]*) \|", section)
    return [{"algorithm": a, "parameter": float(v), "clusters": int(c), "noise": float(n),
             "largest": float(l), "silhouette": float(si), "comembership": float(co),
             "gates": g} for a, _, v, c, n, l, si, co, g in rows]


def test_the_qmd_comparison_claims_agree_with_the_4b_sweeps(committed):
    """Ward and k-means left a topic of 28 % to 67 % of the instruments; the
    density-based runs left 20 % to 35 % in no topic over a minimum of 5 to 30; only
    Louvain passed."""
    rows = four_b_rows()
    assert len(rows) == 22
    ward_kmeans = [r["largest"] for r in rows if r["algorithm"] in ("agglomerative", "kmeans")]
    assert 28 <= min(ward_kmeans) <= 29 and 66 <= max(ward_kmeans) <= 67     # 28 % to 67 %
    density = [r for r in rows if r["algorithm"] == "hdbscan_umap"]
    noise = [r["noise"] for r in density]
    assert len(noise) == 5 and 19 <= min(noise) <= 20 and 35 <= max(noise) <= 36   # 20 % to 35 %
    passing = [r for r in rows if r["gates"] == "pass"]
    assert {r["algorithm"] for r in passing} == {"louvain"} and len(passing) == 4
    meta = committed["meta"]
    assert meta["selection"]["passing_the_gates"] == len(passing)
    chosen = next(r for r in rows if r["algorithm"] == "louvain"
                  and r["parameter"] == meta["parameter"])
    measures = meta["measures"]
    assert (chosen["clusters"], f"{chosen['silhouette']:.3f}", f"{chosen['comembership']:.3f}") == (
        measures["clusters"], f"{measures['silhouette']:.3f}", f"{measures['comembership']:.3f}")
    assert chosen["largest"] == round(measures["largest"] * 100, 1)
    text = " ".join(QMD.read_text(encoding="utf-8").split())
    assert "from 28 % to 67 % of the instruments" in text
    assert "from 20 % to 35 % of the instruments in no topic" in text
    assert "from 5 to 30 instruments" in text


def test_the_committed_files_come_from_the_4b_matrix(committed):
    assert committed["meta"]["model"] == "Qwen/Qwen3-Embedding-4B"
    assert committed["meta"]["method"] == "dense"
    four_b = json.loads((PAGES / "atlas" / "atlas-qwen3-4b.json").read_text(encoding="utf-8"))
    assert len(four_b["instruments"]) == len(committed["instruments"])
    for i, (triple, entry) in enumerate(zip(committed["instruments"], four_b["instruments"])):
        assert triple == [entry["c"], entry["k"], entry["n"]], i
    assert four_b["meta"]["model"] == committed["meta"]["model"]
    assert committed["meta"]["gold"]["instruments"] == committed["meta"]["instruments"] == 2677
    assert committed["meta"]["gold_links"] == 277


def test_the_qmd_says_which_matrix_it_uses_and_nothing_about_the_other_model(committed):
    text = " ".join(QMD.read_text(encoding="utf-8").split())
    assert ("in the version that the 4B embedding model gives, which is the option the Atlas "
            "page offers as *4B* and which was chosen because its evaluation row by row on "
            "that page is the better of the three") in text
    assert "0.6B" not in text and "BM25" not in text


def topic_by_term(committed, term):
    return next(t for t in committed["topics"] if t["terms"][0] == term)


def test_the_qmd_worked_example_is_a_topic_of_the_committed_data(committed):
    topic = topic_by_term(committed, "sorteo")
    instruments = committed["instruments"]
    text = " ".join(QMD.read_text(encoding="utf-8").split())
    assert topic["id"] == 18 and topic["size"] == 42
    assert topic["by"] == {"leyes": 2, "reglamentos": 40}
    assert topic["terms"][:3] == ["sorteo", "pronósticos", "premios"]
    assert ("Topic 18 holds 42 instruments, 2 laws and 40 regulations, and its leading terms "
            "are `sorteo`, `pronósticos` and `premios`") in text
    names = [instruments[i][2] for i in topic["reps"]]
    assert all(part in " ".join(names) for part in ("GANA GATO", "TRIS", "PRONOSTICOS RAPIDOS"))
    assert "the regulations of the Gana Gato, Tris and Pronósticos Rápidos draws" in text
    laws = {instruments[i][1] for i in topic["members"] if instruments[i][0] == "leyes"}
    assert laws == {"lfjs", "lfcp"}
    assert "Its laws are the Federal Law of Games and Draws and the Federal Law of Public Brokerage" in text


def test_the_qmd_remark_on_the_two_spellings_is_true_of_the_data(committed):
    led = next(t for t in committed["topics"] if t["id"] == 9)
    assert {"artículo", "articulo"} <= set(led["terms"][:3])
    text = " ".join(QMD.read_text(encoding="utf-8").split())
    assert "`artículo` and `articulo`" in text and "topic 9 is led by both" in text
    # The page no longer says two topics share their leading terms: none does.
    leading = [tuple(t["terms"][:3]) for t in committed["topics"]]
    assert len(leading) == len(set(leading))
    assert "share their leading terms" not in text


# -- the wiring: the page is a draft, unpublished (review fix-2 of #286) ----------------- #
#
# On 2026-10-13 the repository owner decided that no topic reaches the published site
# for now. The page, its two generated files, the exporter and the clustering stay in the
# tree for review, and these tests are the opposite of what #286 first asserted, kept so
# that a later change cannot republish the page by accident. Re-enabling it takes
# dropping `draft: true`, the navbar entry after Atlas, a `pages/topics/**` resource,
# `@Grootendorst2022BERTopic` in references.qmd's nocite and `pages/topics.html` in
# fix_citation_links.py's PAGES (the note beside `project.resources` in _quarto.yml).

def test_the_qmd_is_a_draft_and_keeps_its_content():
    text = QMD.read_text(encoding="utf-8")
    assert front_matter(text)["draft"] == "true"
    assert text.count("{{< include topics/_topics.md >}}") == 1
    assert "freeze" not in text


def test_the_site_does_not_list_or_ship_the_topics_page():
    config = (WEBSITE / "_quarto.yml").read_text(encoding="utf-8")
    entries = re.findall(r"- href: (\S+)\n\s+text: (.+)", config)
    assert "pages/topics.qmd" not in [href for href, _ in entries]
    assert "Topics" not in [name.strip() for _, name in entries]
    assert [name.strip() for _, name in entries][:6] == [
        "Home", "Archive", "Federal Laws", "Atlas", "Instruments", "DOF Titles"]
    resources = re.search(r"resources:\s*\n((?:\s+(?:-|#).*\n)+)", config).group(1)
    assert "pages/topics/**" not in [line.strip().lstrip("- ").strip('"') for line in resources.splitlines()
                                     if not line.strip().startswith("#")]
    assert '- "pages/atlas/**"' in resources
    assert "draft" in resources and "2026-10-13" in resources     # the note on re-enabling it
    # Quarto 1.9.38 renders a draft page as an empty stub file, so the page is also
    # left out of the render list: nothing of it is shipped, not even `topics.html`.
    assert '- "!pages/topics.qmd"' in config


def test_the_bertopic_entry_stays_in_the_bib_but_is_not_listed_on_the_references_page():
    bib = (WEBSITE / "references.bib").read_text(encoding="utf-8")
    entry = bib[bib.index("@article{Grootendorst2022BERTopic"):]
    assert "2203.05794" in entry and "Grootendorst, Maarten" in entry and "year = {2022}" in entry
    assert "[@Grootendorst2022BERTopic]" in QMD.read_text(encoding="utf-8")   # the draft still cites it
    assert "## References" not in QMD.read_text(encoding="utf-8")
    references = (PAGES / "references.qmd").read_text(encoding="utf-8")
    assert "Grootendorst2022BERTopic" not in references and "Topics page" not in references
    hook = (WEBSITE / "scripts" / "fix_citation_links.py").read_text(encoding="utf-8")
    assert "topics.html" not in hook
    spec = importlib.util.spec_from_file_location("fix_citation_links",
                                                  WEBSITE / "scripts" / "fix_citation_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert [page.name for page in module.PAGES] == ["index.html", "atlas.html"]


def test_the_two_generated_files_stay_committed_for_review():
    assert DATA.exists() and MARKDOWN.exists() and QMD.exists()
    ignored = (WEBSITE.parent / ".gitignore").read_text(encoding="utf-8")
    assert "pages/topics" not in ignored


# -- the site Quarto renders ----------------------------------------------------------- #

@pytest.fixture(scope="module")
def rendered_site():
    """The whole project rendered into the gitignored `website/_site/`, as CI does."""
    quarto = find_quarto()
    if quarto is None:
        pytest.skip("Quarto not found: put it on PATH or install it under "
                    "~/.local/opt/quarto-<version>/bin/quarto (CI uses 1.9.38)")
    shutil.rmtree(SITE, ignore_errors=True)       # a page rendered earlier must not survive
    result = subprocess.run([quarto, "render"], cwd=WEBSITE, check=True,
                            capture_output=True, text=True, timeout=600)
    assert (SITE / "pages" / "atlas.html").exists()
    return result


def test_rendered_no_topics_page_and_no_topics_directory(rendered_site):
    assert not (SITE / "pages" / "topics.html").exists()
    assert not (SITE / "pages" / "topics").exists()
    assert not list(SITE.rglob("_topics*")) and not list(SITE.rglob("topics.json"))
    assert "topics.qmd" not in rendered_site.stdout + rendered_site.stderr


def test_rendered_no_page_links_or_lists_topics(rendered_site):
    for page in SITE.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert 'menu-text">Topics<' not in html, page
        assert "topics.html" not in html, page
    search = SITE / "search.json"
    if search.exists():
        assert "topics" not in search.read_text(encoding="utf-8").lower().replace("topics of the", "")
        assert not [entry for entry in json.loads(search.read_text(encoding="utf-8"))
                    if "topics" in entry.get("href", "")]


def test_rendered_the_atlas_page_still_cites_through_the_references_page(rendered_site):
    atlas = (SITE / "pages" / "atlas.html").read_text(encoding="utf-8")
    assert 'href="references.html#ref-McInnes2018UMAP"' in atlas
    references = (SITE / "pages" / "references.html").read_text(encoding="utf-8")
    assert "BERTopic" not in references
