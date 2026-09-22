"""Turn the UMAP projections into one standalone HTML explorer (Vega-Lite).

Issue #241, the last step: coordinates, centroids, neighbours and
`units.parquet` become a single self-contained HTML file whose visualization
is a Vega-Lite spec generated with Altair. Three linked views over one fixed
`[0, 1]` frame:

* **Overview** — one mark per **unit row** of the three corpora (408,804 for
  all unit types today; a text several instruments share is one vector but
  several unit rows, so it is drawn once per row that carries it), coloured
  by collection. Clicking a mark sets `pick`; clicking a legend entry toggles
  a whole collection through `collections`.
* **Detail** — the clicked instrument's every unit, the clicked point itself
  and — with `--neighbors k` — the clicked text's `k` nearest neighbours (a
  distinct outline mark, so a neighbour that is *also* in the same instrument
  reads as both), over a faint background of `--background-points` random
  points. Empty until something is clicked.
* **Instruments** — one mark per law/reglamento/lineamiento at its centroid,
  size by unit count, with its own `pick_instrument` selection that feeds the
  detail view too, and a black ring plus the instrument's name around
  whatever was clicked in *either* view.

**The last click wins.** The two views own two independent selections, and
Vega-Lite cannot define one selection over two concatenated views — so each
selection is *cleared* by a click in the other's marks: `pick`'s `clear` is
`"dblclick, @centroids_1_marks:click"` and `pick_instrument`'s is
`"dblclick, @overview_marks:click"` (the string event-selector form, which
Altair accepts as it is, so `dblclick` clearing survives alongside it).
Exactly one instrument is ever highlighted, and the `pick.i ||
pick_instrument.i` predicate the ring, the label and the detail view share
degenerates to whichever one is live.

A radio switches between the finished projections; the axes are hidden
because UMAP's axes mean nothing. Every non-obvious mark is explained in its
view's subtitle, and the page ends with a footer naming this script, the
commit, the date, the work directory and the command line — the same record
the spec carries in `usermeta.provenance`.

    uv run --group viz python scripts/embeddings/build_umap_html.py
    uv run --group viz python scripts/embeddings/build_umap_html.py \\
        --unit-types article,article_piece --neighbors 15 --text-chars 200

The session that generates this file cannot click in a browser: the spec is
verified by Altair's own schema validation (`chart.to_dict()`), by reloading
the written `.vl.json`, by compiling it to Vega and asserting both `clear`
streams really reach the other view's marks (`check_last_click_wins`), and by
the tests in `tests/test_umap_scripts.py`. The interactive behaviour itself
is verified by a person opening the file.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

import legalvec

sys.path.insert(0, str(Path(__file__).resolve().parent))

from project_umap import DEFAULT_N_NEIGHBORS  # noqa: E402

#: The three corpora and the one-letter code each carries in the compact
#: point records — `c` is in 400,000 rows, so it is a letter and the legend
#: maps it back to a name.
COLLECTION_CODES = {"leyes": "L", "reglamentos": "R", "lineamientos": "N"}
COLLECTION_COLORS = {"L": "#4c78a8", "R": "#f58518", "N": "#54a24b"}

#: `md2akn`'s own `unit_type` vocabulary (issue #218), coded the same way and
#: for the same reason. Order is the code: `u = 0` is `article`.
UNIT_TYPES = ("article", "article_piece", "heading", "loose", "preamble", "conclusions")

UNIT_SHAPES = ("circle", "square", "triangle-up", "diamond", "cross", "triangle-down")

DEFAULT_OUTPUT = Path("output/umap-vectors-qwen3-0.6b.html")

#: The configurations the radio offers when nothing is asked for: the current
#: sweep, named the way `project_umap.py` names its directories. An earlier
#: sweep's directories (`nn015`, `nn050`, `nn200`) stay on disk and come back
#: with `--projections all`.
DEFAULT_PROJECTIONS = tuple(f"nn{n:03d}" for n in DEFAULT_N_NEIGHBORS)

#: What this script is, for the provenance footer: a reader who opens the HTML
#: has no other way to find out what produced it.
SCRIPT_PATH = "scripts/embeddings/build_umap_html.py"

#: The compiled-Vega mark names of the two clickable views, which the
#: cross-view `clear` streams reference by `@<name>:click`. Vega-Lite compiles
#: a view named `v` into a mark named `v_marks`; the asymmetry between the two
#: names below is Altair's, one step earlier: a **unit** view keeps the name it
#: was given (`overview`), while a **layer child** inside a concat gets that
#: concat row's index appended — so `.properties(name="centroids")` on the
#: first layer of the vconcat's second row reaches Vega-Lite as `centroids_1`
#: and compiles to `centroids_1_marks`. Measured with Altair 6.3 / Vega-Lite
#: 6.4; a bare `centroids_marks` is not something any naming of ours produces.
#: Nothing trusts these strings: `check_last_click_wins` compiles the real spec
#: and fails the build if either name, or either clear stream, is not emitted.
OVERVIEW_MARKS = "overview_marks"
CENTROIDS_MARKS = "centroids_1_marks"


def finished_projections(work_dir: Path, wanted=None) -> list[dict]:
    """Every configuration directory with a `.done`, ordered by
    `n_neighbors`, as its own `projection.json`.

    `wanted` is `None` for the current sweep (`DEFAULT_PROJECTIONS`), the
    string `"all"` for every finished directory whatever its name, or an
    explicit collection of directory names.

    A configuration that failed is simply absent — issue #241's failure
    policy is to build the HTML from what finished.
    """
    work_dir = Path(work_dir)
    if wanted is None:
        wanted = DEFAULT_PROJECTIONS
    elif wanted == "all":
        wanted = None
    found = []
    for directory in sorted(p for p in work_dir.iterdir() if p.is_dir()):
        if wanted is not None and directory.name not in wanted:
            continue
        if not (directory / ".done").exists():
            continue
        data = json.loads((directory / "projection.json").read_text(encoding="utf-8"))
        data["dir"] = directory
        found.append(data)
    return sorted(found, key=lambda d: d["n_neighbors"])


def neighbor_strings(work_dir: Path, k: int) -> pd.DataFrame | None:
    """`neighbors.parquet` as one comma-separated string of vector rows per
    row — ~100 bytes for k=15, against a JSON array's ~180."""
    path = Path(work_dir) / "neighbors.parquet"
    if not k or not path.exists():
        return None
    table = pq.read_table(path)
    rows = table.column("row").to_pylist()
    neighbors = table.column("neighbors").to_pylist()
    return pd.DataFrame({
        "row": rows,
        "n": [",".join(str(j) for j in group[:k]) for group in neighbors],
    })


