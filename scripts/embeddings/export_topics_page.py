"""Export the topics of the instrument map for the website's Topics page (issue #286).

    uv run --group viz python scripts/embeddings/export_topics_page.py --work-dir emb-run-atlas
    uv run --group viz python scripts/embeddings/export_topics_page.py \\
        --work-dir emb-run-atlas --output-dir /tmp/topics

`cluster_instruments.py` (issue #285) clusters the 2,676 unique instruments of the
Atlas into topics and writes `<work-dir>/topics/topics.json` (gitignored). The
website cannot read it, and the page that presents it is static Markdown with no
JavaScript, so this script writes **two committed files** under
`website/pages/topics/`, from that file and `instruments.parquet` alone (a pure
read, seconds, no refit):

* `topics.json`: the page's data, with short keys. `meta` (the title, when and
  from which commit it was generated, the model and method, the algorithm and its
  parameter, the measures and the counts, copied from #285's `meta`), one entry
  per topic (`id`, `size`, `terms`, `reps`, `members`, `by`) and `instruments`,
  the Atlas' own `[c, k, n]` triples in `i` order so a reader of the JSON can
  resolve a member `i`. `id` is the number the page prints: the rank of the topic
  by size, from 1 (`cluster` keeps the number #285 gave it, from 0), and the
  instruments in no topic, if any, are `id: -1`, last. `reps` and `members` are
  lists of `i` (the distance to the topic's mean is not carried; the order is).
* `_topics.md`: the Markdown `pages/topics.qmd` includes, led by an HTML comment
  that says it is generated. A summary table (topic, its three leading terms,
  size, and laws / regulations / guidelines / treaties, each topic cell an anchor)
  and then one section per topic, largest first and the noise last, each with a
  level-3 heading "Topic *n*: term · term · term" and an explicit `{#topic-n}` id,
  its ten terms as inline code, a sentence giving its size and composition in
  words, its three representative instruments with their collection, and a
  collapsed callout with every member **grouped by collection** (the Atlas' order)
  and, within a group, **ordered by distance to the topic's mean**, the
  representatives first. Plain lists, not tables: a long treaty name wraps in a list
  and overflows a table on a phone. Each member is its name as the corpus spells it
  and its `abrev` (a law) or its SCJN id (anything else) in inline code.

Both files are generated, never edited by hand; regenerating them is this script,
run by a human, never a workflow (issue #115, Hallazgo C). A work directory with
no `topics/.done` is refused. Nothing but `topics.json` and `instruments.parquet`
is read, so the output is deterministic apart from `meta.generated`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_umap_html as html  # noqa: E402
import cluster_instruments  # noqa: E402
from _atomic import atomic_write_text  # noqa: E402

DEFAULT_OUTPUT_DIR = Path("website/pages/topics")
DATA_NAME = "topics.json"
MARKDOWN_NAME = "_topics.md"

TITLE = "Topics of the Atlas of Mexican Federal Law"

#: The Atlas' order of collections, and the words the page uses for them.
COLLECTIONS = tuple(html.COLLECTION_CODES)
PLURAL = {"leyes": "laws", "reglamentos": "regulations", "lineamientos": "guidelines",
          "tratados": "treaties"}
SINGULAR = {"leyes": "law", "reglamentos": "regulation", "lineamientos": "guideline",
            "tratados": "treaty"}
HEADING_TERMS = 3
NOISE_ID = -1
NOISE_HEADING = "Instruments in no topic"

#: Characters Markdown (and pandoc) would read as syntax inside a name. A backslash
#: before any ASCII punctuation is literal in pandoc, so escaping is always safe.
_ESCAPED = re.compile(r"([\\`*_\[\]<>#|$~^@\"'])")
_DASHES = re.compile(r"-{2,}")
_DOTS = re.compile(r"\.{3,}")


def escape(text: str) -> str:
    """`text` as literal Markdown. Whitespace runs (one name carries a line break)
    collapse to one space, quotes stay straight (pandoc's smart typography would
    curl them), and a run of two hyphens or three dots, which it would turn into a
    dash and an ellipsis, is kept as the corpus spells it."""
    text = " ".join(text.split())
    text = _ESCAPED.sub(r"\\\1", text)
    text = _DASHES.sub(lambda match: "\\-" * len(match.group(0)), text)
    return _DOTS.sub(lambda match: "\\." * len(match.group(0)), text)


def inline_code(text: str) -> str:
    """`text` as inline code, with a fence one backtick longer than any run inside."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def count_phrase(counts: dict, *, forms=PLURAL) -> str:
    """`9 laws, 121 regulations and 12 guidelines`: the non-zero counts in the Atlas'
    order, a count of one in the singular."""
    parts = [f"{count:,} {SINGULAR[name] if count == 1 else forms[name]}"
             for name in COLLECTIONS if (count := counts.get(name, 0))]
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def instruments_noun(count: int) -> str:
    return f"{count:,} instrument" + ("" if count == 1 else "s")


