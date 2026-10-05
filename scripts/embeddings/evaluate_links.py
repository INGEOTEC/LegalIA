"""Score the Atlas data sets against the strong-link gold set (issue #272).

`gold_links.py` builds the gold: reglamentos and lineamientos whose own name (A)
or own "objeto" sentence (B) names the law they develop. This scores the
directed instrument matrix `A` of each data set (`<work-dir>/instrument-matrix/
matrix.npy`) against it, **at the instrument level**, for every gold instrument
`i` with gold laws `G` and row `A[i]`:

* **closest** -- a gold law is the instrument's closest one (rank 1);
* **top-5** -- a gold law is among its five closest (rank <= 5);
* **reciprocal rank** -- `1 / rank` of the best-ranked gold law (0 when every
  gold law has weight 0);
* **weight share** -- `sum(A[i, G]) / A[i].sum()`: how much of the row's weight
  survives on the link.

The rank of a gold law is the *competition* rank: `1 +` the number of
instruments whose weight is greater than the best gold law's by more than
`TIE_TOLERANCE`. Equal weights are frequent (BM25's rows especially), and the
optimistic rank is the standard choice; it is also what makes "closest" not
depend on column order, which `argmax` would (the laws come first in `i`).
`ties_at_top` counts the instruments whose "closest" was a tie with a non-gold
instrument, so the reader can see how much that convention gave.

An instrument whose row sums to 0 in *any* model is skipped (and counted) in all
of them, so the models are compared on the very same instruments.

    python evaluate_links.py                                # the three defaults
    python evaluate_links.py --work-dir 0.6B=emb-run-atlas --work-dir 4B=emb-run-atlas-4b
    python evaluate_links.py --report                       # print the table, offline

**Significance, fixed before looking at the numbers (issue #272).** For every
pair of models and every metric, a paired bootstrap over the gold instruments
(`--seed` 0, `--resamples` 10,000) gives the 95 % percentile interval of the mean
difference; for the two binary metrics (closest, top-5) McNemar's exact test
(`scipy.stats.binomtest` on the discordant pairs, two-sided) is added. A difference
is **reported as real only if the bootstrap interval excludes 0 and, for a binary
metric, McNemar's p < 0.05**; otherwise the pair is a **tie** on that metric. With
a few hundred cases and differences of a few instruments, a tie is the expected,
acceptable verdict.

Two gold sets are always scored: `A` alone, the near-certain control, and `A+B`,
the union (`--signals` narrows to one). The gold is read from
`--gold` (default `emb-run-atlas/gold-links/gold.json`); every `--work-dir` must
hold the very instruments the gold was built over (`coleccion`, `clave`, in `i`
order) and a `matrix.npy` of the right shape. Nothing here rebuilds either, and
nothing needs the network.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic import atomic_write_text  # noqa: E402
from instrument_matrix import SUBDIR as MATRIX_SUBDIR  # noqa: E402

DEFAULT_GOLD = Path("emb-run-atlas/gold-links/gold.json")
DEFAULT_WORK_DIRS = (("0.6B", "emb-run-atlas"), ("4B", "emb-run-atlas-4b"),
                     ("BM25", "emb-run-atlas-bm25"))

#: Weights differing by no more than this are equal. Rows of `A` are sums of
#: `1/m` in float32.
TIE_TOLERANCE = 1e-6

TOP_K = 5
BOOTSTRAP_LEVEL = 95.0
P_THRESHOLD = 0.05

#: `(key, label, binary)`.
METRICS = (
    ("closest", "closest is a gold law", True),
    ("top5", "a gold law among the five closest", True),
    ("mrr", "mean reciprocal rank", False),
    ("share", "weight share on gold laws", False),
)

SIGNAL_SETS = ("A", "A+B")


def load_gold(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} does not exist: run gold_links.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def gold_sets(gold: dict, signal_set: str) -> dict[int, list[int]]:
    """`{instrument i: [gold law i]}` of one signal set: `A` is the laws the
    instrument's name gives, `A+B` the union with its "objeto" sentence's."""
    key = "laws_A" if signal_set == "A" else "laws"
    return {int(e["i"]): list(e[key]) for e in gold["entries"] if e[key]}


def load_matrix(label: str, work_dir: Path, gold: dict) -> np.ndarray:
    """The matrix of `work_dir`, refused unless the work directory lists the
    gold's instruments (`export_atlas_data.check_same_instruments`' idea, on the
    parquet: `coleccion` and `clave` at every `i`) and the shape fits."""
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    table_path = work_dir / "instruments.parquet"
    if not table_path.is_file():
        raise SystemExit(f"{label}: {table_path} does not exist")
    instruments = pq.read_table(table_path).to_pandas().sort_values("i")
    mine = [[row["coleccion"], str(row["clave"])] for _, row in instruments.iterrows()]
    theirs = [[c, str(k)] for c, k in gold["instruments"]]
    if len(mine) != len(theirs):
        raise SystemExit(f"{label}: {table_path} lists {len(mine)} instruments, the gold was "
                         f"built over {len(theirs)}")
    for position, (a, b) in enumerate(zip(mine, theirs)):
        if a != b:
            raise SystemExit(f"{label}: instrument {position} is {a} in {table_path} but {b} "
                             "in the gold -- the two do not list the same instruments in the "
                             "same positions")
    matrix_path = work_dir / MATRIX_SUBDIR / "matrix.npy"
    if not matrix_path.is_file():
        raise SystemExit(f"{label}: {matrix_path} does not exist (this script never rebuilds "
                         "it: run instrument_matrix.py)")
    matrix = np.load(matrix_path)
    if matrix.shape != (len(mine), len(mine)):
        raise SystemExit(f"{label}: {matrix_path} has shape {matrix.shape}, expected "
                         f"{(len(mine), len(mine))}")
    return matrix


def row_metrics(row: np.ndarray, laws, *, tolerance: float = TIE_TOLERANCE) -> dict | None:
    """The four metrics of one row against its gold `laws`, or `None` when the
    row sums to 0 (an instrument none of whose unit rows was counted)."""
    total = float(row.sum())
    if total <= 0:
        return None
    weights = row[list(laws)]
    best = float(weights.max())
    if best <= 0:
        rank = None
    else:
        rank = 1 + int((row > best + tolerance).sum())
    tied = (rank == 1
            and int((np.abs(row - best) <= tolerance).sum())
            > int((np.abs(weights - best) <= tolerance).sum()))
    return {
        "closest": rank == 1,
        "top5": rank is not None and rank <= TOP_K,
        "mrr": 0.0 if rank is None else 1.0 / rank,
        "share": float(weights.sum()) / total,
        "tied_at_top": bool(tied),
    }


def metric_table(matrices: dict[str, np.ndarray], gold_by_instrument: dict[int, list[int]]):
    """Per model, one array per metric over the instruments every model can
    score; also the instruments skipped (a zero row in any model), per model."""
    scorable = []
    skipped: dict[str, int] = {label: 0 for label in matrices}
    for i in sorted(gold_by_instrument):
        zero = [label for label, matrix in matrices.items() if matrix[i].sum() <= 0]
        if zero:
            for label in zero:
                skipped[label] += 1
        else:
            scorable.append(i)
    values = {label: {key: [] for key, _, _ in METRICS} | {"tied_at_top": []}
              for label in matrices}
    for label, matrix in matrices.items():
        for i in scorable:
            result = row_metrics(matrix[i], gold_by_instrument[i])
            for key in values[label]:
                values[label][key].append(float(result[key]))
    arrays = {label: {key: np.asarray(vals, dtype=float) for key, vals in per.items()}
              for label, per in values.items()}
    return scorable, arrays, skipped


def paired_bootstrap(difference: np.ndarray, indices: np.ndarray, level: float = BOOTSTRAP_LEVEL):
    """The percentile interval of the mean of `difference` over `indices`
    (`resamples x n` positions drawn with replacement)."""
    means = difference[indices].mean(axis=1)
    low, high = np.percentile(means, [(100 - level) / 2, 100 - (100 - level) / 2])
    return float(low), float(high)


def mcnemar_exact(a: np.ndarray, b: np.ndarray) -> dict:
    """McNemar's exact test on two paired binary arrays: the discordant counts
    and the two-sided binomial p-value (1.0 with no discordant pair)."""
    from scipy.stats import binomtest

    only_a = int(((a == 1) & (b == 0)).sum())
    only_b = int(((a == 0) & (b == 1)).sum())
    total = only_a + only_b
    p = 1.0 if total == 0 else float(binomtest(min(only_a, only_b), total, 0.5).pvalue)
    return {"only_first": only_a, "only_second": only_b, "p": p}


def compare(first: np.ndarray, second: np.ndarray, binary: bool, indices: np.ndarray,
            *, names=("first", "second")) -> dict:
    """The verdict for one pair of models on one metric. `real` only if the
    bootstrap interval of `first - second` excludes 0 and, for a binary
    metric, McNemar's p < `P_THRESHOLD`."""
    difference = first - second
    low, high = paired_bootstrap(difference, indices)
    result = {"difference": float(difference.mean()), "interval": [low, high], "p": None}
    excludes = low > 0 or high < 0
    real = excludes
    if binary:
        test = mcnemar_exact(first, second)
        result.update(p=test["p"], only_first=test["only_first"], only_second=test["only_second"])
        real = excludes and test["p"] < P_THRESHOLD
    result["verdict"] = ("tie" if not real
                         else f"{names[0] if result['difference'] > 0 else names[1]} better")
    return result