def load_frames(
    work_dir: Path,
    projections: list[dict],
    *,
    unit_types=UNIT_TYPES,
    neighbors: int = 0,
    text_chars: int = 0,
    collections=tuple(COLLECTION_CODES),
    cache_dir=None,
    log=print,
):
    """The two datasets the spec inlines: one row per unit row, and one row
    per instrument.

    The join is `units.parquet` -> `vector_ids.parquet` on
    `(coleccion, text_sha1)` (which vector row this unit's text got) ->
    `instruments.parquet` on `(coleccion, clave)` (which instrument it
    belongs to) -> each projection's `coordinates.parquet`/
    `centroids.parquet` on that row.
    """
    work_dir = Path(work_dir)
    frames = []
    for coleccion in collections:
        columns = ["coleccion", "clave", "unit_type", "eId", "text_sha1"]
        if text_chars:
            columns.append("text")
        table = legalvec.load_units(coleccion, cache_dir=cache_dir).select(columns)
        frames.append(table.to_pandas())
    units = pd.concat(frames, ignore_index=True)
    log(f"{len(units)} unit rows in {len(collections)} collection(s)")
    units = units[units["unit_type"].isin(list(unit_types))]
    log(f"{len(units)} after --unit-types {','.join(unit_types)}")

    ids = pq.read_table(work_dir / "vector_ids.parquet").to_pandas()
    units = units.merge(ids, on=["coleccion", "text_sha1"], how="inner")
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    units = units.merge(instruments[["i", "coleccion", "clave"]],
                        on=["coleccion", "clave"], how="inner")

    points = pd.DataFrame({
        "v": units["row"].astype("int64"),
        "c": units["coleccion"].map(COLLECTION_CODES),
        "i": units["i"].astype("int64"),
        "u": units["unit_type"].map({name: n for n, name in enumerate(UNIT_TYPES)}).astype("int64"),
        "e": units["eId"].fillna("").astype(str),
    })
    if text_chars:
        points["t"] = units["text"].fillna("").astype(str).str.slice(0, text_chars)

    instrument_points = pd.DataFrame({
        "i": instruments["i"].astype("int64"),
        "nm": instruments["nombre"].fillna("").astype(str),
        "cl": instruments["clave"].astype(str),
        "c": instruments["coleccion"].map(COLLECTION_CODES),
        "un": instruments["units"].astype("int64"),
    })

    for j, projection in enumerate(projections):
        coordinates = pq.read_table(projection["dir"] / "coordinates.parquet").to_pandas()
        # float64 before rounding on purpose: a rounded float32 still prints
        # as 0.12300000339746475 in JSON, which would add tens of MB.
        coordinates["x"] = coordinates["x"].astype("float64").round(3)
        coordinates["y"] = coordinates["y"].astype("float64").round(3)
        merged = points.merge(
            coordinates.rename(columns={"row": "v", "x": f"x{j}", "y": f"y{j}"}),
            on="v", how="left",
        )
        points[f"x{j}"] = merged[f"x{j}"].to_numpy()
        points[f"y{j}"] = merged[f"y{j}"].to_numpy()

        centroids = pq.read_table(projection["dir"] / "centroids.parquet").to_pandas()
        centroids["x"] = centroids["x"].astype("float64").round(3)
        centroids["y"] = centroids["y"].astype("float64").round(3)
        merged = instrument_points.merge(
            centroids.rename(columns={"x": f"cx{j}", "y": f"cy{j}"}), on="i", how="left",
        )
        instrument_points[f"cx{j}"] = merged[f"cx{j}"].to_numpy()
        instrument_points[f"cy{j}"] = merged[f"cy{j}"].to_numpy()

    strings = neighbor_strings(work_dir, neighbors)
    if strings is not None:
        merged = points.merge(strings.rename(columns={"row": "v"}), on="v", how="left")
        points["n"] = merged["n"].fillna("").to_numpy()

    return points, instrument_points


