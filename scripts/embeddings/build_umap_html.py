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
* **Detail** — the clicked instrument's every unit, the clicked text's
  nearest neighbours (a distinct outline mark, so a neighbour that is *also*
  in the same instrument reads as both), and the clicked point itself, over a
  faint background of `--background-points` random points. Empty until
  something is clicked.
* **Instruments** — one mark per law/reglamento/lineamiento at its centroid,
  size by unit count, with its own `pick_instrument` selection that feeds the
  detail view too.

A radio switches between the finished projections; the axes are hidden
because UMAP's axes mean nothing.

    uv run --group viz python scripts/embeddings/build_umap_html.py
    uv run --group viz python scripts/embeddings/build_umap_html.py \\
        --unit-types article,article_piece --neighbors 0 --text-chars 200

The session that generates this file cannot click in a browser: the spec is
verified by Altair's own schema validation (`chart.to_dict()`), by reloading
the written `.vl.json`, and by the tests in `tests/test_umap_scripts.py`.
The interactive behaviour itself is verified by a person opening the file.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

import legalvec

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


def finished_projections(work_dir: Path, wanted=None) -> list[dict]:
    """Every configuration directory with a `.done`, ordered by
    `n_neighbors`, as its own `projection.json`.

    A configuration that failed is simply absent — issue #241's failure
    policy is to build the HTML from what finished.
    """
    work_dir = Path(work_dir)
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
    neighbors: int = 15,
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


def build_chart(points: pd.DataFrame, instruments: pd.DataFrame, projections: list[dict],
                *, neighbors: int = 15, text_chars: int = 0,
                background_points: int = 20000, overview_sample: int | None = None,
                nearest: bool = True, seed: int = 0):
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
    pick = alt.selection_point(name="pick", fields=pick_fields, on="click",
                               nearest=nearest, clear="dblclick", empty=False)
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
        .mark_circle(size=3)
        .encode(x=x, y=y, color=color,
                opacity=alt.condition(collections, alt.value(0.3), alt.value(0.02)),
                tooltip=tooltip)
        .add_params(pick, collections)
        .properties(width=520, height=520,
                    title=f"{len(overview_data)} units of the three corpora")
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
    detail = alt.layer(*layers).properties(
        width=520, height=520,
        title="click a unit or an instrument: its units, the neighbours of the clicked text",
    )

    pick_instrument = alt.selection_point(name="pick_instrument", fields=["i"],
                                          on="click", clear="dblclick", empty=False)
    instrument_view = (
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
        .properties(width=1060, height=300,
                    title=f"{len(instruments)} instruments, at the centroid of their units")
    )

    chart = (
        alt.vconcat(alt.hconcat(overview, detail), instrument_view)
        .add_params(proj)
        .configure_view(stroke=None)
        .configure_legend(labelFontSize=11, titleFontSize=11)
        .properties(
            title="UMAP of every text of the federal laws, reglamentos and lineamientos",
            usermeta={"embedOptions": {"renderer": "canvas", "actions": False}},
        )
    )
    return chart


def build(work_dir: Path, output: Path, *, unit_types=UNIT_TYPES, projections=None,
          neighbors: int = 15, text_chars: int = 0, background_points: int = 20000,
          overview_sample: int | None = None, nearest: bool = True, inline_js: bool = False,
          collections=tuple(COLLECTION_CODES), cache_dir=None, log=print) -> dict:
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

    chart = build_chart(points, instruments, found, neighbors=neighbors,
                        text_chars=text_chars, background_points=background_points,
                        overview_sample=overview_sample, nearest=nearest)
    spec = chart.to_dict()  # validates against the Vega-Lite schema

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    spec_path = output.with_suffix(".vl.json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    if inline_js:
        chart.save(str(output), format="html", inline=True)
    else:
        chart.save(str(output), format="html")

    # A reload is the cheap end-to-end check a session with no browser can
    # actually make: `from_dict` re-validates the spec it just wrote.
    alt.Chart.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))

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
    }
    log(json.dumps(measured, indent=2))
    log(f"{output}: {measured['html_bytes'] / 1e6:.1f} MB, {len(points)} points")
    return measured


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--unit-types", default=",".join(UNIT_TYPES),
                        help="comma-separated md2akn unit_types to draw")
    parser.add_argument("--projections", default=None,
                        help="comma-separated configuration names (default: every finished one)")
    parser.add_argument("--neighbors", type=int, default=15,
                        help="neighbours per point (0 drops the field and the layer)")
    parser.add_argument("--text-chars", type=int, default=0,
                        help="characters of the unit's own text to put in the tooltip")
    parser.add_argument("--background-points", type=int, default=20000)
    parser.add_argument("--overview-sample", type=int, default=None,
                        help="thin only the overview layer to N points (default: all)")
    parser.add_argument("--no-nearest", dest="nearest", action="store_false",
                        help="select the mark under the cursor instead of the nearest one "
                             "-- the escape hatch if the Voronoi over ~400k marks is slow")
    parser.add_argument("--inline-js", action="store_true",
                        help="embed vega/vega-lite/vega-embed instead of loading them from jsdelivr")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    random.seed(args.seed)
    build(
        args.work_dir, args.output,
        unit_types=tuple(t for t in args.unit_types.split(",") if t),
        projections=None if not args.projections else set(args.projections.split(",")),
        neighbors=args.neighbors, text_chars=args.text_chars,
        background_points=args.background_points, overview_sample=args.overview_sample,
        nearest=args.nearest, inline_js=args.inline_js, cache_dir=args.cache_dir,
    )


if __name__ == "__main__":
    main()