def anchor(topic: dict) -> str:
    return "topic-none" if topic["id"] == NOISE_ID else f"topic-{topic['id']}"


def leading_terms(topic: dict) -> str:
    return " · ".join(topic["terms"][:HEADING_TERMS])


def heading(topic: dict) -> str:
    if topic["id"] == NOISE_ID:
        return f"{NOISE_HEADING} {{#{anchor(topic)}}}"
    return f"Topic {topic['id']}: {escape(leading_terms(topic))} {{#{anchor(topic)}}}"


# -- reading ---------------------------------------------------------------- #

def load(work_dir: Path) -> dict:
    """`#285`'s `topics.json` and the instruments, after refusing a work directory whose
    topics are not finished."""
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    out = cluster_instruments.output_dir(work_dir)
    for path in (out / cluster_instruments.DONE_MARKER, out / "topics.json"):
        if not path.exists():
            raise SystemExit(f"{path} is missing: run cluster_instruments.py --work-dir "
                             f"{work_dir} first")
    topics = json.loads((out / "topics.json").read_text(encoding="utf-8"))
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    return {"topics": topics, "instruments": instruments}


def page_topics(raw: dict, instruments: list[list[str]]) -> list[dict]:
    """The topics as the page numbers and orders them: by size, largest first (a tie
    keeps #285's order), numbered from 1, the instruments in no topic last as `-1`.
    Every instrument must be in exactly one topic."""
    ordinary = sorted((t for t in raw["topics"] if t["id"] != NOISE_ID),
                      key=lambda t: (-t["size"], t["id"]))
    noise = [t for t in raw["topics"] if t["id"] == NOISE_ID]
    topics = []
    for number, topic in enumerate(ordinary + noise, start=1):
        members = [int(i) for i, _ in topic["members"]]
        by: dict[str, int] = {}
        for i in members:
            by[instruments[i][0]] = by.get(instruments[i][0], 0) + 1
        topics.append({
            "id": NOISE_ID if topic["id"] == NOISE_ID else number,
            "cluster": topic["id"],
            "size": len(members),
            "terms": list(topic["terms"]),
            "reps": [int(i) for i, _ in topic["representatives"]],
            "members": members,
            "by": {name: by[name] for name in COLLECTIONS if name in by},
        })
    seen = sorted(i for topic in topics for i in topic["members"])
    if seen != list(range(len(instruments))):
        raise SystemExit("topics.json does not place every instrument in exactly one topic "
                         f"({len(seen)} memberships for {len(instruments)} instruments)")
    return topics


def build_data(work_dir: Path, *, now=None, commit=None) -> dict:
    """`topics.json`'s content for the page."""
    loaded = load(work_dir)
    raw = loaded["topics"]
    frame = loaded["instruments"].sort_values("i")
    if frame["i"].tolist() != list(range(len(frame))):
        raise SystemExit("instruments.parquet does not number its instruments 0..n-1")
    instruments = [[str(c), str(k), str(n)] for c, k, n in
                   zip(frame["coleccion"], frame["clave"], frame["nombre"])]
    topics = page_topics(raw, instruments)
    source = raw["meta"]
    collections = {name: sum(1 for c, _, _ in instruments if c == name) for name in COLLECTIONS
                   if any(c == name for c, _, _ in instruments)}
    meta = {
        "title": TITLE,
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "commit": commit if commit is not None else html.repository_commit(),
        "model": source["model"],
        "method": source["method"],
        "algorithm": source["algorithm"],
        "parameter_name": source["parameter_name"],
        "parameter": source["parameter"],
        "selection": source["selection"],
        "gates": source["gates"],
        "measures": source["measures"],
        "gold_links": source["gold_links"],
        "labels": {key: source["labels"][key] for key in
                   ("method", "ngram_range", "min_instruments", "top_terms", "representatives")},
        "instruments": len(instruments),
        "topics": sum(1 for t in topics if t["id"] != NOISE_ID),
        "noise_instruments": sum(t["size"] for t in topics if t["id"] == NOISE_ID),
        "collections": collections,
        "numbering": "topics are numbered from 1 by size; instruments in no topic are -1",
    }
    return {"meta": meta, "topics": topics, "instruments": instruments}