def projection_expr(prefix: str, count: int) -> str:
    """The `calculate` expression that picks one projection's coordinate
    pair, driven by the `proj` radio."""
    expr = f"datum.{prefix}{count - 1}"
    for j in range(count - 2, -1, -1):
        expr = f"proj === {j} ? datum.{prefix}{j} : ({expr})"
    return expr


def _code_expr(field: str, values) -> str:
    """A Vega expression turning a small integer/letter code back into the
    name a tooltip should show."""
    pairs = ", ".join(f"{json.dumps(str(key))}: {json.dumps(value)}"
                      for key, value in values.items())
    return f"{{{pairs}}}[{field}]"


def repository_commit(root: Path | None = None) -> str:
    """The short commit this build came from, or `"unknown"`.

    Never raises: the HTML has to be buildable from a tarball with no `.git`
    at all, and a missing commit is worth less than a failed build.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def provenance_record(work_dir: Path, projections: list[dict], *, argv=None,
                      now=None, commit=None) -> dict:
    """What produced this page, as a dict — the same record goes into the
    HTML's footer, into the spec's `usermeta` (so the `.vl.json` carries it
    too) and into what `build()` logs, so the three can be matched."""
    argv = list(sys.argv if argv is None else argv)
    return {
        "script": SCRIPT_PATH,
        "commit": commit if commit is not None else repository_commit(),
        "date": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "work_dir": str(Path(work_dir).resolve()),
        "argv": " ".join(argv),
        "projections": [p["name"] for p in projections],
    }


def footer_html(provenance: dict) -> str:
    """The provenance line inserted after the chart's own `div`.

    Written by hand rather than by Altair: its HTML saver has no hook for
    anything outside the chart, and post-processing its output keeps both the
    CDN and the `inline=True` template intact.
    """
    line = (f"Generated by {provenance['script']} (LegalIA, commit "
            f"{provenance['commit']}) on {provenance['date']} from "
            f"{provenance['work_dir']} — projections "
            f"{', '.join(provenance['projections'])} — command: "
            f"{provenance['argv']}")
    return ('<footer style="font-family: sans-serif; font-size: 11px; '
            f'color: #666666; margin: 8px 0 0 8px;">{html_module.escape(line)}</footer>')


def insert_footer(html: str, footer: str) -> str:
    """Put `footer` right after the chart's `div`, or at the end of the body
    if Altair ever stops emitting one."""
    anchor = '<div id="vis"></div>'
    if anchor in html:
        return html.replace(anchor, anchor + "\n  " + footer, 1)
    return html.replace("</body>", "  " + footer + "\n</body>", 1)


def _spec_skeleton(spec: dict) -> dict:
    """`spec` with its inlined datasets truncated to five rows.

    Mark names and signals are decided by the spec's *structure*, so the
    Vega compiler answers the same question about the skeleton as about the
    real thing — at 408,804 rows less. The same trick the schema check
    already uses.
    """
    skeleton = dict(spec)
    skeleton["datasets"] = {name: rows[:5]
                            for name, rows in spec.get("datasets", {}).items()}
    return skeleton


def compiled_vega(spec: dict) -> dict:
    """`spec` compiled to Vega, the way a browser would compile it."""
    try:
        import vl_convert
    except ImportError as error:  # pragma: no cover - the viz group ships it
        raise SystemExit(
            "vl-convert-python is needed to check the compiled spec -- "
            "install the viz group (uv sync --group viz)"
        ) from error
    return vl_convert.vegalite_to_vega(_spec_skeleton(spec))


def _vega_marks(node: dict) -> list[dict]:
    """Every mark of a compiled Vega spec, groups included, flattened."""
    found = []
    for mark in node.get("marks", []) or []:
        found.append(mark)
        found.extend(_vega_marks(mark))
    return found


def _vega_signals(node: dict) -> list[dict]:
    """Every signal of a compiled Vega spec, at whatever group it sits in."""
    found = list(node.get("signals", []) or [])
    for mark in node.get("marks", []) or []:
        found.extend(_vega_signals(mark))
    return found


def clearing_marknames(vega: dict) -> dict[str, list[str]]:
    """Per selection tuple signal, the mark names a click on which clears it.

    A selection's `clear` stream compiles to an `on` entry whose `update` is
    the literal `"null"`; `@<name>:click` becomes an event with that
    `markname`. So this is the compiled evidence that "the last click wins"
    is really wired — the one thing a session with no browser can check.
    """
    clears: dict[str, list[str]] = {}
    for signal in _vega_signals(vega):
        if not signal["name"].endswith("_tuple"):
            continue
        names = [event["markname"]
                 for entry in signal.get("on", []) or []
                 if entry.get("update") == "null"
                 for event in entry.get("events", []) or []
                 if isinstance(event, dict) and "markname" in event]
        if names:
            clears[signal["name"]] = names
    return clears


def check_last_click_wins(spec: dict, log=print) -> dict:
    """Compile `spec` and fail the build unless each selection is cleared by
    a click in the other view's marks.

    Returns what was found, so the caller can log it next to the rest of the
    measurements. A page whose two selections can both be live at once is
    the defect this pass exists to fix, so an unwired spec raises rather than
    being written out with a warning.
    """
    vega = compiled_vega(spec)
    marks = [mark["name"] for mark in _vega_marks(vega) if mark.get("name")]
    clears = clearing_marknames(vega)
    found = {
        "marks": sorted(name for name in marks if name.endswith("_marks")),
        "clears": clears,
    }
    wanted = {"pick_tuple": CENTROIDS_MARKS, "pick_instrument_tuple": OVERVIEW_MARKS}
    problems = [f"the compiled Vega has no mark named {name}"
                for name in (OVERVIEW_MARKS, CENTROIDS_MARKS) if name not in marks]
    problems += [f"{signal} is not cleared by a click on {markname} "
                 f"(cleared by: {clears.get(signal, [])})"
                 for signal, markname in wanted.items()
                 if markname not in clears.get(signal, [])]
    if problems:
        raise SystemExit("the 'last click wins' wiring is broken: " + "; ".join(problems))
    log("last click wins: " + ", ".join(
        f"{signal} cleared by @{markname}:click" for signal, markname in wanted.items()))
    return found


def build_chart(points: pd.DataFrame, instruments: pd.DataFrame, projections: list[dict],
                *, neighbors: int = 0, text_chars: int = 0,
                background_points: int = 20000, overview_sample: int | None = None,
                nearest: bool = False, provenance: dict | None = None, seed: int = 0):
    """The whole spec: overview | detail over instruments, with the
    projection radio on top."""
    import altair as alt

    alt.data_transformers.disable_max_rows()

    count = len(projections)
    unit_names = {n: name for n, name in enumerate(UNIT_TYPES)}
    collection_names = {code: name for name, code in COLLECTION_CODES.items()}

    proj = alt.param(
        name="proj", value=0,
        bind=alt.binding_radio(
            options=list(range(count)),
            labels=[f"n_neighbors={p['n_neighbors']} " for p in projections],
            name="projection ",
        ),
    )
    pick_fields = ["v", "i", "e"] + (["n"] if neighbors else [])
    # "The last click wins": a click on the instrument view's centroids clears
    # this selection, and vice versa below. A comma merges event streams, so
    # `dblclick` clearing survives; `@<markname>:click` is what scopes a stream
    # to one view's marks, which is why both views are named.
    pick = alt.selection_point(name="pick", fields=pick_fields, on="click",
                               nearest=nearest,
                               clear=f"dblclick, @{CENTROIDS_MARKS}:click", empty=False)
    collections = alt.selection_point(name="collections", fields=["c"], bind="legend")

    color = alt.Color(
        "c:N",
        scale=alt.Scale(domain=list(COLLECTION_COLORS), range=list(COLLECTION_COLORS.values())),
        legend=alt.Legend(title="collection",
                          labelExpr=_code_expr("datum.value", collection_names)),
    )
    x = alt.X("x:Q", scale=alt.Scale(domain=[0, 1]), axis=None, title=None)
    y = alt.Y("y:Q", scale=alt.Scale(domain=[0, 1]), axis=None, title=None)
    lookup = alt.LookupData(instruments, key="i", fields=["nm", "cl", "un"])

    def framed(data, prefix="x", prefix_y="y"):
        return (
            alt.Chart(data)
            .transform_calculate(
                x=projection_expr(prefix, count),
                y=projection_expr(prefix_y, count),
                col=_code_expr("datum.c", collection_names),
                ut=_code_expr("datum.u", unit_names),
            )
        )

    tooltip = [
        alt.Tooltip("nm:N", title="instrument"),
        alt.Tooltip("col:N", title="collection"),
        alt.Tooltip("e:N", title="eId"),
        alt.Tooltip("ut:N", title="unit type"),
    ]
    if text_chars:
        tooltip.append(alt.Tooltip("t:N", title="text"))

    overview_data = points
    if overview_sample and overview_sample < len(points):
        overview_data = points.sample(n=overview_sample, random_state=seed)
    overview = (
        framed(overview_data)
        .transform_lookup(lookup="i", from_=lookup)
        # size 10 / opacity 0.25 rather than the first pass's 3 / 0.3: with
        # `nearest` off, the pointer has to land on the mark itself, and a
        # 3-px² circle among 400,000 is not clickable. Measured by eye on the
        # real page: at 10 px² the cloud still reads as a cloud.
        .mark_circle(size=10)
        .encode(x=x, y=y, color=color,
                opacity=alt.condition(collections, alt.value(0.25), alt.value(0.02)),
                tooltip=tooltip)
        .add_params(pick, collections)
        # `name` is what the cross-view clear streams address: Vega-Lite turns
        # it into the compiled mark name `overview_marks`.
        .properties(name="overview", width=520, height=520,
                    title=alt.TitleParams(
                        f"{len(overview_data)} units of the three corpora",
                        subtitle=["click a unit to fill the two views below and to the right;"
                                  " double-click to clear",
                                  "the last click wins: clicking an instrument below clears"
                                  " this selection, and the other way round"]))
    )

    same_instrument = (
        "((isValid(pick.i) && indexof(pick.i, datum.i) >= 0) || "
        "(isValid(pick_instrument.i) && indexof(pick_instrument.i, datum.i) >= 0))"
    )
    layers = []
    if background_points:
        sample = points if background_points >= len(points) else points.sample(
            n=background_points, random_state=seed)
        layers.append(
            framed(sample).mark_circle(size=2, color="#cccccc", opacity=0.35).encode(x=x, y=y)
        )
    layers.append(
        framed(points)
        .transform_filter(same_instrument)
        .transform_lookup(lookup="i", from_=lookup)
        .mark_point(size=40, filled=True)
        .encode(x=x, y=y, color=color,
                shape=alt.Shape("ut:N", scale=alt.Scale(domain=list(UNIT_TYPES),
                                                        range=list(UNIT_SHAPES)),
                                legend=alt.Legend(title="unit type")),
                tooltip=tooltip)
    )
    if neighbors:
        layers.append(
            framed(points)
            .transform_filter(
                "isValid(pick.n) && indexof(split(pick.n[0], ','), toString(datum.v)) >= 0"
            )
            .transform_lookup(lookup="i", from_=lookup)
            .mark_point(size=110, filled=False, stroke="#d62728", strokeWidth=1.5)
            .encode(x=x, y=y, tooltip=tooltip)
        )
    layers.append(
        framed(points)
        .transform_filter("isValid(pick.v) && indexof(pick.v, datum.v) >= 0")
        .transform_lookup(lookup="i", from_=lookup)
        .mark_point(size=200, filled=True, color="#000000", shape="diamond")
        .encode(x=x, y=y, tooltip=tooltip)
    )
    layers.append(
        alt.Chart(instruments)
        .transform_filter(same_instrument)
        .transform_calculate(
            label="datum.nm + (isValid(pick.e) ? '  --  ' + pick.e[0] : '')")
        .mark_text(align="center", baseline="top", fontSize=11, limit=480)
        .encode(x=alt.value(260), y=alt.value(4), text=alt.Text("label:N"))
    )
    # Every mark of this view is explained in the subtitle rather than in a
    # hand-drawn legend layer: a subtitle is a few lines of spec, renders the
    # same on every renderer, and cannot drift from the data. The neighbour
    # count is the `neighbors` argument, never a literal.
    detail_subtitle = ["◆ black diamond: the clicked text"]
    if neighbors:
        detail_subtitle.append(
            f"○ red rings: its {neighbors} nearest neighbours by cosine, "
            "across all three collections")
    detail_subtitle.append(
        "filled shapes: every unit of the same instrument (shape = unit type)")
    detail_title = (
        "click a unit or an instrument: its units, the neighbours of the clicked text"
        if neighbors else
        "click a unit or an instrument: every unit of that instrument"
    )
    detail = alt.layer(*layers).properties(
        width=520, height=520,
        title=alt.TitleParams(detail_title, subtitle=detail_subtitle),
    )

    pick_instrument = alt.selection_point(
        name="pick_instrument", fields=["i"], on="click",
        clear=f"dblclick, @{OVERVIEW_MARKS}:click", empty=False)
    centroids_layer = (
        framed(instruments, "cx", "cy")
        .mark_circle()
        .encode(x=x, y=y, color=color,
                size=alt.Size("un:Q", scale=alt.Scale(range=[10, 400]),
                              legend=alt.Legend(title="units")),
                opacity=alt.condition(collections, alt.value(0.75), alt.value(0.03)),
                tooltip=[alt.Tooltip("nm:N", title="instrument"),
                         alt.Tooltip("cl:N", title="clave"),
                         alt.Tooltip("col:N", title="collection"),
                         alt.Tooltip("un:Q", title="units")])
        .add_params(pick_instrument)
        # Named for the same reason the overview is, with the one difference
        # the constant records: a layer child compiles to `centroids_1_marks`,
        # not to `centroids_marks`.
        .properties(name="centroids")
    )
    # The ring and the label answer "where did what I just clicked come
    # from?", whichever view the click landed in -- `same_instrument` is the
    # very predicate the detail view filters on, so the two cannot disagree.
    # A ring rather than a colour change: colour already means collection, and
    # 900 px^2 is well above the largest centroid (the size scale tops at 400),
    # so it reads as a ring around the mark at every instrument size.
    ring_layer = (
        framed(instruments, "cx", "cy")
        .transform_filter(same_instrument)
        .mark_point(filled=False, stroke="#000000", strokeWidth=2, size=900)
        .encode(x=x, y=y)
    )
    label_layer = (
        framed(instruments, "cx", "cy")
        .transform_filter(same_instrument)
        .mark_text(align="center", baseline="bottom", dy=-18, fontSize=11, limit=480)
        .encode(x=x, y=y, text=alt.Text("nm:N"))
    )
    instrument_view = (
        alt.layer(centroids_layer, ring_layer, label_layer)
        .properties(width=1060, height=300,
                    title=alt.TitleParams(
                        f"{len(instruments)} instruments, at the centroid of their units",
                        subtitle=["black ring + name: the instrument of the clicked text "
                                  "(or the clicked centroid)",
                                  "clicking a centroid clears the unit picked above: "
                                  "only one instrument is ever highlighted"]))
    )

    usermeta = {"embedOptions": {"renderer": "canvas", "actions": False}}
    if provenance is not None:
        usermeta["provenance"] = provenance
    chart = (
        alt.vconcat(alt.hconcat(overview, detail), instrument_view)
        .add_params(proj)
        .configure_view(stroke=None)
        .configure_legend(labelFontSize=11, titleFontSize=11)
        .properties(
            title="UMAP of every text of the federal laws, reglamentos and lineamientos",
            usermeta=usermeta,
        )
    )
    return chart


def build(work_dir: Path, output: Path, *, unit_types=UNIT_TYPES, projections=None,
          neighbors: int = 0, text_chars: int = 0, background_points: int = 20000,
          overview_sample: int | None = None, nearest: bool = False, inline_js: bool = False,
          collections=tuple(COLLECTION_CODES), cache_dir=None, argv=None,
          log=print) -> dict:
    """Write the HTML and its `.vl.json`, returning what was measured."""
    import altair as alt

    work_dir = Path(work_dir)
    found = finished_projections(work_dir, projections)
    if not found:
        raise SystemExit(f"no finished projection in {work_dir} -- nothing to build")
    log("projections: " + ", ".join(f"{p['name']} (n_neighbors={p['n_neighbors']})" for p in found))

    points, instruments = load_frames(
        work_dir, found, unit_types=unit_types, neighbors=neighbors,
        text_chars=text_chars, collections=collections, cache_dir=cache_dir, log=log,
    )
    log(f"{len(points)} points, {len(instruments)} instruments")

    provenance = provenance_record(work_dir, found, argv=argv)
    chart = build_chart(points, instruments, found, neighbors=neighbors,
                        text_chars=text_chars, background_points=background_points,
                        overview_sample=overview_sample, nearest=nearest,
                        provenance=provenance)
    spec = chart.to_dict()  # validates against the Vega-Lite schema
    # Before anything is written: a page whose two selections stay on at once
    # is exactly what this build is supposed to stop producing.
    wiring = check_last_click_wins(spec, log=log)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    spec_path = output.with_suffix(".vl.json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    if inline_js:
        chart.save(str(output), format="html", inline=True)
    else:
        chart.save(str(output), format="html")
    # Post-processing rather than a saver option: Altair has no hook for
    # anything outside the chart, and both templates end up with the same
    # `<div id="vis">` to insert after.
    output.write_text(
        insert_footer(output.read_text(encoding="utf-8"), footer_html(provenance)),
        encoding="utf-8",
    )

    # A reload is the cheap end-to-end check a session with no browser can
    # actually make. It is done in two halves on purpose: the whole spec is
    # reloaded with `validate=False` (a 113 MB spec put through jsonschema
    # validates all 408,804 inlined data rows against the schema's `any`,
    # which measured at over ten minutes without finishing), and the schema
    # check itself then runs on a copy whose datasets are truncated to five
    # rows -- the structure is what a schema can say anything about.
    reloaded = json.loads(spec_path.read_text(encoding="utf-8"))
    alt.Chart.from_dict(reloaded, validate=False)
    alt.Chart.from_dict(_spec_skeleton(reloaded))

    measured = {
        "output": str(output),
        "spec": str(spec_path),
        "html_bytes": output.stat().st_size,
        "spec_bytes": spec_path.stat().st_size,
        "points": len(points),
        "instruments": len(instruments),
        "projections": [p["name"] for p in found],
        "neighbors": neighbors,
        "unit_types": list(unit_types),
        "last_click_wins": wiring,
        "provenance": provenance,
    }
    log(json.dumps(measured, indent=2))
    log(f"{output}: {measured['html_bytes'] / 1e6:.1f} MB, {len(points)} points")
    log(f"HTML: {output.resolve()}")
    log(f"spec: {spec_path.resolve()}")
    return measured


def _wanted_projections(value: str | None):
    """`--projections` as `finished_projections` wants it: `None` for the
    current sweep, `"all"` for every finished directory, a set otherwise."""
    if not value:
        return None
    if value == "all":
        return "all"
    return set(value.split(","))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--unit-types", default=",".join(UNIT_TYPES),
                        help="comma-separated md2akn unit_types to draw")
    parser.add_argument("--projections", default=None,
                        help="comma-separated configuration names, or 'all' for every "
                             f"finished directory (default: {', '.join(DEFAULT_PROJECTIONS)})")
    parser.add_argument("--neighbors", type=int, default=0,
                        help="neighbours per point to ring in the detail view. 0, the "
                             "default, drops the `n` field, the red-ring layer and the "
                             "subtitle line that explains them; the kNN table itself "
                             "(neighbors.parquet) is kept on disk either way, so "
                             "--neighbors 15 restores the full page with no refit")
    parser.add_argument("--text-chars", type=int, default=0,
                        help="characters of the unit's own text to put in the tooltip")
    parser.add_argument("--background-points", type=int, default=20000)
    parser.add_argument("--overview-sample", type=int, default=None,
                        help="thin only the overview layer to N points (default: all)")
    parser.add_argument("--nearest", action="store_true",
                        help="select the mark nearest the cursor instead of the one under "
                             "it. Off by default: `nearest` makes Vega-Lite insert a hidden "
                             "Voronoi layer that captures the pointer, and the tooltip is "
                             "then evaluated on that layer's wrapper datum, so every "
                             "overview tooltip reads `undefined`")
    parser.add_argument("--inline-js", action="store_true",
                        help="embed vega/vega-lite/vega-embed instead of loading them from jsdelivr")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    random.seed(args.seed)
    build(
        args.work_dir, args.output,
        unit_types=tuple(t for t in args.unit_types.split(",") if t),
        projections=_wanted_projections(args.projections),
        neighbors=args.neighbors, text_chars=args.text_chars,
        background_points=args.background_points, overview_sample=args.overview_sample,
        nearest=args.nearest, inline_js=args.inline_js, cache_dir=args.cache_dir,
        argv=[SCRIPT_PATH] + list(sys.argv[1:] if argv is None else argv),
    )


if __name__ == "__main__":
    main()