def evaluate_set(matrices: dict[str, np.ndarray], gold_by_instrument: dict[int, list[int]],
                 *, seed: int, resamples: int) -> dict:
    scorable, arrays, skipped = metric_table(matrices, gold_by_instrument)
    n = len(scorable)
    result = {"gold_instruments": len(gold_by_instrument), "scored": n,
              "skipped_zero_row": skipped, "models": {}, "pairs": []}
    for label, per in arrays.items():
        result["models"][label] = {
            key: {"mean": float(per[key].mean()) if n else None,
                  "count": int(per[key].sum()) if binary else None}
            for key, _, binary in METRICS}
        result["models"][label]["tied_at_top"] = int(per["tied_at_top"].sum())
    if n == 0:
        return result
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(resamples, n))
    for first, second in itertools.combinations(matrices, 2):
        for key, _, binary in METRICS:
            result["pairs"].append({
                "first": first, "second": second, "metric": key,
                **compare(arrays[first][key], arrays[second][key], binary, indices,
                          names=(first, second))})
    return result


def evaluate(gold: dict, work_dirs: dict[str, Path], *, signals=SIGNAL_SETS, seed: int = 0,
             resamples: int = 10000) -> dict:
    matrices = {label: load_matrix(label, path, gold) for label, path in work_dirs.items()}
    sets = {name: evaluate_set(matrices, gold_sets(gold, name), seed=seed, resamples=resamples)
            for name in signals}
    return {
        "thresholds": {
            "rule": ("a difference is real only if the paired-bootstrap interval of the mean "
                     "difference excludes 0 and, for the binary metrics (closest, top5), "
                     "McNemar's exact test has p < p_threshold; otherwise a tie"),
            "bootstrap_level": BOOTSTRAP_LEVEL, "resamples": resamples, "seed": seed,
            "p_threshold": P_THRESHOLD, "tie_tolerance": TIE_TOLERANCE, "top_k": TOP_K,
            "rank": "competition rank (1 + instruments with strictly greater weight)",
        },
        "gold": {"provenance": gold["provenance"], "summary": gold["summary"]},
        "models": {label: str(path) for label, path in work_dirs.items()},
        "signal_sets": sets,
    }


