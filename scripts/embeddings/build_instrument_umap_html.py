"""Turn the nearest-foreign-neighbour matrix into one standalone HTML page.

Issue #242, the second half: `instrument_matrix.py` counted, for every unit
of every instrument, which *other* instrument owns the nearest text; this
embeds the 1,523 rows of that matrix and draws them.

    uv run --group viz python scripts/embeddings/build_instrument_umap_html.py
    uv run --group viz python scripts/embeddings/build_instrument_umap_html.py \\
        --n-neighbors 8,16 --force --output output/instruments.html

An instrument is therefore represented by **where its articles' nearest
foreign neighbours live** — a 1,523-long distribution over the other
instruments — rather than by its own text. Two laws land together when their
articles point at the same places, which is a statement about the corpus'
structure rather than about vocabulary.

* Rows are L2-normalised (`normalise_rows`), so a 3,600-article code and a
  single-article lineamiento are compared by *where* they point, never by how
  much they point. A zero row would mean an instrument whose every unit
  failed to find a foreign neighbour, which cannot happen (1,522 candidates
  are always available) — so it raises rather than becoming a `nan`.
* Four UMAP fits, `n_neighbors` 4 / 8 / 16 / 32, `metric="cosine"`,
  `min_dist=0.1`, **`random_state=0`**: at 1,523 points the fit is seconds,
  so the single-threaded cost a seed forces is worth a reproducible page
  (the opposite of `project_umap.py`'s decision, for the opposite reason).
  They run on the login node; no Slurm job, nothing to wait for.
* Each projection is min-max scaled into `[0, 1]` with its raw range
  recorded, exactly as `project_umap.py` does, so the radio can switch
  between them without rescaling an axis.

The page (`output/umap-instruments-qwen3-0.6b.html` and its `.vl.json`) is
one layered scatter: colour by collection (legend-bound toggle), size by unit
count, a radio for `n_neighbors`, and a click that rings the picked
instrument in black and its ten strongest targets in red — the smallest
interaction that makes a *relation* visible. It carries the same provenance
footer #241's page does, through the same helpers.

The session that writes this file cannot click: what is verified here is the
spec (`chart.to_dict()` against the Vega-Lite schema, plus
`tests/test_instrument_matrix.py`); a person confirms the interaction in a
browser.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_umap_html as html  # noqa: E402
import instrument_matrix  # noqa: E402
from project_umap import apply_scale, scale_to_unit  # noqa: E402

#: The four neighbourhood sizes issue #242 asks for, smallest first (the
#: order the radio shows them in). 16 is the default because it is the middle
#: of the range at which the picture stops being filaments and has not yet
#: collapsed into one blob — the same range #241's third sweep settled on.
DEFAULT_N_NEIGHBORS = (4, 8, 16, 32)
DEFAULT_RADIO_VALUE = 16

DEFAULT_OUTPUT = Path("output/umap-instruments-qwen3-0.6b.html")

#: How many targets the tooltip lists, and how many the click rings. Five
#: lines is what fits in a tooltip; ten rings is what stays readable at 1,523
#: points.
TOP_TARGETS = 5
RING_TARGETS = 10

SCRIPT_PATH = "scripts/embeddings/build_instrument_umap_html.py"

#: Same colours as #241's unit-level page, so the two read as one pair.
COLLECTION_COLORS = {"leyes": "#4c78a8", "reglamentos": "#f58518",
                     "lineamientos": "#54a24b"}


def normalise_rows(matrix):
    """`A` with every row on the unit sphere, as `float32`.

    An instrument with no counts at all would divide by zero and reach UMAP
    as `nan`; it also cannot exist, since every unit row credits at least one
    of the 1,522 other instruments. So it is a failure to report, not a case
    to handle.
    """
    import numpy as np

    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    empty = np.flatnonzero(norms == 0)
    if empty.size:
        raise SystemExit(
            f"{empty.size} instrument(s) have an all-zero row ({empty[:10].tolist()}): "
            "no unit of theirs found a foreign neighbour, which means the matrix is "
            "not the one instrument_matrix.py writes"
        )
    return (matrix / norms[:, None]).astype(np.float32)


def fit_one(rows, n_neighbors: int, *, min_dist: float = 0.1, metric: str = "cosine",
            random_state: int = 0, log=print) -> tuple:
    """One UMAP fit, scaled into `[0, 1]`. Returns the coordinates and what
    `umap.json` records about the fit."""
    import umap

    mark = time.time()
    reducer = umap.UMAP(n_components=2, n_neighbors=n_neighbors, min_dist=min_dist,
                        metric=metric, random_state=random_state)
    embedding = reducer.fit_transform(rows)
    scaled, ranges = scale_to_unit(embedding)
    seconds = round(time.time() - mark, 1)
    log(f"n_neighbors={n_neighbors}: fit in {seconds}s")
    return scaled, {
        "n_neighbors": n_neighbors,
        "min_dist": min_dist,
        "metric": metric,
        "random_state": random_state,
        "seconds": seconds,
        "x_range": ranges[0],
        "y_range": ranges[1],
        "umap_version": getattr(umap, "__version__", None),
    }


def project(work_dir: Path, *, n_neighbors=DEFAULT_N_NEIGHBORS, force: bool = False,
            log=print) -> dict:
    """Fit every configuration over the normalised matrix and write
    `umap.parquet`/`umap.json` beside it. A no-op once they exist."""
    import numpy as np
    import pyarrow as pa

    from _atomic import atomic_write_table, atomic_write_text

    out_dir = instrument_matrix.output_dir(work_dir)
    matrix_path = out_dir / "matrix.npy"
    if not (out_dir / ".done").exists() or not matrix_path.exists():
        raise SystemExit(f"{matrix_path} is missing -- run instrument_matrix.py first "
                         "(it is a Slurm job: --submit, then --wait)")
    summary_path = out_dir / "umap.json"
    table_path = out_dir / "umap.parquet"
    if table_path.exists() and summary_path.exists() and not force:
        log(f"{table_path} exists, kept -- pass --force to refit")
        return json.loads(summary_path.read_text(encoding="utf-8"))

    rows = normalise_rows(np.load(matrix_path))
    log(f"{rows.shape[0]} instruments x {rows.shape[1]} targets, rows L2-normalised")

    columns = {"i": pa.array(range(rows.shape[0]), type=pa.int32())}
    fits = []
    for k in n_neighbors:
        coordinates, fit = fit_one(rows, k, log=log)
        columns[f"x{k}"] = pa.array(coordinates[:, 0], type=pa.float32())
        columns[f"y{k}"] = pa.array(coordinates[:, 1], type=pa.float32())
        fits.append(fit)

    atomic_write_table(table_path, pa.table(columns))
    summary = {"instruments": int(rows.shape[0]), "n_neighbors": list(n_neighbors),
               "fits": fits, "seconds_total": round(sum(f["seconds"] for f in fits), 1)}
    atomic_write_text(summary_path,
                      json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def top_targets(matrix, instruments, index: int, limit: int = TOP_TARGETS) -> str:
    """The `limit` instruments this one points at hardest, as
    `"nombre (count)"` lines for the tooltip."""
    import numpy as np

    row = matrix[index]
    order = np.argsort(row)[::-1][:limit]
    return "\n".join(f"{instruments['nombre'].iloc[int(j)]} ({int(row[j])})"
                     for j in order if row[j] > 0)


def instrument_points(work_dir: Path, *, log=print):
    """One row per instrument: its coordinates in the four projections, both
    directions of the matrix, and the targets the page needs.

    `out` is the row sum — how many of its units found a foreign neighbour,
    counting every tied owner; `in` is the column sum — how many foreign
    units point *at* it, which the directed matrix would otherwise hide. `t`
    is its ten strongest targets as one comma-separated string, the same
    trick #241's page uses for a point's neighbours: one short string beats a
    JSON array by ~40 % over thousands of rows, and Vega's `split` reads it
    back in the filter.
    """
    import numpy as np
    import pyarrow.parquet as pq

    out_dir = instrument_matrix.output_dir(work_dir)
    matrix = np.load(out_dir / "matrix.npy")
    instruments = pq.read_table(Path(work_dir) / "instruments.parquet").to_pandas()
    coordinates = pq.read_table(out_dir / "umap.parquet").to_pandas()

    points = instruments[["i", "coleccion", "clave", "nombre", "units"]].copy()
    points["out"] = matrix.sum(axis=1).astype("int64")
    points["in"] = matrix.sum(axis=0).astype("int64")
    points["top"] = [top_targets(matrix, instruments, i) for i in range(len(points))]
    points["t"] = [",".join(str(int(j)) for j in np.argsort(matrix[i])[::-1][:RING_TARGETS]
                            if matrix[i, j] > 0)
                   for i in range(len(points))]
    points = points.merge(coordinates, on="i", how="inner")
    log(f"{len(points)} instruments, {int(matrix.sum())} counts")
    return points


def coordinate_expr(prefix: str, values, param: str = "nn") -> str:
    """The `calculate` expression that picks one projection's coordinate,
    driven by the `n_neighbors` radio.

    `build_umap_html.projection_expr`'s shape, keyed by the value rather than
    by an index: here the radio's own value *is* `n_neighbors`, so the page
    can say `n_neighbors=16` without a lookup table, and the columns are
    named `x16`/`y16` for the same reason.
    """
    values = list(values)
    expr = f"datum.{prefix}{values[-1]}"
    for value in reversed(values[:-1]):
        expr = f"{param} === {value} ? datum.{prefix}{value} : ({expr})"
    return expr


def build_chart(points, *, n_neighbors=DEFAULT_N_NEIGHBORS, radio_value=DEFAULT_RADIO_VALUE,
                provenance: dict | None = None):
    """The whole spec: one layered scatter with a radio, a legend toggle and
    a click that shows an instrument's strongest targets."""
    import altair as alt

    alt.data_transformers.disable_max_rows()

    nn = alt.param(
        name="nn", value=radio_value,
        bind=alt.binding_radio(options=list(n_neighbors),
                               labels=[f"n_neighbors={k} " for k in n_neighbors],
                               name="projection "),
    )
    # `nearest` stays off: it makes Vega-Lite insert a hidden Voronoi mark
    # that captures the pointer, and every tooltip then reads `undefined`
    # (issue #241's own finding, measured on the unit-level page).
    pick = alt.selection_point(name="pick", fields=["i", "t"], on="click",
                               nearest=False, clear="dblclick", empty=False)
    collections = alt.selection_point(name="collections", fields=["coleccion"],
                                      bind="legend")

    color = alt.Color("coleccion:N",
                      scale=alt.Scale(domain=list(COLLECTION_COLORS),
                                      range=list(COLLECTION_COLORS.values())),
                      legend=alt.Legend(title="collection"))
    x = alt.X("x:Q", scale=alt.Scale(domain=[0, 1]), axis=None, title=None)
    y = alt.Y("y:Q", scale=alt.Scale(domain=[0, 1]), axis=None, title=None)
    tooltip = [
        alt.Tooltip("nombre:N", title="instrument"),
        alt.Tooltip("clave:N", title="clave"),
        alt.Tooltip("coleccion:N", title="collection"),
        alt.Tooltip("units:Q", title="units"),
        alt.Tooltip("out:Q", title="units pointing out"),
        alt.Tooltip("in:Q", title="foreign units pointing here"),
        alt.Tooltip("top:N", title="strongest targets"),
    ]

    def framed():
        return alt.Chart(points).transform_calculate(
            x=coordinate_expr("x", n_neighbors),
            y=coordinate_expr("y", n_neighbors),
        )

    scatter = (
        framed()
        .mark_circle()
        .encode(x=x, y=y, color=color,
                # Log, because `units` runs from 1 to 3,663: on a linear scale
                # every lineamiento would be the same invisible dot.
                size=alt.Size("units:Q", scale=alt.Scale(type="log", range=[15, 400]),
                              legend=alt.Legend(title="units")),
                opacity=alt.condition(collections, alt.value(0.75), alt.value(0.03)),
                tooltip=tooltip)
        .add_params(pick, collections)
    )
    targets = (
        framed()
        .transform_filter(
            "isValid(pick.t) && indexof(split(pick.t[0], ','), toString(datum.i)) >= 0")
        .mark_point(size=260, filled=False, stroke="#d62728", strokeWidth=1.5)
        .encode(x=x, y=y, tooltip=tooltip)
    )
    picked = (
        framed()
        .transform_filter("isValid(pick.i) && indexof(pick.i, datum.i) >= 0")
        .mark_point(size=420, filled=False, stroke="#000000", strokeWidth=2)
        .encode(x=x, y=y, tooltip=tooltip)
    )
    label = (
        framed()
        .transform_filter("isValid(pick.i) && indexof(pick.i, datum.i) >= 0")
        .mark_text(align="center", baseline="bottom", dy=-16, fontSize=11, limit=520)
        .encode(x=x, y=y, text=alt.Text("nombre:N"))
    )

    usermeta = {"embedOptions": {"renderer": "canvas", "actions": False}}
    if provenance is not None:
        usermeta["provenance"] = provenance
    return (
        alt.layer(scatter, targets, picked, label)
        .add_params(nn)
        .properties(
            width=1100, height=760,
            title=alt.TitleParams(
                f"{len(points)} federal instruments, placed by where their units' "
                "nearest foreign neighbours live",
                subtitle=[
                    "each point is a law, reglamento or lineamiento; size = its units,"
                    " colour = its collection (click the legend to hide one)",
                    "click a point: black ring + name on it, ○ red rings on the "
                    f"{RING_TARGETS} instruments its units point at hardest; "
                    "double-click to clear",
                    "the matrix is directed: the tooltip's two counts are the units"
                    " pointing out and the foreign units pointing here",
                ]),
            usermeta=usermeta,
        )
        .configure_view(stroke=None)
        .configure_legend(labelFontSize=11, titleFontSize=11)
    )