# -- writing ----------------------------------------------------------------- #

def member_line(instrument: list[str]) -> str:
    _, key, name = instrument
    return f"- {escape(name)} {inline_code(key)}"


def summary_table(data: dict) -> str:
    header = ("| Topic | Leading terms | Instruments | Laws | Regulations | Guidelines | Treaties |\n"
              "|---|---|---:|---:|---:|---:|---:|")
    rows = []
    for topic in data["topics"]:
        label = NOISE_HEADING if topic["id"] == NOISE_ID else f"Topic {topic['id']}"
        terms = "" if topic["id"] == NOISE_ID else escape(leading_terms(topic))
        counts = " | ".join(f"{topic['by'].get(name, 0):,}" for name in COLLECTIONS)
        rows.append(f"| [{label}](#{anchor(topic)}) | {terms} | {topic['size']:,} | {counts} |")
    return "\n".join([header, *rows])


def section(topic: dict, instruments: list[list[str]]) -> str:
    """One topic's Markdown: heading, terms, composition, representatives, members."""
    noise = topic["id"] == NOISE_ID
    composition = count_phrase(topic["by"])
    lines = [f"### {heading(topic)}", "",
             " ".join(inline_code(term) for term in topic["terms"]), ""]
    if noise:
        lines += [f"{instruments_noun(topic['size'])} belong to no topic, namely "
                  f"{composition}.", ""]
    else:
        lines += [f"The topic holds {instruments_noun(topic['size'])}, namely {composition}.", ""]
    shown = len(topic["reps"])
    if shown == 1:
        intro = ("Its most representative instrument, the nearest to the centre of the "
                 "topic, is the following.")
    else:
        number = {2: "two", 3: "three"}.get(shown, str(shown))
        intro = (f"Its {number} most representative instruments, from the nearest to the "
                 "centre of the topic, are the following.")
    lines += [intro, ""]
    for i in topic["reps"]:
        collection, key, name = instruments[i]
        lines.append(f"- {escape(name)} {inline_code(key)}, a {SINGULAR[collection]}")
    lines += ["", f'::: {{.callout-note collapse="true" title="All {topic["size"]:,} members"}}', ""]
    for name in COLLECTIONS:
        members = [i for i in topic["members"] if instruments[i][0] == name]
        if not members:
            continue
        lines += [f"**{PLURAL[name].capitalize()} ({len(members):,})**", ""]
        lines += [member_line(instruments[i]) for i in members] + [""]
    lines += [":::", ""]
    return "\n".join(lines)


def build_markdown(data: dict) -> str:
    """`_topics.md`: the generated comment, the summary table, then every section."""
    instruments = data["instruments"]
    parts = [
        "<!-- Generated by scripts/embeddings/export_topics_page.py from the topics of "
        "the instrument map; do not edit by hand. -->",
        "",
        summary_table(data),
        "",
    ]
    parts += [section(topic, instruments) for topic in data["topics"]]
    return "\n".join(parts)


def export(work_dir: Path, output_dir: Path = DEFAULT_OUTPUT_DIR, *, now=None, commit=None,
           log=print) -> dict:
    """Write `topics.json` and `_topics.md` into `output_dir`; returns the data."""
    data = build_data(work_dir, now=now, commit=commit)
    output_dir = Path(output_dir)
    atomic_write_text(output_dir / DATA_NAME,
                      json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    atomic_write_text(output_dir / MARKDOWN_NAME, build_markdown(data))
    meta = data["meta"]
    log(f"{meta['topics']} topics over {meta['instruments']} instruments "
        f"({meta['noise_instruments']} in none) -> {output_dir}")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="a work directory with finished topics (emb-run-atlas)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"where topics.json and _topics.md go (default {DEFAULT_OUTPUT_DIR})")
    args = parser.parse_args(argv)
    export(args.work_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