def render_markdown(evaluation: dict) -> str:
    """`evaluation.md`: one metric table and one verdict table per signal set."""
    lines = ["# Atlas data sets against strong reglamento -> law links", ""]
    rule = evaluation["thresholds"]
    lines += [f"Significance (fixed beforehand): paired bootstrap, seed {rule['seed']}, "
              f"{rule['resamples']} resamples, {rule['bootstrap_level']:.0f} % percentile "
              f"interval must exclude 0 and, for closest / top-5, McNemar exact p < "
              f"{rule['p_threshold']}; otherwise a tie.", ""]
    for name, result in evaluation["signal_sets"].items():
        labels = list(result["models"])
        lines += [f"## Gold {name}: {result['scored']} instruments scored "
                  f"(of {result['gold_instruments']})", ""]
        if any(result["skipped_zero_row"].values()):
            lines += [f"Skipped (zero row): {result['skipped_zero_row']}", ""]
        lines += ["| metric | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
        for key, label, binary in METRICS:
            cells = []
            for model in labels:
                entry = result["models"][model][key]
                cells.append(f"{entry['count']} ({entry['mean']:.3f})" if binary
                             else f"{entry['mean']:.3f}")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("| closest ties with a non-gold instrument | "
                     + " | ".join(str(result["models"][m]["tied_at_top"]) for m in labels) + " |")
        lines += ["", "| pair | metric | difference | 95 % interval | McNemar p | verdict |",
                  "|---|---|---|---|---|---|"]
        for pair in result["pairs"]:
            low, high = pair["interval"]
            p = "" if pair["p"] is None else f"{pair['p']:.4f}"
            lines.append(f"| {pair['first']} - {pair['second']} | {pair['metric']} | "
                         f"{pair['difference']:+.4f} | [{low:+.4f}, {high:+.4f}] | {p} | "
                         f"{pair['verdict']} |")
        lines.append("")
    return "\n".join(lines)


def parse_work_dirs(values) -> dict[str, Path]:
    if not values:
        return {label: Path(path) for label, path in DEFAULT_WORK_DIRS}
    parsed = {}
    for value in values:
        label, sep, path = value.partition("=")
        if not sep or not label or not path:
            raise SystemExit(f"--work-dir takes LABEL=PATH, got {value!r}")
        parsed[label] = Path(path)
    if len(parsed) < 2:
        raise SystemExit("--work-dir needs at least two models to compare")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--work-dir", action="append", default=None, metavar="LABEL=PATH",
                        help="a model to score, repeatable (default: 0.6B=emb-run-atlas "
                             "4B=emb-run-atlas-4b BM25=emb-run-atlas-bm25)")
    parser.add_argument("--output", type=Path, default=None,
                        help="evaluation.json (default: next to --gold)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--signals", choices=("all",) + SIGNAL_SETS, default="all")
    parser.add_argument("--report", action="store_true",
                        help="print the table from the written evaluation.json and exit")
    args = parser.parse_args(argv)

    output = args.output or args.gold.parent / "evaluation.json"
    if args.report:
        if not output.is_file():
            raise SystemExit(f"{output} does not exist: run evaluate_links.py first")
        print(render_markdown(json.loads(output.read_text(encoding="utf-8"))))
        return 0

    gold = load_gold(args.gold)
    signals = SIGNAL_SETS if args.signals == "all" else (args.signals,)
    evaluation = evaluate(gold, parse_work_dirs(args.work_dir), signals=signals,
                          seed=args.seed, resamples=args.resamples)
    atomic_write_text(output, json.dumps(evaluation, ensure_ascii=False, indent=1) + "\n")
    markdown = render_markdown(evaluation)
    atomic_write_text(output.with_suffix(".md"), markdown)
    print(markdown)
    print(f"wrote {output} and {output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