def provenance_record(work_dir: Path, n_neighbors, *, argv=None, now=None) -> dict:
    """#241's own record, with the four `n_neighbors` in place of its
    projection directory names."""
    from datetime import datetime, timezone

    argv = list(sys.argv if argv is None else argv)
    return {
        "script": SCRIPT_PATH,
        "commit": html.repository_commit(),
        "date": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "work_dir": str(Path(work_dir).resolve()),
        "argv": " ".join(argv),
        "n_neighbors": list(n_neighbors),
        "projections": [f"n_neighbors={k}" for k in n_neighbors],
    }


def build(work_dir: Path, output: Path, *, n_neighbors=DEFAULT_N_NEIGHBORS,
          force: bool = False, inline_js: bool = False, argv=None, log=print) -> dict:
    """Fit what is missing, write the HTML and its `.vl.json`, return what was
    measured."""
    import altair as alt

    work_dir = Path(work_dir)
    fits = project(work_dir, n_neighbors=n_neighbors, force=force, log=log)
    points = instrument_points(work_dir, log=log)

    provenance = provenance_record(work_dir, n_neighbors, argv=argv)
    chart = build_chart(points, n_neighbors=n_neighbors, provenance=provenance)
    spec = chart.to_dict()  # validates against the Vega-Lite schema

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    spec_path = output.with_suffix(".vl.json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    chart.save(str(output), format="html", inline=inline_js)
    # The same post-processing #241 settled on: Altair's saver has no hook for
    # anything outside the chart, and the footer goes after its `div`.
    output.write_text(
        html.insert_footer(output.read_text(encoding="utf-8"),
                           html.footer_html(provenance)),
        encoding="utf-8",
    )
    alt.Chart.from_dict(json.loads(spec_path.read_text(encoding="utf-8")), validate=False)

    measured = {
        "output": str(output),
        "spec": str(spec_path),
        "html_bytes": output.stat().st_size,
        "spec_bytes": spec_path.stat().st_size,
        "instruments": len(points),
        "n_neighbors": list(n_neighbors),
        "seconds_fit": fits.get("seconds_total"),
        "provenance": provenance,
    }
    log(json.dumps(measured, indent=2, ensure_ascii=False))
    log(f"{output}: {measured['html_bytes'] / 1e6:.1f} MB, {len(points)} instruments")
    log(f"HTML: {output.resolve()}")
    log(f"spec: {spec_path.resolve()}")
    return measured


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-neighbors", default=",".join(str(k) for k in DEFAULT_N_NEIGHBORS),
                        help="comma-separated neighbourhood sizes, one fit each")
    parser.add_argument("--force", action="store_true",
                        help="refit even if umap.parquet is already there")
    parser.add_argument("--inline-js", action="store_true",
                        help="embed vega/vega-lite/vega-embed instead of loading them "
                             "from jsdelivr")
    args = parser.parse_args(argv)

    build(args.work_dir, args.output,
          n_neighbors=tuple(int(k) for k in args.n_neighbors.split(",") if k),
          force=args.force, inline_js=args.inline_js,
          argv=[SCRIPT_PATH] + list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    main()
