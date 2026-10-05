# `scripts/embeddings/` — a vector for every text of every federal instrument

Fase 2 of issue #218 (the implementation plan for #217's research), widened
to all three SCJN corpora by issue #227's Fase 3: the Slurm build that turns
`md2akn.text_units()`'s output into one vector per distinct text of the
cached `scjn-leyes`, `scjn-reglamentos` and `scjn-lineamientos` releases.
This is orchestration for one research run on one specific cluster
(`headmaster` + `cemieredes`), not a package — it talks to Slurm, to Hugging
Face Hub, and to this machine's own `/home` layout, none of which belongs
inside `packages/`.

**This has been run, end to end, against the real cluster and both real
models** — six model × corpus runs (issue #227's Fase 3; the leyes half was
first run for #218 and rebuilt here under rules 8 and 9). What is *not*
automated is the publish: no GitHub Action creates or uploads a release
here, and a human runs the `PUBLICAR.md` the packaging step generates (issue
#115, Hallazgo C).

## One work directory per collection

`--coleccion {leyes,reglamentos,lineamientos}` picks the corpus (default
`leyes`, so the #218 command keeps working), and each gets its own work
directory: `emb-run-leyes/`, `emb-run-reglamentos/`, `emb-run-lineamientos/`.
Dedup stays inside a collection — measured at 0.9 % of the vectors saved by
deduplicating across them, against a shared file three independently
crawled, packaged and republished corpora would all have to be published
against (issue #227's decision 8).

`units.parquet` carries `coleccion` and `clave`: `clave` is a law's slug and
the `id_ordenamiento` of a reglamento or a lineamiento, and it is what names
every published file, so a reader never branches. `slug` and `codNota` are
null for the id-keyed collections, which have no DOF link at all (#220).

## The work directory

Everything lives under one directory per run, on `/home` (`--work-dir`, or
`$LEGALIA_EMB_WORK`) — never `/tmp`, which is not shared between
`headmaster` and the compute nodes on this cluster (verified: a file written
to `/tmp` on `headmaster` is invisible inside a job).

```
work/
  units.parquet  leaves.parquet  corpus-manifest.json
  shards.json
  runs/<model-slug>/
      shard-0000.parquet  shard-0000.done  shard-0000.json
      shard-0001.parquet.parcial
      shard-0002.failed
      vectors/
          vectors-<clave>-<model-slug>-<K>.parquet
          vectors-shared-<model-slug>-<K>.parquet
      manifest.json
```

## The sequence

```bash
# Once: populate the scjn/dofjson caches this reads from -- no network from here on.
nota2md download all

# 1. The corpus, as two Parquet files + a manifest. Once per collection:
#    --no-split-articles (issue #256) turns rules 3/9 off for articles: one
#    article is one unit whatever its length, truncated at the model's own
#    window rather than split. Omit it to keep the default, cap-2000 shape
#    that reproduces a pre-#256 vector set byte for byte.
python scripts/embeddings/build_units.py --work-dir emb-run-leyes --no-split-articles
python scripts/embeddings/build_units.py --work-dir emb-run-reglamentos \
    --coleccion reglamentos --no-split-articles
python scripts/embeddings/build_units.py --work-dir emb-run-lineamientos \
    --coleccion lineamientos --no-split-articles

# 2. Dedup by text_sha1, sort by estimated token length, balance into shards.
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes --num-shards 12

# 3. One model at a time: download it, submit every pending shard, wait, retry, delete it.
#    --max-batch-tokens (issue #256) replaces the fixed --batch-size as what
#    governs memory; --batch-size stays as the upper bound on a batch's row
#    count. See "encode_shard.py: window truncation and token-budget
#    batching" below, and "submit_jobs.py: why its wait is chunked too" for
#    --max-wait-minutes.
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B --max-batch-tokens 20000
python scripts/embeddings/status.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B

# 4. Once every shard is done: the per-instrument vector files + the manifest.
python scripts/embeddings/merge_shards.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B

# repeat 3-4 for the second model
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-4B
python scripts/embeddings/merge_shards.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-4B

# 5. Once every model has merged: the release assets + PUBLICAR.md (never uploaded here).
python scripts/embeddings/package_vectors.py --work-dir emb-run-leyes \
    --coleccion leyes --out-dir emb-run-leyes/publish

# 6. A human reads PUBLICAR.md and runs it. Its upload step is this, per part --
#    resumable, because GitHub's secondary rate limit cuts a thousand-asset
#    upload in half and `xargs` leaves no record of what landed.
python scripts/embeddings/upload_release_assets.py scjn-leyes-vectors \
    emb-run-leyes/publish/parte-1.txt
```

Steps 2-4 repeat per collection against its own work directory; nothing
below `build_units.py`/`merge_shards.py` knows what a collection is — a
shard is keyed by `text_sha1` alone.

**Migrating an already-published collection to whole articles** (issue
#256's own run): build into a *new* work directory,
`emb-run-whole-<coleccion>/`, rather than the one that already holds the
published (split-article) vectors — `package_vectors.py`'s `PUBLICAR.md`
still targets the same release tags, replacing them in place, but nothing
here overwrites the old work directory while the new one is being built.

### `encode_shard.py`: window truncation and token-budget batching (issue #256)

An article is never split any more when `--no-split-articles` built the
corpus, so a handful of texts are longer than a model's own context window
(measured against the pre-#256 `units.parquet` files: 4 exceed the 0.6B's
32,768 tokens, 2 the 4B's 40,960). Those are **truncated at the model's own
`max_position_embeddings`**, read from its config rather than hard-coded —
never split, windowed or mean-pooled (the user's own decision) — and every
truncated text is recorded, by `text_sha1` and its real (pre-truncation)
token count, in that shard's own `.done` marker and carried into
`merge_shards.py`'s `manifest.json` as `texts_truncated` /
`max_tokens_embedded`.

The fixed `--batch-size` a shard's texts used to be embedded under (default
32, sorted by character length) is replaced by batches built in **token**
order under a padded-token budget, `--max-batch-tokens`: a batch's cost is
its own longest text's token count times its row count, since every row in
a batch pads out to the batch's longest sequence. `--batch-size` stays as
the upper bound on a batch's row count. A text whose own token count already
exceeds `--max-batch-tokens` is embedded alone, in a batch of one, rather
than blocking every shorter text from ever batching with it.

### `submit_jobs.py`: why its wait is chunked too (issue #256)

Same reason, same shape as `submit_umap.py`'s own chunked wait (below):
`submit_jobs.py --model ... ` used to block on `squeue` with no ceiling,
which an automated session driving this cannot hold. `--max-wait-minutes N`
returns exit status 75 ("still running") while shard jobs remain queued or
running, and the caller simply calls the same command again — the state
(the attempt's own job ids) lives in `runs/<model-slug>/jobs.json`, not in a
process that has to stay alive. `0` (the default) blocks until every
pending shard finishes, the pre-#256 behaviour. `--report` prints the
pending-shard count offline, no Slurm at all. Unlike `submit_umap.py`
there is no `--max-wait-hours` ceiling or `scancel` here: a shard job that
does not finish is handled by `submit_jobs.py`'s own `--max-attempts` retry
loop, resumed the same chunked way on the next call.

**`submit_jobs.py --mem` (default `32G`) is not optional in practice**, even
though `submit.sh` itself carries no `#SBATCH --mem`. Found running #256's
own GPU phase: with another user's job holding two of `cemieredes`' three
A100s (and part of the node's ~1 TB of memory), a shard job submitted with
no `--mem` sat `PENDING (Resources)` indefinitely even once a GPU freed up
— on this cluster, "no `--mem` given" resolves to reserving the *entire*
node's memory for the job (`DefMemPerNode=UNLIMITED` at the partition
level apparently means "no accounting", not "use what's free"), so the job
was waiting for the other one to vacate the whole node, not just a GPU.
`--mem=32G` (comfortably more than one shard needs) fixed it immediately.

Incremental re-embedding after a reform: replan against what a model already
has, then resubmit — only the new units reach the GPU at all.

```bash
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes \
    --against emb-run-leyes/runs/qwen3-0.6b
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
```

## The shared cluster venv had drifted (found running issue #256)

`/home/mgraffg/.venvs/cluster` (`submit.sh`'s hardcoded interpreter, shared
with `../Chimalli-overleaf`) had `torch==2.14.0+cu130` — a build needing a
newer NVIDIA driver than `cemieredes` actually has (525.116.04, CUDA 12.0).
`torch.cuda.is_available()` silently returned `False` with no error, so
`encode_shard.py`'s `device_map="auto"` fell back to CPU without complaint
(`"Device set to use cpu"` in the Slurm log is the only sign). Fixed by
reinstalling a driver-compatible build:

```bash
uv pip install --python /home/mgraffg/.venvs/cluster/bin/python \
    "torch==2.4.1" torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cu121
uv pip install --python /home/mgraffg/.venvs/cluster/bin/python accelerate
```

(`accelerate` was also missing — newer `transformers` needs it for
`device_map="auto"`, which #227's original run predates.) Check
`torch.cuda.is_available()` returns `True` under `srun --gres=gpu:1` before
trusting a "successful" shard: a CPU fallback still writes a `.done` marker,
just very slowly, and would otherwise go unnoticed until someone asks why
a 20-text shard took hours.

## The CPU smoke test

No cluster, no Slurm, no GPU — proves the whole pipeline (units -> shards ->
vectors -> merge) end to end. Still downloads the real 0.6B model's weights
(~1.2 GB) the first time it runs, since `encode_shard.py` has no fake mode of
its own:

```bash
python scripts/embeddings/build_units.py --work-dir ~/smoke --slug lft
python scripts/embeddings/plan_shards.py --work-dir ~/smoke --num-shards 1
python scripts/embeddings/encode_shard.py --work-dir ~/smoke \
    --model Qwen/Qwen3-Embedding-0.6B --shard 0 --device cpu --limit 512
python scripts/embeddings/merge_shards.py --work-dir ~/smoke \
    --model Qwen/Qwen3-Embedding-0.6B
```

`scripts/embeddings/tests/` covers the surrounding logic — atomic writes,
shard balancing, the `.done`/`.failed` marker contract, merge-by-hash —
against small fixtures and a monkeypatched pipeline, with no download and no
dependency on `torch`/`transformers` being importable at all.

## Design points worth restating (see issue #218 for the measurements)

- A shard is keyed by `text_sha1` alone: `encode_shard.py` never learns
  which instrument a text came from, so a text four laws share gets embedded
  once. `merge_shards.py` is where the instrument comes back, from
  `units.parquet`'s own `text_sha1 -> clave` map.
- `units.parquet` is byte-reproducible from the same release, cap and
  template — the row order is `(coleccion, clave, document order)`. Rebuilding
  the 315 laws under issue #227's rule 8 with rule 9 off
  (`--no-split-over-cap`) reproduced the pre-#227 file exactly, one byte
  apart: the pyarrow writer version in the Parquet footer.
- Every artifact is written as `<name>.parcial`, `fsync`'d, then renamed —
  the same convention `scjn.cache.SUFIJO_PARCIAL` uses, replicated in
  `_atomic.py` rather than imported (this directory has no other reason to
  depend on `scjn`'s cache layout).
- `padding_side = "left"` on the tokenizer is not optional (last-token
  pooling), and the only transform applied to the model's own output is the
  cast to `float16` — no normalization, no quantization.
- A failing shard writes `shard-XXXX.failed` and does not fail the run;
  `status.py` says what to relaunch.
- Since issue #256, a text over the model's own context window is
  truncated at `max_position_embeddings` (read from the model's config) and
  recorded, never split/windowed/mean-pooled; batches are built under a
  padded-token budget (`--max-batch-tokens`) instead of a fixed row count,
  because `--no-split-articles` corpus texts vary far more in length than
  the pre-#256, cap-2000 shape did.

## The UMAP explorer (issue #241)

The same vectors, looked at. Issue #241 adds four scripts that fit UMAP on
**every** vector of the three corpora and turn the result into one
standalone HTML file — an overview of all ~400,000 unit rows, a detail view
of a clicked instrument (and, with `--neighbors k`, of a clicked text's `k`
nearest neighbours), and a view with one point per instrument. Exactly one
instrument is highlighted at a time: **the last click wins**, whichever view
it landed in.

### Where things are

| What | Path |
|---|---|
| The script that generates the page | `scripts/embeddings/build_umap_html.py` |
| The page | `output/umap-vectors-qwen3-0.6b.html` |
| Its Vega-Lite spec, on its own | `output/umap-vectors-qwen3-0.6b.vl.json` |
| Work directory (vectors, projections, neighbours) | `emb-run-umap/` |
| One directory per configuration | `emb-run-umap/nn016`, `nn032`, `nn064`, `nn128` |
| Earlier sweeps, kept on disk | `emb-run-umap/nn004`, `nn008`, `nn015`, `nn050`, `nn200` |

Neither `emb-run-umap/` nor `output/` is committed, so this table is how a
reader finds the file that produced a page they were handed. The page itself
carries the same answer in a footer — see the provenance footer below.

**This is a different cluster from everything above.** The vector build ran
on `cemieredes` (GPUs, its own `/home/mgraffg/.venvs/cluster` venv, one GPU
per `sbatch` job); this runs on `geoint`: partition `compute`, three nodes
`geoint0`/`geoint1`/`geoint2`, ~60 cores and ~245 GB each, **no GPU at all**
(`GRES=(null)`), time limit `infinite`, and a shared `/home` — so
`submit_umap.sh` calls this repository's own synced `.venv`
(`/home/mgraffg/software/LegalIA/.venv/bin/python`), which is the one
interpreter the login node and every job can agree on. `submit.sh` and
`submit_jobs.py` are untouched: they are the GPU cluster's and would ask for
a GPU that does not exist here.

`geoint0` is also the login node and was saturated by another user's work
outside Slurm, so every job is submitted with `--exclude=geoint0`
(`submit_umap.py --exclude`, that value as the default): two configurations
run on `geoint1`/`geoint2` and the rest queue. They are submitted heaviest
first (`128, 64, 32, 16`), so the long poles start immediately; no job waits on
another, because the shared neighbour table is computed once and kept.
`prepare_umap_input.py` and `build_umap_html.py` do run on the login node —
they are minutes and a couple of GB.

The sweep is `project_umap.DEFAULT_N_NEIGHBORS = (16, 32, 64, 128)`, one
constant the launcher and the HTML builder both import. It has moved twice:
15 / 50 / 200 first, then 4 / 8 / 16 / 32 for a finer look at local
structure, then this window when the small neighbourhoods turned out to
shatter the cloud into filaments. **Nothing is ever deleted**: `nn016` and
`nn032` were reused exactly as they were (only 64 and 128 ran), and
`build_umap_html.py --projections all` brings every earlier directory back
into the radio.

```bash
uv sync --group viz                       # umap-learn, pynndescent, altair, pandas, vl-convert-python
uv run --group viz python scripts/embeddings/prepare_umap_input.py
uv run --group viz python scripts/embeddings/submit_umap.py --wait
uv run --group viz python scripts/embeddings/build_umap_html.py
```

Everything derived lives under `emb-run-umap/` and `output/`, both
gitignored: no vector, no projection and no HTML is ever committed.

### `prepare_umap_input.py` — one matrix, read once

Three jobs each re-reading ~600 parquet files would triple a ten-minute step
and could disagree about row order; one `vectors.npy` cannot. It reads
`legalvec.load_vectors` once per instrument (the published per-instrument
file unioned with the collection's shared file), deduplicates **inside** a
collection and never across it (issue #227's own rule: a text two
collections share got a vector in each release, so it gets two rows here),
and writes `vectors.npy` (`float16`, exactly as published),
`vector_ids.parquet` (`row` -> `coleccion`, `text_sha1`),
`instruments.parquet`, `centroid_input.npy` and `input.json`, then
`prepare.done`. A missing asset surfaces as `legalvec.AssetNotCached`, never
as a silently skipped instrument.

An instrument's centroid is the mean of its **unit rows'** vectors, so a law
that repeats the same transitorio is pulled toward that text as many times
as it repeats it. The rows are copied out of each `VectorSet` rather than
kept as views: a view keeps that instrument's whole matrix (its own file plus
the ~11 MB shared one) alive for as long as any single row of it is
referenced, which measured at ~7 GB after 300 instruments and would have
reached ~25 GB.

### `project_umap.py` — one configuration, one node

`umap.UMAP(n_components=2, metric="cosine", n_neighbors=..., min_dist=0.1,
n_jobs=-1, low_memory=False)` on all N rows, with **no `random_state`**: it
forces single-threaded execution, which on a 60-core node turns an hour into
a day. Reproducibility rests on keeping `coordinates.parquet` and
`projection.json`, not on a seed.

Coordinates are min-max scaled to `[0, 1]` per configuration (the raw range
is recorded in `projection.json`), so the HTML's radio switches projections
without rescaling an axis — UMAP's raw units carry no meaning anyway. The
1,523 instrument centroids are `transform`ed by the fitted model and mapped
through the *same* affine map, so an instrument may land slightly outside
`[0, 1]`, which is information rather than an error.

`--knn 15` writes `neighbors.parquet` at the **work-directory root**, not
inside a configuration: the neighbours live in the 1,024-dimension embedding
space, so they are the same for every projection. They come from a separate
`pynndescent.NNDescent(n_neighbors=16)` index with the self column dropped,
rather than from UMAP's private `_knn_indices`. The launcher passes `--knn`
to the cheapest configuration only, and an existing `neighbors.parquet` is
**kept** rather than rewritten (`--force-knn` overrides): a second sweep over
other `n_neighbors` changes no distance in the embedding space, so k stays 15
across every sweep and the second one paid nothing for it.

### `submit_umap.py` — and why its wait is chunked

(`submit_jobs.py` gained the same chunked wait for the same reason in issue
#256 — see "submit_jobs.py: why its wait is chunked too" above.)

`submit_umap.py --wait` blocks polling `squeue` until every job has left the
queue, up to `--max-wait-hours 8`, then prints the table and `scancel`s
anything still running at the ceiling. That is the human command. An
automated session cannot use it as such: its per-command timeout is minutes,
and ending a turn to wait for a background process kills the jobs'
supervisor with the session. So the wait is resumable —
`--wait --max-wait-minutes 9` returns exit status 75 ("still running",
naming what remains and for how long) and the caller simply calls it again;
the state lives in `emb-run-umap/jobs.json`, not in a process that has to
stay alive. `--report` prints the same table offline.

There is **no retry and no fallback**. A configuration that dies or hits the
8-hour ceiling is reported with the tail of its own Slurm output, and the
HTML is built from the configurations that finished (issue #241's explicit
choice: a fit that does not fit is a finding, not something to hide behind a
sampled rerun). Re-running the launcher skips whatever already has a
`.done`, so relaunching one transient failure is a one-line command.

### `build_umap_html.py` — one file, three linked views

One mark per **unit row** (a shared text is one vector but several unit
rows, drawn once per row that carries it), all six `unit_type`s by default.
The data is inlined with short keys: `v` (vector row), `c` (collection
letter), `i` (instrument index), `u` (unit-type code), `e` (`eId`), one
`x`/`y` pair per projection rounded to 3 decimals, and `n` — the neighbours
as one comma-separated string, ~100 bytes against a JSON array's ~180.
Altair consolidates identical datasets, so the four layers that read the
full point table inline it once.

- **Overview**: `pick` (a point selection on click) and `collections` (a
  point selection bound to the colour legend, which hides a whole corpus).
- **Detail**: the picked instrument's every unit (honouring `pick` *or*
  `pick_instrument`), the picked point itself, and — only with `--neighbors
  k` — that text's neighbours as an outline mark, so a neighbour that is also
  in the same instrument reads as both; all over a faint
  `--background-points` sample. Empty until something is clicked; a text mark
  names the instrument and the `eId`. The view's title says which of the two
  it is showing, so it never promises neighbours that are switched off.
- **Instruments**: a three-layer view — one mark per instrument at its
  centroid (size by unit count, its own `pick_instrument` selection), plus a
  black ring and the instrument's name around whatever was clicked, in
  *either* view. Ring and label are filtered by the same predicate the detail
  view uses (`pick.i` **or** `pick_instrument.i`), so the two cannot
  disagree. A ring rather than a colour change, because colour already means
  collection.

**What each mark means is on the page**, as the views' own subtitles rather
than a hand-drawn legend layer (a subtitle is a few lines of spec, renders
the same on every renderer, and cannot drift from the data):

| Mark | Meaning |
|---|---|
| ◆ black diamond, detail view | the clicked text |
| ○ red rings, detail view | with `--neighbors k` only: its `k` nearest neighbours by cosine, across all three collections (the count is the flag's value, never a literal) |
| filled shapes, detail view | every unit of the same instrument; shape = unit type |
| grey cloud, detail view | the `--background-points` sample, for orientation only |
| black ring + name, instrument view | the instrument of the clicked text, or the clicked centroid |

Knobs: `--unit-types`, `--projections` (a comma-separated list, or `all` for
every finished directory including the earlier sweeps'), `--neighbors N`
(**0 by default** — see below), `--text-chars N` (N characters of the unit's
own text in the tooltip), `--background-points`, `--overview-sample` (thins
only the overview layer), `--inline-js` (embed vega/vega-lite/vega-embed
instead of loading them from jsdelivr) and `--nearest`.

`--nearest` is **off** by default, and that is a bug fix rather than a
preference. Vega-Lite implements `nearest: true` by inserting a hidden
Voronoi mark that captures the pointer; the tooltip is then evaluated on that
mark, whose datum is a wrapper, so every field it names comes out
`undefined`. The first pass shipped `nearest` on and the whole overview
tooltip read `undefined` — that is the finding. With it off the pointer has
to land on the mark itself, so the overview mark went from `size=3`,
`opacity=0.3` to `size=10`, `opacity=0.25`: clickable, still a cloud. The
flag remains, for anyone who wants the old behaviour back.

#### The last click wins

The overview and the instrument view own two independent selections, and
Vega-Lite cannot define one selection over two concatenated views. Before
this was wired, clicking a unit and then a centroid left **both** live: the
detail view showed the union of two instruments and the bottom view ringed
two centroids, with nothing on the page saying which units belonged to
which.

The fix is that each selection is *cleared* by a click in the other's marks.
A selection's `clear` accepts any Vega event stream, `@<markname>:click`
scopes a stream to one view's marks, and a comma merges streams — so
`dblclick` clearing survives alongside it:

| selection | `clear` |
|---|---|
| `pick` (overview) | `dblclick, @centroids_1_marks:click` |
| `pick_instrument` (instrument view) | `dblclick, @overview_marks:click` |

Those mark names are the compiler's, not ours. `build_umap_html.py` names
the two clickable views (`.properties(name="overview")` and
`name="centroids"`), Vega-Lite compiles a view named `v` into a mark named
`v_marks`, and Altair adds one twist on the way: a **layer child** inside a
concat gets that concat row's index appended, so `centroids` reaches
Vega-Lite as `centroids_1` and ends up as `centroids_1_marks`. A unit view
like the overview keeps its name as it is. Both names are module constants
(`OVERVIEW_MARKS`, `CENTROIDS_MARKS`).

Nothing trusts them. `check_last_click_wins` compiles the spec to Vega with
`vl_convert` (on a copy whose inlined datasets are truncated to five rows —
mark names and signals are structural) and **fails the build** unless both
marks exist and each selection's `*_tuple` signal has a clearing `on` entry
(`update: "null"`) carrying the other view's `markname`. It runs before
anything is written, its result goes into the logged `measured` dict as
`last_click_wins`, and `tests/test_umap_scripts.py` asserts the same thing
on a toy spec — so a Vega-Lite or Altair upgrade that renames a mark fails a
13-second test rather than a 70 MB build. **A person still confirms the
clicks in a browser**: the compiled signals are evidence that the streams
are wired, not that the page feels right.

#### Neighbours are off by default

`--neighbors` defaults to **0**: no red rings, no `n` field in the inlined
data, no neighbour line in the detail subtitle, and a detail title that does
not mention them ("click a unit or an instrument: every unit of that
instrument"). The reader asked for the neighbours to go "en este momento",
so the machinery is switched off rather than removed — `--neighbors 15`
restores the full page with no refit, because `neighbors.parquet` stays on
disk and `project_umap.py --knn` is untouched.

Every generated page ends with a small grey **provenance footer** naming this
script, the repository commit (`unknown` where there is no git), the ISO
date, the work directory, the projections and the exact command line; the
`.vl.json` carries the same record in `usermeta.provenance`, so a page and a
spec can be matched. It is inserted by post-processing Altair's HTML —
Altair's saver has no hook for anything outside the chart, and inserting
after the chart's `<div>` leaves both the CDN and the `--inline-js` template
intact. `build_umap_html.py` also prints the absolute path of both files
when it finishes.

**The interactive behaviour is verified by a person opening the file.** What
the scripts themselves verify is the spec: `chart.to_dict()` validates
against the Vega-Lite schema, the spec is compiled to Vega and both
cross-view `clear` streams are asserted (see "the last click wins" above,
which is also the one check that *stops* the build), the written `.vl.json`
is reloaded with Altair, and `tests/test_umap_scripts.py` asserts the `pick`,
`pick_instrument`, `collections` and `proj` params, the neighbour filter, the
legend binding and the canvas renderer are all in it.

**The similarity control (issues #262, #268).** Beside *Neighbourhood size*, the
toolbar has a second `.atlas-segmented` radiogroup, *Similarity* (titled
*Embedding model* until issue #268, when BM25 joined it), with `0.6B` (the
default), `4B` and `BM25`, and a caption saying the method is what measured how
similar two texts are (the 0.6B is the default), so both the relations the panel
lists and the layout change (the neighbourhood size changes the layout only). Both controls are
built by one `radiogroup()` helper in `atlas.js` — same markup, `aria-checked`,
roving `tabindex` and arrow keys — and sit side by side in `.atlas-controls`
at desktop width and stacked at 390 px.

* **The mount names the models.** `data-src`/`data-pairs` stay the default
  model's paths; one more attribute, `data-models`, is a JSON list of
  `{label, src, pairs}` (`0.6B` → `atlas/atlas.json` + `atlas/pairs/`, `4B` →
  `atlas/atlas-qwen3-4b.json` + `atlas/pairs-qwen3-4b/`, `BM25` →
  `atlas/atlas-bm25.json` + `atlas/pairs-bm25/`), relative like the
  rest, whose first entry must agree with `data-src`/`data-pairs` (a test checks
  it). A mount without it — or with a malformed one — builds the page with no
  model control at all, which is how the toy site's older tests run.
* **Load on switch.** The page opens on the 0.6B and fetches nothing else. The
  first switch to the 4B fetches its file once (a failed fetch is not
  remembered: choosing it again retries, and the status line under the control
  says "The 4B map could not be loaded. Choose it again to retry."). The file is
  refused — the status line says "The 4B map cannot be used: it lists different
  instruments." or "…has no such layout.", and the radio stays on the model on
  screen — unless its `instruments` have the same `k` at every position and its
  `projections` has the current neighbourhood size.
* **What a switch does.** `in`/`out`/`inc`/`p` are swapped into the instruments
  the page already holds, every point animates to the new model's layout at the
  *current* neighbourhood size (the same 700 ms transition as a size change,
  one `moveTo()` serves both), the selection, its ring, its numbered lines and
  the panel are redrawn for the new relations, the explanation dialog's pair
  directory becomes the model's `pairs`, and the "Map data generated on" date
  shows the model's `meta.generated`. The explanations cache is keyed
  `<model>:<i>-<j>`, and a dialog request still on its way when the model
  changes is dropped the way a closed dialog's is. No URL parameter and no
  `localStorage`: neither control persists.
* **A score is not a similarity (issue #268).** The explanation dialog's column
  reads **Score** with one decimal when the data set on screen says
  `meta.method` is `"bm25"` — a raw BM25 score is unbounded, three decimals on a
  number in the hundreds is noise — and **Similarity** with three decimals
  otherwise (a file with no `method`, like the two embedding files committed
  before #267, is an embedding). It is the file's `meta.method` that decides,
  never the option's label, and `data-column` follows for the phone layout; the
  rest of a switch (fetch once, the refusal rules, the status messages naming
  the option, the dialog directory) is #262's, unchanged with three entries.
* **Tests.** The harness tests count requests with Playwright's
  `page.on("request")` (only `atlas.json` on load, the 4B file once however many
  switches), read Chapingo's list for each model from the two JSON files, follow
  the layouts by the correlation of circle positions with `projections[k]`,
  route the 4B file through edited copies (swapped `k`, a missing layout, a
  500) for the refusal and retry paths, and open a real pair under each model
  when both `pairs/` and `pairs-qwen3-4b/` are installed. The toy site gets a
  second model — the same instruments, a reversed relation for one, a mirrored
  layout, its own pair directory — for the dialog's per-model directory, the
  dropped stale answer and a differing table; the rendered-page tests check the
  control after Quarto's CSS and the phone-width stacking.

### Tests

```bash
uv run --group viz pytest scripts/embeddings/tests -q
```

Synthetic data, no network, no Slurm, no real UMAP fit: `umap` and
`pynndescent` are stubbed into `sys.modules` with known coordinates and
neighbours, so what is under test is the dedup rule, the centroid mean, the
`[0, 1]` scaling and its affine reuse, the `.done` contract, the launcher's
submission order / wait / report, and the generated spec.

### Measured, on `geoint`, 2026-09-21

`prepare_umap_input.py`, on the login node: **775.5 s** (12.9 min) for
**381,349** vectors at K=1,024 — 107,691 leyes + 264,911 reglamentos +
8,747 lineamientos, exactly the distinct-text counts of the three
`units.parquet` — plus **1,523** instrument centroids (315 + 1,082 + 126).
`vectors.npy` is 781 MB, `vector_ids.parquet` 17 MB, peak RSS ~1.4 GB.

Then three jobs, submitted at once, `--exclude=geoint0`: `nn200` and `nn015`
started immediately on `geoint1`/`geoint2`, `nn050` queued for `Resources`
and started ~5 min later on `geoint2`. Every configuration finished; none
came near the 8-hour limit.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn015` | 15 | `geoint2` | 14.5 s | 203.9 s | 22.3 s | 15.3 s | **284.5 s** | 10.1 GB |
| `nn050` | 50 | `geoint2` | 1.5 s | 211.8 s | 25.0 s | — | **258.5 s** | 10.8 GB |
| `nn200` | 200 | `geoint1` | 14.5 s | 469.8 s | 45.6 s | — | **555.7 s** | 15.8 GB |

So the estimates in the issue (20–60 min, 30–90 min, 1–3 h) were an order of
magnitude pessimistic: 62 numba threads on an idle 60-core node fit 381,349
× 1,024 in 3–8 minutes, and `n_neighbors=200` costs ~2.3× the fit of
`n_neighbors=15` rather than ~13×. The k=15 cosine neighbour table
(`pynndescent`, 16 neighbours with the self column dropped) took **15.3 s**
and 42 MB of parquet — cheap enough that computing it in the cheapest job,
once, is not a real economy so much as a way to keep it out of the
projections. Peak RSS never passed 16 GB of the ~245 GB a node has, so the
"fit on everything" decision was never close to the constraint it was
weighed against.

`build_umap_html.py`, on the login node, ~2 min:

```
408804 points, 1523 instruments
output/umap-vectors-qwen3-0.6b.html: 113.6 MB, 408804 points
```

**113.6 MB, not the 50–60 MB the issue estimated** — three projections'
`x`/`y` pairs, the neighbour strings and JSON's own key overhead over
408,804 rows. The knobs are real: `--neighbors 0` drops it to
67.1 MB, and `--projections nn015` or `--overview-sample` cut it
further. The default is left where issue #241 asked for it, and the size is
printed rather than hidden.

### Measured, the second sweep, `geoint`, 2026-09-21

Four jobs, submitted at once, `--exclude=geoint0`: `nn032` and `nn016`
started immediately on `geoint1`/`geoint2`, `nn008` and `nn004` queued
(`Resources`, then `Priority`) and started ~4 min later on the node each
freed. `prepare_umap_input.py` was **not** rerun — the same 381,349 vectors —
and `neighbors.parquet` was kept, so k stays 15 and the kNN column is empty
for every row.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn004` | 4 | `geoint2` | 1.4 s | 803.7 s | 21.5 s | kept | **836.7 s** | 10.1 GB |
| `nn008` | 8 | `geoint1` | 1.5 s | 346.1 s | 21.5 s | kept | **382.3 s** | 10.3 GB |
| `nn016` | 16 | `geoint2` | 1.5 s | 191.4 s | 21.8 s | kept | **232.8 s** | 10.1 GB |
| `nn032` | 32 | `geoint1` | 1.5 s | 170.1 s | 23.2 s | kept | **212.7 s** | 10.4 GB |

**In this range the cost runs the other way**: `n_neighbors=4` cost 4.7× the
fit of `n_neighbors=32`, where the first sweep had `n_neighbors=200` cost
2.3× `n_neighbors=15`. A small neighbourhood makes a sparser, more
fragmented graph, and UMAP's own heuristic then runs many more epochs over
it. So the launcher's "heaviest first" rule, which sorts by descending
`n_neighbors`, in fact submitted this sweep *cheapest* first. It is kept as
issue #241 specifies it — with two idle nodes and four jobs of 3–14 minutes
the order cost nothing — but the rule is a heuristic about a monotone cost,
and this table is the measurement that says the cost is not monotone. Peak
RSS was ~10 GB for all four, of ~245 GB.

The page itself, rebuilt from the four:

```
408804 points, 1523 instruments
output/umap-vectors-qwen3-0.6b.html: 124.7 MB, 408804 points
```

124.7 MB against the first pass's 113.6, for the one reason that matters
here: four `x`/`y` pairs per point instead of three.

One correction the run itself forced: reloading the written `.vl.json` with
`alt.Chart.from_dict` **validates every inlined data row against the
schema**, which on a 113 MB spec had not finished after ten minutes. The
reload now happens with `validate=False`, and the schema check runs on a
copy whose datasets are truncated to five rows — the spec's structure is
what a schema can say anything about anyway.

### Measured, the third sweep, `geoint`, 2026-09-22

The window moved up to 16 / 32 / 64 / 128, so only **two** jobs ran:
`submit_umap.py` skipped `nn016` and `nn032` (both already `.done`) and
submitted `nn128` then `nn064`, which started at once on `geoint1`/`geoint2`.
`prepare_umap_input.py` was not rerun — the same 381,349 vectors — and
`neighbors.parquet` was kept again, so k stays 15 across all three sweeps.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn016` | 16 | `geoint2` | 1.5 s | 191.4 s | 21.8 s | kept | **232.8 s** | 10.1 GB |
| `nn032` | 32 | `geoint1` | 1.5 s | 170.1 s | 23.2 s | kept | **212.7 s** | 10.4 GB |
| `nn064` | 64 | `geoint2` | 1.5 s | 238.5 s | 27.3 s | kept | **276.9 s** | 11.3 GB |
| `nn128` | 128 | `geoint1` | 1.5 s | 342.2 s | 35.6 s | kept | **389.3 s** | 13.4 GB |

(The first two rows are the second sweep's own measurement, repeated here
because these are the directories this sweep reused rather than refitted.)

**Above 32 the cost is monotone again**, and mildly so: 128 costs 2.0× the
fit of 16, against the 4.7× *penalty* 4 paid over 32 in the second sweep. So
the launcher's "heaviest first" rule submitted this sweep in genuine
heaviest-first order — the first time in three sweeps. Peak RSS grows with
`n_neighbors` as the neighbour graph does (10.1 → 13.4 GB), still an order
of magnitude under the ~245 GB a node has. Centroid `transform` follows the
same curve (21.8 → 35.6 s).

The page, rebuilt from the four with neighbours off:

```
408804 points, 1523 instruments
last click wins: pick_tuple cleared by @centroids_1_marks:click,
                 pick_instrument_tuple cleared by @overview_marks:click
output/umap-vectors-qwen3-0.6b.html: 78.2 MB, 408804 points
```

**78.2 MB**, against the second sweep's 124.7 for the same four projections:
the `n` column — one comma-separated string of 15 vector rows per point,
~100 bytes × 408,804 — was the whole difference, which is the measured
answer to "what do the neighbours cost". The compiled-Vega check ran on the
real spec and logged both clear streams, as above; the footer is present
exactly once.

## The instrument map (issues #242, #259)

The UMAP explorer above is a picture of **texts**. This is a picture of the
1,303 unique **instruments** that own them, built from one question asked of
every unit of every federal law, reglamento and lineamiento:

> which instrument owns the text closest to this one, among all the texts
> that are not exclusively mine?

Weighing the answers gives a square matrix `A` (1,303 × 1,303, rows and
columns in `instruments.parquet`'s `i` order): every *counted* unit row of
instrument `I` hands out a total weight of **1**, `A[I, J] += 1/m` to each of
the `m` instruments owning a winning text. Headings and transitorios
are not compared at all (issue #264), and a word-for-word match shared by
several instruments is searched but not counted (see the rules below). An instrument is then represented by
**where its articles' nearest foreign neighbours live** — a distribution over
the other instruments — rather than by its own text, and two instruments land
together when their articles point at the same places.

The vectors are the whole-article ones (`split_articles: false`, `md2akn`
0.4.0, issue #256) that `legalvec` reads back from the three
`scjn-*-vectors` releases.

### Where things are

| What | Path |
|---|---|
| The input, read once (issues #241, #259) | `scripts/embeddings/prepare_umap_input.py --unique-names` → `emb-run-atlas/` (`vectors.npy`, `vector_ids.parquet`, `instruments.parquet`, `centroid_input.npy`, `input.json`, `unique-instruments.json`, `prepare.done`) |
| Which instruments are unique | `scripts/embeddings/unique_instruments.py` |
| The matrix (a Slurm job) | `scripts/embeddings/instrument_matrix.py` |
| The research page | `scripts/embeddings/build_instrument_umap_html.py` → `output/umap-instruments-qwen3-0.6b.html` (+ `.vl.json`) |
| The website's data (issue #244) | `scripts/embeddings/export_atlas_data.py` → `website/pages/atlas/atlas.json` (committed) |
| The pair explanations (issue #249) | `scripts/embeddings/export_atlas_pairs.py` → `emb-run-atlas/atlas-pairs/` (`pairs/`, `manifest.json`, `atlas-pairs.tar.gz`, `SHA256SUMS.txt`, `PUBLICAR.md`, `.done`), installed into `website/pages/atlas/pairs/` (gitignored); published by hand as the release `atlas-pairs`, body `.github/atlas-pairs.md` |
| The 4B data set (issue #261) | the same chain over `emb-run-atlas-4b/` → `website/pages/atlas/atlas-qwen3-4b.json` (committed), pair files in `website/pages/atlas/pairs-qwen3-4b/` (gitignored), assets `atlas-pairs-qwen3-4b.tar.gz` / `manifest-qwen3-4b.json` / `SHA256SUMS-qwen3-4b.txt` |
| The BM25 data set (issue #267) | `scripts/embeddings/prepare_bm25_input.py` → `emb-run-atlas-bm25/` (no vectors; `bm25-index/`, `tokens.parquet`), then the same chain → `website/pages/atlas/atlas-bm25.json` (committed), pair files in `website/pages/atlas/pairs-bm25/` (gitignored), assets `atlas-pairs-bm25.tar.gz` / `manifest-bm25.json` / `SHA256SUMS-bm25.txt`; the scorer is `scripts/embeddings/scoring.py` |
| The strong-link gold and the three-way evaluation (issue #272) | `scripts/embeddings/gold_links.py` → `emb-run-atlas/gold-links/` (`gold.json`, `gold.md`), then `scripts/embeddings/evaluate_links.py` → `emb-run-atlas/gold-links/` (`evaluation.json`, `evaluation.md`); gitignored, nothing uploaded |
| The row-level evaluation against citations (issue #273) | `scripts/embeddings/gold_links.py` also writes `emb-run-atlas/gold-links/citations.parquet`, then `scripts/embeddings/evaluate_citations.py` → `emb-run-atlas/gold-links/` (`evaluation-rows.json`, `evaluation-rows.md`); gitignored, nothing uploaded |
| Everything derived | `emb-run-atlas/instrument-matrix/` (`matrix.npy`, `nearest.parquet`, `matrix.json`, `umap.parquet`, `umap.json`, `job.json`, `slurm-*.out`, `.done`) |

Nothing here is committed except `atlas.json`. Every command takes
`--work-dir emb-run-atlas` explicitly: the scripts' own default stays
`emb-run-umap/`, the unit-level explorer's work directory, which this chain
never touches.

```bash
uv run --group viz python scripts/embeddings/prepare_umap_input.py \
    --work-dir emb-run-atlas --unique-names
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas --dry-run
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py --work-dir emb-run-atlas
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas \
    --atlas website/pages/atlas/atlas.json --install website/pages/atlas/pairs
```

Before `prepare_umap_input.py`, check that the three cached
`~/.cache/legalvec/scjn-*-vectors/corpus-manifest.json` say `"split_articles":
false`, and refresh with `uv run legalvec download --collection all` if not.
The unique-name selection also reads the SCJN corpus for the two id-keyed
collections' snapshot dates; a missing tarball raises `scjn.AssetNotCached`,
whose message names the `scjn download --coleccion ...` command that fixes it.

### A second model: the 4B (issue #261)

The same chain, over the **Qwen/Qwen3-Embedding-4B** vectors (K = 2560,
`legalvec.model_slug` → `qwen3-4b`) that the three `scjn-*-vectors` releases
carry beside the 0.6B, so the page can offer the comparison between instruments
under either model. A second work directory, never `emb-run-atlas/` with a flag:
the 0.6B outputs stay untouched and reproducible, and `--work-dir` is already
the switch. Both models' vectors must be in the `legalvec` cache
(`uv run legalvec status`).

```bash
uv run --group viz python scripts/embeddings/prepare_umap_input.py \
    --work-dir emb-run-atlas-4b --unique-names --model Qwen/Qwen3-Embedding-4B
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-4b --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-4b \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py \
    --work-dir emb-run-atlas-4b --output output/umap-instruments-qwen3-4b.html
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas-4b \
    --output website/pages/atlas/atlas-qwen3-4b.json \
    --instruments-as website/pages/atlas/atlas.json
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas-4b \
    --atlas website/pages/atlas/atlas-qwen3-4b.json --install website/pages/atlas/pairs-qwen3-4b
```

* **`--instruments-as`** makes `export_atlas_data.py` refuse, with a `SystemExit`
  naming the first differing position, an export whose `c`/`k`/`n`/`p` differ from
  an existing `atlas.json` — or whose instrument count does. The units and the
  unique-name selection do not depend on the model, so the two files must list the
  same instruments in the same positions (the page swaps them keeping its
  selection); `instruments.parquet` and `vector_ids.parquet` of the two work
  directories are byte-comparable (`pyarrow`'s `Table.equals`), and were checked.
  Only `c`/`k`/`n`/`p` must agree: the relations (`in`, `out`, `inc`) and the
  layouts are what a model changes.
* **Asset names carry the model.** `export_atlas_pairs.py` reads the model from
  `input.json`. The default model (the 0.6B) keeps `atlas-pairs.tar.gz`,
  `manifest.json` and `SHA256SUMS.txt`, so nothing already published moves; any
  other model writes `atlas-pairs-<slug>.tar.gz`, `manifest-<slug>.json` and
  `SHA256SUMS-<slug>.txt` (a second `SHA256SUMS.txt` uploaded with `--clobber`
  would overwrite the first), to be **added** to the same `atlas-pairs` release.
  Inside the tarball the layout is `pairs/` + `manifest.json` either way, so the
  publish workflow unpacks every model the same way; the manifest gains `model`,
  `model_slug`, `asset`, `manifest` and `sums`.
* **`meta` has no new key.** `meta.model` already names the model; the page names
  each file on its mount.
* **Never regenerated here:** `atlas.json`, `emb-run-atlas/` and the published
  `atlas-pairs.tar.gz`. `PUBLICAR.md` (in `emb-run-atlas-4b/atlas-pairs/`) is the
  only thing to run, by hand, before the pull request is merged: the workflow
  fetches both asset sets and fails when either is missing.

### A lexical baseline: BM25 (issue #267)

A third data set over **exactly the same comparison**: same unit rows, same
exclusions (headings and transitorios), same masking of a source's exclusively
owned texts, same ties, same `1/m` weighting, same identical-shared rule, same
UMAP of the matrix rows, same exports, same `atlas-pairs` release. Only *how two
texts are scored* changes: a classical bag-of-words ranking whose vocabulary and
inverse document frequencies come from the compared texts themselves, nothing
external. It is a baseline to read the embeddings against, not a claim about
which is better (that evaluation is issue #217's).

```bash
uv run --group viz python scripts/embeddings/prepare_bm25_input.py \
    --work-dir emb-run-atlas-bm25 --from emb-run-atlas
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-bm25 --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-bm25 \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py \
    --work-dir emb-run-atlas-bm25 --output output/umap-instruments-bm25.html
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas-bm25 \
    --output website/pages/atlas/atlas-bm25.json --instruments-as website/pages/atlas/atlas.json
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas-bm25 \
    --atlas website/pages/atlas/atlas-bm25.json --install website/pages/atlas/pairs-bm25
```

`bm25s` and `scipy` are in the root `viz` group (not `dev`), and the Slurm job
runs this repository's own `.venv`, so `uv sync --group viz` must have run
before `--submit`.

* **A pluggable scorer.** `scoring.py` is the one place a text-to-text score is
  computed. `scorer_for(work_dir)` reads `input.json`'s `method` (`dense` when
  the key is absent, so `emb-run-atlas/` and `emb-run-atlas-4b/` keep working
  untouched) and returns a `DenseScorer` (the cosine code that used to be inline,
  moved) or a `Bm25Scorer`; both answer `scores(rows)` (a `float32` block, row
  `r` the score of every text against source `rows[r]`) and
  `scores_against(rows, candidates)` (the pair export's per-pair recomputation).
  `instrument_matrix.py` and `export_atlas_pairs.py` call it and nothing else; the
  dense output is **byte-for-byte** the one it was (checked on the real 0.6B
  directory in a scratch copy: `matrix.npy`, every column of `nearest.parquet`
  and all 6,288 pair files equal; the toy-corpus suite is unchanged).
* **The tokeniser** is `bm25s.tokenize(lower=True, stopwords=None, stemmer=None)`
  with its default Unicode-aware pattern `(?u)\b\w\w+\b`: lowercase words of
  two or more characters and nothing else. Accents are kept, the Markdown `**`
  markers fall out of the pattern, and a one-character word or a single digit is
  not a token. No stopwords (the IDF makes `de`/`la`/`el` nearly weightless) and
  no stemming (`PyStemmer` is deliberately not installed).
* **The index is the candidate set.** One document per vector row carried by at
  least one *searched* unit (`instrument_matrix.searched_mask`), so a heading or a
  transitorio shapes neither the vocabulary nor the IDF: 110,185 documents, of
  145,788 vector rows. `bm25s`' defaults — `method="lucene"`, `k1=1.5`, `b=0.75`
  — are recorded in `input.json` and `matrix.json` so a later sweep changes them
  knowingly. The **query side is binary over distinct terms** (Lucene's and
  `bm25s`' own convention): a term repeated in a text counts once. A test pins
  the sparse product against `bm25s.BM25.get_scores` row by row.
* **`prepare_bm25_input.py`** copies `vector_ids.parquet`, `instruments.parquet`
  and `unique-instruments.json` verbatim from `--from` (so a vector `row` and an
  instrument `i` mean the same thing in all three work directories and
  `--instruments-as` holds by construction), reads the texts through
  `build_umap_html.load_frames`, indexes them, and writes `bm25-index/`
  (`bm25s`' own files), `tokens.parquet` (each document's vector `row` and token
  ids, so the query side is rebuilt without tokenising again) and `input.json`
  with `method: "bm25"`, `model: "bm25"`, `model_slug: "bm25"`, `k: null`. It
  writes **no `vectors.npy` and no `centroid_input.npy`**: nothing BM25 produces
  is a vector per text. It refuses a source without `prepare.done` or without
  `unique_names: true`.
* **Ties are relative, identity is `text_sha1`.** BM25 scores are unbounded, so
  an absolute 1e-6 means nothing on them: a tie is `score >= best * (1 - 1e-6)`
  (`scoring.tie_mask`), and a word-for-word match is a winner carrying the
  source's own `text_sha1` (in any collection) — what a cosine of 1 means for the
  embeddings. Identical texts tie *exactly* under either scorer: the same token
  multiset gives identical columns of the sparse score matrix, and the product
  accumulates a row's terms in one order for every document. The dense path keeps
  its absolute tolerance and its `>= 1 - tolerance` identity.
* **A row can have no foreign match.** A lexical score of 0 against everything
  would otherwise tie every column, so a source whose best foreign score is not
  positive (no term, or every term lies only in texts the source owns alone) has
  no winner: `nearest.parquet` records it with `m` 0, `weight` 0 and
  `drop_reason == "no_match"`, it is not counted, and `matrix.json` reports
  `no_match_rows`. The 112,077 searched rows had none; the dense path still
  raises when a row finds no target.
* **`similarity` keeps its name and holds the raw BM25 score** (one number per
  row, whatever the scale; the pair files round it to four decimals). There is no
  normalisation: a shorter document can out-score the query's own self-score, so
  a self-score ratio does not stay in [0, 1].
* **`meta` and the footer say which method.** `atlas-bm25.json`'s `meta.method`
  is `"bm25"` and `meta.model` `"bm25"`, with the index parameters, tokeniser,
  vocabulary size and documents indexed; `matrix.json` and the pair manifest
  carry `method` and `tolerance_relative`; the research page's footer names the
  method. `export_atlas_data.py` refuses a `matrix.json` computed by another
  method than `input.json` records. The two committed embedding files predate
  `meta.method` and are not regenerated: a reader treats a missing `method` as
  `"dense"`.
* **Asset names** follow the per-model rule with no special case:
  `legalvec.model_slug("bm25")` is `"bm25"`, so the three added assets are
  `atlas-pairs-bm25.tar.gz` / `manifest-bm25.json` / `SHA256SUMS-bm25.txt`
  (`website.yml` fetches and verifies them, and fails when they are missing).
  `emb-run-atlas-bm25/atlas-pairs/PUBLICAR.md` is the only thing to run, by
  hand, before the pull request is merged.

* **The defaults were tuned against, and kept (issue #267's review fix).** The
  index parameters (`lucene`, `k1` 1.5, `b` 0.75, binary queries) were bm25s'
  own and had never been measured. `tune_bm25.py` searches 300 configurations
  for the one whose per-row winners agree most with the 4B's -- the
  "Measured, BM25 tuning" block below has the table and the decision: the best
  configuration lifts the closest-instrument agreement with the 4B by 11 of
  1,303 instruments, short of the 26 (2 percentage points) that would have
  justified regenerating the data set, so nothing was.

```bash
# The grid on a 10 % sample, two Slurm jobs (geoint), then the top 3 + the defaults in full.
uv run --group viz python scripts/embeddings/tune_bm25.py --dry-run          # the grid, nothing computed
uv run --group viz python scripts/embeddings/tune_bm25.py --submit
uv run --group viz python scripts/embeddings/tune_bm25.py --wait --max-wait-minutes 9   # repeat while it exits 75
uv run --group viz python scripts/embeddings/tune_bm25.py --stage confirm --submit
uv run --group viz python scripts/embeddings/tune_bm25.py --stage confirm --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/tune_bm25.py --report            # offline
```

Everything lands under `emb-run-atlas-bm25/tune/` (gitignored): `sample.parquet`,
`grid/<configuration>.json`, `full/<configuration>/` (a normal work directory
each), `results.parquet` and `results.json`. To adopt a winner, pass its values
to `prepare_bm25_input.py` (`--method --k1 --b --weighting`) or change that
script's `METHOD`/`K1`/`B`/`QUERY` constants, re-prepare and rerun the chain;
`Bm25Scorer` reads them back from `input.json`.

### Unique instruments (issue #259)

The SCJN does not reform a reglamento or a lineamiento into a new version: it
reissues it under a new `idOrdenamiento` and keeps the old one. So the
corpora hold several instruments with the same name, and drawing each would
count the same regulation several times. The Atlas compares **unique** laws,
regulations and guidelines, and `unique_instruments.py` is the rule:

- **Only `reglamentos` and `lineamientos` are grouped.** Laws are keyed by
  `abrev`, a curated key, and pass through untouched (`lamn` and `lamni` both
  read "LEY DE AMNISTÍA" and both stay). Names are never compared across
  collections.
- **A name is folded** — NFKD, combining marks dropped, whitespace collapsed,
  casefolded — and nothing looser: no fuzzy matching, and punctuation is not
  folded. That is what pairs `REGLAMENTO  DEL CONCURSO PROGOL…` (a double
  space) with its reissue.
- **The newest original publication date wins**: the date of the instrument's
  own *oldest* snapshot, read from the SCJN corpus and parsed (`fecha_publicacion`
  is `DD-MM-YYYY`, never compared as a string); a tie goes to the larger
  integer `id_ordenamiento`. It agrees with the SCJN's own `vigencia ==
  "VIGENTE"` in all 104 groups that have exactly one VIGENTE member, and has no
  ties today. The newest *latest*-snapshot date is not usable: an abrogated
  instrument's last snapshot can carry the date of the decree that replaced it,
  so it ties often.
- **Only instruments that have vectors compete**, so a text-less instrument
  can never win a group and erase one the Atlas can draw.

| collection | same-name groups | dropped | kept |
|---|---|---|---|
| leyes | (not grouped) | 0 | 315 |
| reglamentos | 138 | 219 | 863 |
| lineamientos | 1 | 1 | 125 |
| **total** | | 220 | **1,303** |

Four kept pairs of regulations still differ only by punctuation (a final
period, or a comma) — `159474`/`178861`, `52327`/`107302`, `69462`/`108392` and
`81471`/`108459` — since the rule folds accents, whitespace and case only.

`prepare_umap_input.py --unique-names` applies it **before the vector matrix is
stacked**, so a dropped instrument has no row in `instruments.parquet`, no unit
row in the join, and no place in `matrix.npy`. The vector rows are restricted
to texts a *kept* instrument carries too: `legalvec.load_vectors` always unions
in the collection's whole shared file, which also holds texts only a dropped
instrument owns, and those never become a row (so they can never be a
candidate). `unique-instruments.json` records, per collection, the
before/kept/dropped counts and every dropped instrument with its name, first
publication date and the `clave` that replaced it; `input.json` records
`"unique_names": true`. `instrument_matrix.py` asserts that the joined unit rows
equal the sum of `instruments.parquet`'s `units`. Without the flag nothing
changes, which keeps the unit-level explorer's inputs reproducible.

`export_atlas_data.py` and `export_atlas_pairs.py` refuse (`SystemExit`) a work
directory whose `input.json` does not record `unique_names: true`, or whose
`instruments.parquet` still lists an instrument `unique-instruments.json` says
was dropped, so the website cannot be fed a map that counts a reissued
regulation several times.

### The rules that define `A`

Each of these was a decision in issue #242, not a default:

- **Headings and transitorios are out of the comparison entirely**
  (`searched_mask`, which `instrument_matrix.py` and `export_atlas_pairs.py`
  both use, so they cannot disagree about which rows were searched). A unit with
  `unit_type == "heading"` (`EXCLUDED_UNIT_TYPES`), or inside a transitorios
  section, is neither a source row nor a candidate and owns no vector row. The
  transitorios section is Akoma Ntoso's own label — any element of the unit's
  `path` equal to `TRANSITORIOS` or starting with `TRANSITORIOS `
  (`is_transitorio_path`; `md2akn.units._container_label` emits that label
  *only* for a section marked `refersTo="#transitorios"`, so `units.parquet`
  needs no `refers_to` column). `md2akn` marks every heading, and the
  reform-date ones (`**D.O.F. 14 DE ENERO DE 1985.**`) match another
  instrument's identical heading; transitorios repeat the standard wording of a
  decree ("El presente Decreto entrará en vigor al día siguiente…") across
  dozens of instruments. Neither says anything about how two instruments
  relate. A text owned by a heading or a transitorio *and* by an article stays a
  candidate, owned only by the article's instrument; a text only headings and
  transitorios own is never a winner. Headings are counted first, so a heading
  inside a transitorios section is one excluded row, and `searched_rows +
  heading_rows_excluded + transitorio_rows_excluded == unit_rows`. Every other
  `unit_type` counts, and there is no `--unit-types` flag. This replaced issue
  #259's narrower rule (a transitorio whose best match was a near-copy was
  searched, but not counted), which is gone — the 2026-09-29/30 measurements
  below were made under it.
- **Ties count, every one of them.** A row's winners are every column within
  `--tolerance` (1e-6) of its best. Identical texts across collections are
  *exact* ties in float32, and breaking them by column index would silently
  prefer `leyes` to everything else.
- **`1/m` to each of the `m` instruments owning a winning text.** A text two
  instruments share is evidence about both, so both are credited — but a unit
  row is one article and weighs one, however many instruments answer for it.
  Without it a single boilerplate winner ("Se deroga.") owned by hundreds of instruments would credit hundreds of cells
  at once, and `A` would count article–instrument incidences rather than
  articles.
- **A word-for-word match shared by several instruments is not counted.** A
  row whose best similarity is `>= 1 - tolerance` (1e-6) *and* whose `m > 1`
  is boilerplate owned by many instruments — it cannot tell one pair apart
  from any other and says nothing distinctive about its source. It adds
  nothing to `A`, and it is **not** re-credited to its next-nearest text.
  `nearest.parquet` keeps the row, with `counted` false. An identical winner
  owned by exactly one other instrument (`m == 1`) still counts a whole 1, as
  does a non-identical winner with `m > 1` (a tie within the tolerance).
- **The identity.** Every row of `A` sums to that instrument's **counted**
  unit rows (searched rows minus the ones the rule above drops), and
  `A.sum()` equals the counted rows; `matrix.json` records this as
  `row_sums_equal_counted_rows`, next to `unit_rows`, `heading_rows_excluded`,
  `transitorio_rows_excluded`, `excluded_transitorios`, `searched_rows`,
  `identical_shared_dropped` and `counted_rows`. An
  instrument's `p` stays its total excerpts and its `pc` (circle size, issue #271) is the row sum, its counted excerpts.
- **Per unit row, not per distinct text.** A boilerplate article repeated
  `m` times inside a code is `m` articles and counts `m` times — the same
  choice #241's centroids made.
- **Only columns owned *exclusively* by the source are masked.** A text `I`
  shares with `J` stays a candidate: the nearest foreign neighbour of such a
  unit is that very text, at similarity 1, which is the strongest relation
  there is and the last thing to hide.
- **Exact cosine, by blocked matrix products.** Not `neighbors.parquet`
  (k=15 neighbours of an article of a 3,600-article code are all inside that
  code, so the foreign one is never among them) and not an approximate
  index. The published vectors are **not** normalised — their norms run 92 to
  121 — so the matrix is normalised once, in place, before any product.
  `--block-rows` (default 1,024) bounds one product at ~1.6 GB whatever the
  instrument. Blocking changes no count, which a test asserts.
- **Directed, never symmetrised.** A reglamento pointing at its law says
  nothing about the law pointing back. The other direction is not thrown
  away: the research page's tooltip carries both the row sum (`units pointing
  out`) and the column sum (`foreign units pointing here`).

The unit → instrument → vector row join is **`build_umap_html.load_frames`
itself**, imported and called with no projection, rather than a second copy:
the two scripts must never disagree about which vector row a unit got.

`nearest.parquet` keeps the evidence, one row per *searched* unit row (every
row but the headings and the transitorios): `similarity`, `n_winners` (how many vector rows tied),
`targets` (the instruments answering), `m` (how many of them), `weight`
(`1/m`) and `counted` (whether the row entered `A`), next to `clave`,
`unit_type` and `eId`, and a `drop_reason` (null or `identical_shared`).
`n_winners` and `m` are different numbers: one winning row can have several
owners, and two tied rows can share one.

### The Slurm plumbing

`--dry-run`/`--submit`/`--wait`/`--report`, in `submit_umap.py`'s own style
and reusing its `queued_jobs`: one job, `--exclude=geoint0`, a two-hour
ceiling, and **no retry** — a job that dies is reported with the tail of its
own Slurm output, never resubmitted automatically. `--wait
--max-wait-minutes N` returns **75** while the job is still there, 0 once
`.done` exists and 1 if the job left the queue without one, so an automated
session can poll in chunks instead of holding a process open. `.done` makes a
plain rerun a no-op; `--force` recomputes — `--submit --force` forwards the
flag into the job (the `.done` check happens there, not at submission time)
and deletes the previous run's `.done` first, so `--wait` cannot mistake the
run being replaced for the one it is waiting on. The job runs this checkout's
own `.venv` (shared `/home`), so it executes whatever branch is checked out.

### The research page

`build_instrument_umap_html.py` runs on the login node — 1,303 × 1,303 is
seconds of work, so there is no job to submit. Rows are L2-normalised (a
zero row raises rather than reaching UMAP as `nan`), then fitted four times
with `n_neighbors` 4 / 8 / 16 / 32, `metric="cosine"`, `min_dist=0.1` and
**`random_state=0`** — the opposite of `project_umap.py`'s decision, for the
opposite reason: a seed costs a single-threaded fit, which at this size is
seconds, and buys a reproducible page. Each projection is min-max scaled into
`[0, 1]` with its raw range recorded, so the radio switches between them
without rescaling an axis.

One layered scatter: colour by collection (legend-bound toggle), size by
`units` on a **log** scale, a radio for `n_neighbors` defaulting to 16, and a
click that puts a black ring and the instrument's name on the picked point
and red rings on the five instruments its units point at hardest (the
comma-separated-string trick #241 validated, read back with Vega's `split`).
`nearest` is **off**, for the reason #241 measured. The tooltip carries
`nombre`, `clave`, `coleccion`, `units`, both directions of the matrix and
the five strongest targets — all weights to **one decimal**, since a weight
is a sum of fractions. `units pointing out` is the instrument's counted unit rows, at most its
`units`, and the column sum (`foreign units pointing here`) is the one that
varies. The same provenance
footer and `usermeta.provenance` as #241's page, through the same helpers.

**The five targets are five tooltip rows, and they are the five red rings.**
Each is its own field (`top1`..`top5`, titled `target 1`..`target 5`, after
`foreign units pointing here`), written **weight first**, two spaces, then
the name — vega-tooltip's default style clips a value cell at **300px × 7em**,
so one row per target is what fits without custom tooltip CSS. An instrument
with fewer than five non-zero targets fills the remaining rows with an em
dash `—`, not a null: vega-tooltip prints a null as the word `null` and skips
only `undefined`. Both the rows and the `t` string the red-ring filter reads
come from one helper, `strongest_targets` (weight > 0 only, heaviest first,
ties by ascending `i`, at most `TOP_TARGETS = 5`), so the names and the rings
cannot disagree and an instrument that receives no weight is never ringed.

**A person still confirms the interaction in a browser.** What the script
verifies is the spec: `chart.to_dict()` against the Vega-Lite schema, the
written `.vl.json` reloaded, and the tests below.

### The atlas export

The website's Atlas cannot read parquet or npy, so `export_atlas_data.py`
(issue #244) writes one compact JSON into the website's own tree,
**`website/pages/atlas/atlas.json`** — the one derived file committed for the
page, like `website/pages/data/scjn-leyes-summary.json`:

```bash
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas
```

It is a pure read of `instruments.parquet`, `matrix.npy`, `matrix.json`,
`umap.parquet` and `umap.json`: `project()` is called only so a missing
matrix fails with a `SystemExit` naming `instrument_matrix.py`, and is a
no-op because `umap.parquet` exists (there is no `--force`). Seconds, no
Slurm, no network. Flags: `--work-dir`, `--output`, `--n-neighbors`
(default `4,8,16,32`, each already fitted), `--top` (5), `--decimals` (4,
coordinates). Run by a human and committed, never by a workflow (issue #115,
Hallazgo C).

One object, keys in this order:

- `meta` — `title`, `generated` (ISO UTC), `commit` (provenance for the file,
  never displayed by the page), `model`, `instruments` (1,303), `provisions`
  (`matrix.json`'s `unit_rows`, 161,989), `heading_rows_excluded` (28,568),
  `transitorio_rows_excluded`, `identical_shared_dropped`, `counted_rows`
  (in that order; the counts of the current files are in the newest
  "Measured" section below),
  `distinct_texts` (`vector_rows`, 145,788), `collections` (`{"leyes": 315, "reglamentos": 863,
  "lineamientos": 125}`, counted from the table), `unique_names` (`true`),
  `duplicates_dropped` (`{"reglamentos": 219, "lineamientos": 1}`, from
  `unique-instruments.json`), `n_neighbors`, `default_n_neighbors` (16), `umap`
  (`min_dist`, `metric`, `random_state`, `umap_version` of the first fit),
  `weighting` (`"1/m"`), `top` (5), `sources` (the three corpus releases, then
  the three `scjn-*-vectors`). `provisions` keeps its old name as a JSON key
  though it counts excerpts (issue #271): keys are code, and the pair files'
  `provisions` lives in a release.
- `instruments` — a list whose index is `i`, each `{"c": coleccion, "k":
  clave, "n": nombre, "p": total excerpts, "pc": counted excerpts, "in":
  incoming weight, "out": [[j, w], …], "inc": [[j, w], …]}`. `out` is `strongest_targets(A, i, top)` and `inc`
  the same helper over `A.T` (who points *here* hardest): weight > 0 only,
  heaviest first, ties by ascending id, weights to one decimal — the ranking
  the research page uses, imported, so the two cannot disagree. `pc`
  (issue #271) is `out`'s total: the matrix row sum, the instrument's counted
  excerpts, an integer `>= 1` (the exporter refuses a row sum more than 1e-3
  from an integer) whose sum over the instruments is `meta.counted_rows`. It
  is a per-model fact — BM25 differs from the two embedding models in 197
  instruments — so, unlike `p`, it is **not** among the keys `--instruments-as`
  compares (`c`, `k`, `n`, `p`). The page shows `pc` only; `p` stays for
  integrity and identity checks.
- `projections` — `{"4": [[x, y], …], "8": …, "16": …, "32": …}`, one pair
  per instrument in `i` order, rounded to `--decimals`, in `[0, 1]`.

**Excerpts, not units, not provisions** (issue #271), in everything the export
carries and the page says: an *excerpt* is a block of text cut from an
instrument whatever its legal role (an article, a transitory article, a
heading, a preamble) — the word of the LegalIA paper, after Colombo et al.
2025 — where *provision* would suggest a normative rule a heading is not, and
*legal provision* already means a DOF note in this project. So: *excerpt* is
the reader's word, *unit* the Python side's (`md2akn.text_units`), *legal
provision* a DOF note. The article of a transitorios block is a *transitory
article* on the page. The Python side keeps `units`; the words `units` and `tooltip` appear nowhere in the
file. The exporter refuses (`SystemExit`) a work directory not prepared with
`--unique-names`, a matrix whose shape does not match the table, a requested
`n_neighbors` missing from `umap.parquet`, an instrument with an empty `out`,
excerpts that do not add up to `matrix.json`'s `unit_rows`, counted excerpts that do not add up to `counted_rows`, and a `matrix.json` that
lacks the counted-row totals or a `matrix.npy` that does not sum to `counted_rows`.

Measured on 2026-09-29: **422.7 kB** (110.1 kB gzipped), under a second; the
Universidad Autónoma Chapingo (`luach`) points at the UAM 10.0, Narro 7.0, Ley
Agraria 1.0, INAH 1.0 and the IPN 1.0 — all five — and `cpeum` has 1,328
excerpts, 837 of them counted. One instrument (the reglamento `124138`, nothing
points at it) has an empty `inc`; every `out` is non-empty.

### The pair explanations (issue #249)

`atlas.json` says the Constitution is closest to the *ESTATUTO de Gobierno del
Distrito Federal* with a weight of 41.0 and nothing more. `export_atlas_pairs.py` writes the evidence
behind every such number, one JSON per pair `(i, j)` the panel lists under
*Closest instruments* — `j` among `strongest_targets(A, i, 5)`, taken from
`export_atlas_data.weighted_targets` itself, so the files are exactly
`atlas.json`'s `out` lists — with the full text of both sides:

```bash
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas \
    --atlas website/pages/atlas/atlas.json --install website/pages/atlas/pairs
```

It needs `emb-run-atlas/instrument-matrix/.done` (plus `matrix.npy`,
`nearest.parquet`, `vectors.npy`, `vector_ids.parquet`,
`instruments.parquet`, each refused by name when missing), a work directory
prepared with `--unique-names`, and the `legalvec` cache, and is a pure read of
all of them. `.done` makes a rerun export nothing unless `--force`; `--install
DIR` (run even then) replaces `DIR` with a copy of `pairs/`, so a pair from an
earlier run cannot survive there. `--atlas` checks an existing `atlas.json`
pair by pair, weight by weight and `clave` by `clave`. Other flags:
`--work-dir`, `--cache-dir`, `--top` (5), `--tolerance` (1e-6), `--out-dir`
(`<work-dir>/atlas-pairs`), `--repo`.

`pairs/<i>-<j>.json`, `i`/`j` being positions in `atlas.json`'s
`instruments`, holds, keys in this order: `source` and `target` (`i`, `k`
clave, `c` collection, `n` name — `k` lets the page notice a tarball built
against another instrument table), `weight` (`round(A[i, j], 1)`, the panel's
number), `provisions` (rows listed, the key kept as published), `rows` and `texts`. A row is one **unit
row** of `nearest.parquet` whose `targets` hold `j` — a text repeated inside
the source is one row per repetition, as in the matrix, so `Σ 1/m` over the
rows is `A[i, j]` (asserted to 1e-3 per pair) — with `label`, `path` (the
breadcrumb, `" › "`-joined, never merged into the label), `unit_type`, `text`
(a vector row, a key into `texts`), `targets` (every unit of `j` carrying a
winning text, each `label`/`path`/`text`), `similarity` (4 decimals) and `m`
(an integer; the page turns it into `1` or `1/m`). Rows go similarity first,
then the heavier weight (smaller `m`), then label. `texts` holds each text
once per file, as `md2akn` emits it.

- **The winners are recomputed, not read.** `nearest.parquet` records which
  instruments won, not which of their vector rows, and recording that would
  mean rerunning #242's Slurm job. So the exporter multiplies each pair's
  source rows by all of `j`'s rows (normalised, from `vectors.npy`), keeps
  every `j` row within the tolerance of the recorded best, and asserts `j`'s
  own best equals `nearest.parquet`'s `similarity` to 1e-6 for every row —
  it must, since `j` owns a global winner and none of its rows is masked.
- **Labels** (`unit_label`) are English for the type word and the corpus'
  spelling for the rest: `Article 27`, `Article 2, part 3`, `Heading V`,
  `Preamble`, `Closing`, `Loose text`, and, for anything inside a
  transitorios block (an `article` whose `path` ends in `TRANSITORIOS …`,
  which is where the corpus puts them — not `loose`), `Transitory provisions,
  29 DE AGOSTO DE 2008 · Único`.
- **Release + per-pair files, not a commit and not one file.** The files are
  far too big for `master`; GitHub release assets send no
  `Access-Control-Allow-Origin` header, so a browser cannot read them from a
  release; GitHub Pages gzips JSON on the fly (`atlas.json`, 427 kB, is served
  as 115 kB), so one plain `.json` per pair costs a median click ~3 kB. The
  tarball is published by a human as the release `atlas-pairs`
  (`PUBLICAR.md`, generated, never run by the script; issue #115, Hallazgo
  C), and the website's publish workflow unpacks it into the site (#250).
  It is reproducible: sorted members, mtime/uid/gid 0, gzip mtime 0.
- **`PUBLICAR.md` replaces the release in place.** The `atlas-pairs` tag
  already exists after its first publication, so the generated file leads with
  `gh release upload ... --clobber` and `gh release edit --notes-file
  .github/atlas-pairs.md` (the create command is kept only for a repository
  that has no such release yet), under the warning to do it **before** the
  pull request that carries the regenerated `atlas.json` is merged to
  `master`: `website.yml` pairs the committed `atlas.json` with whatever the
  release holds when it runs, and the page's `k` check would answer every click
  with the "different version of the map" message.

Measured on 2026-09-29, on the login node:

| | value |
|---|---|
| wall clock | 51 s |
| pairs | 6,372 |
| unit rows the pairs explain | 75,756 of 120,141 counted (weight 73,645.7) |
| unit rows per pair | median 4, p90 27, p99 112, max 705 (`pairs/0-6.json`, *CÓDIGO Civil Federal* → *CÓDIGO Nacional de Procedimientos Civiles y Familiares*) |
| JSON | 165.2 MB raw, 39.8 MB gzipped file by file; per file median 8.8 kB / 2.8 kB, p95 99.3 / 21.7 kB, max 1.19 MB / 214 kB |
| `atlas-pairs.tar.gz` | 35.0 MB |
| largest file | `pairs/1122-1059.json` (1.19 MB, 84 rows): the *REGLAMENTO INTERIOR DE LA SECRETARIA DE MEDIO AMBIENTE Y RECURSOS NATURALES* → the same title with a final period, one of the four punctuation-only pairs above |
| Constitution → *ESTATUTO de Gobierno del Distrito Federal* (`pairs/8-10.json`) | 41 unit rows, 41 distinct texts, weight 41.0, every row a whole 1 (`m == 1`): 38 transitory rows (each below 0.99, the highest 0.906) and three articles (44, 61 and 101) |
| Constitution → *CÓDIGO Penal Federal* (`pairs/8-9.json`) | 55 unit rows, weight 34.4, third of the Constitution's five; 53 of them transitory |

No pair file holds a heading row, a row with similarity 1 and `m > 1`, or a
transitory row at 0.99 or more.
Every file's `weight` equals its `atlas.json` `out` weight, every text key
resolves, every file is sorted, and `Σ 1/m` is within 0.05 of `weight` except
for float noise on exact halves.

### The Atlas page

The file above is what the website's **Atlas** reads
(`website/pages/atlas.qmd`, navbar *Atlas*, issue #245): a hand-written D3 v7
application in `website/pages/atlas/atlas.js` and `atlas.css`, the public
successor of the research page. It draws circle **area**
proportional to counted excerpts, `pc` (a square-root radius, 2.5 px floor) in the site's
palette (laws `#2a78d6`, regulations `#008300`, guidelines `#e87ba4`), lists
all five closest instruments and the five that point here in a detail panel
instead of a tooltip, joins the selection to its five targets with numbered
lines, and has a search box (accents and case folded), a labelled
*Neighbourhood size* control over the four layouts, and a collection legend.
Its browser tests run the qmd's own markup in headless Chromium:

```bash
uv run --group viz playwright install chromium   # once
uv run --group viz pytest scripts/embeddings/tests/test_atlas_page.py -q
```

They serve `website/pages` over `http.server` (Chromium will not `fetch` from
`file://`) with a harness page whose body is the qmd's `<!-- atlas:app -->`
block verbatim, and leave a screenshot with Chapingo selected at
`output/atlas-chapingo.png` (gitignored). Without Playwright or its Chromium
they skip with the reason; the static checks on the qmd, `_quarto.yml` and the
two files always run. The `test_rendered_*` tests (issue #247) render
`pages/atlas.qmd` with Quarto into `website/_site/` (gitignored) and drive the
page Quarto actually wrote, site CSS included — the harness alone cannot see
Quarto's `aside` rule collapse the map to 0 px — leaving
`output/atlas-rendered.png` and `output/atlas-rendered-chapingo.png`; they
skip only when no Quarto is found on `PATH` or under
`~/.local/opt/quarto-*/bin/quarto`. Every number the tests compare with the
page (the subtitle's counts, Chapingo's five targets, the Constitution's
counted excerpts, the pair dialog's rows) is read off `atlas.json` and the installed
pair file, so the prose and the data cannot drift.

**The explanation dialog (issue #250).** Each weight under *Closest
instruments* is a `button.atlas-why` that opens a native `<dialog>` over the
pair file #249 exports: a heading naming both instruments, one sentence
("15 excerpts of … have their closest text outside it in …; they add up to
15.0 of its 137 counted excerpts."), and a table —
number, the source excerpt
(label, breadcrumb, full text), the closest text in the target (the same, plus
"also N other excerpts … carry this text" when several do), similarity to
three decimals, and the weight as `1` or `1/m` with "this text is shared by
*m* instruments". Rows keep the file's order and arrive 50 at a time ("Show 14
more (14 left)" for the last batch of the pair above); the mount's `data-page-size` changes that number, and
exists for the tests. Text is Markdown rendered bold-only, paragraphs on blank
lines, never shortened, never parsed as HTML. The file is fetched on the first
click only (a `Map` per page load), from the mount's `data-pairs`
(`atlas/pairs/`), and checked by both instruments' `k`: a 404 or network error
says "The explanation for this pair is not available.", a mismatch "The
explanation was built for a different version of the map." Escape, the close
button and a backdrop click close it; the selection, lines and zoom are
untouched and focus returns to the weight. *Points here* has no file behind
it (only the five closest are exported) and stays plain numbers.

To see it locally the pairs have to be under `website/pages/atlas/pairs/`
(gitignored): either `uv run --group viz python
scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas --install
website/pages/atlas/pairs`, or the published release by hand:

```bash
gh release download atlas-pairs --repo INGEOTEC/LegalIA \
    --pattern atlas-pairs.tar.gz --pattern SHA256SUMS.txt --dir /tmp/atlas-pairs
(cd /tmp/atlas-pairs && sha256sum -c --ignore-missing SHA256SUMS.txt)
mkdir -p /tmp/atlas-pairs/unpacked && tar -xzf /tmp/atlas-pairs/atlas-pairs.tar.gz -C /tmp/atlas-pairs/unpacked
rm -rf website/pages/atlas/pairs && mv /tmp/atlas-pairs/unpacked/pairs website/pages/atlas/pairs
```

The site does the same: `.github/workflows/website.yml`'s *Fetch the Atlas
pair explanations* step runs those lines (plus `manifest.json` into the same
directory) before `quarto publish`, and fails when the release or the asset
is missing, because a site without the files would answer every click with
"not available". It only copies what a human published (issue #115, Hallazgo
C) — so **replace `atlas-pairs` before this reaches `master`**, or the next
website run pairs the new `atlas.json` with the old pair files.
`_quarto.yml`'s `pages/atlas/**` resource glob already ships the directory.

The dialog's tests run over a toy site — the six-instrument corpus exported
by both scripts into a temporary `website/pages/` look-alike with the real
`atlas.js`/`atlas.css` — and check the rows against the pair file, the weights
against the panel, `1/m`, paging, the three ways to close, focus, no request
before the click and one per pair, both messages, bold-only rendering, and a
390 px phone. With the real pairs installed, one more test opens the
Constitution's heaviest pair (the ESTATUTO, 41 rows, 41.0, every row a whole 1)
and saves `output/atlas-explain-cpeum.png`; without them it skips. A
rendered-site test checks Quarto's `h2` rule does not reach the dialog.

### Tests

```bash
uv run --group viz pytest scripts/embeddings/tests -q
```

`tests/test_instrument_matrix.py`: a six-instrument toy corpus over two
collections whose vectors are written by hand, so every weight in the
expected matrix is derivable with a pen: a text shared inside a collection
wins at cosine 1, a text identical across collections wins at cosine 1 from
the other side, one row ties across two foreign instruments and gives ½ to
each, an instrument's own exclusive texts are masked, and a boilerplate line
carried by three leyes at once ("Se deroga.") wins at cosine 1 with `m = 3`
and is **dropped** rather than credited. Three headings stand in the corpus:
one carries a text nobody else has and is nearly another instrument's
article (it must never win), one carries an article's text (that text is
owned by the article's instrument alone), one repeats a transitorio (it is
not a source row). An identical text owned by exactly one other instrument
still adds 1, and a non-identical tie over two instruments still gives ½ each.
A second toy corpus (with a `path` column) pins the transitorio rule (issue
#264): a transitorio has no row in `nearest.parquet` whatever its similarity
(0.995, 0.98, cosine 1 with `m == 2`), an article at 0.995 still counts, a
transitorio text is not a candidate (a row that used to point at one goes to its
next-nearest text), a text a transitorio shares with an article is owned by the
article's instrument alone, a text only transitorios own never wins, a heading
inside a transitorios section is counted once as a heading, the near-copy
rule's flag, parameter and keys are gone, `export_atlas_pairs.load` accepts the
directory, and the path predicate is read on `TRANSITORIOS`, `TRANSITORIOS 18 DE MARZO DE 1980`, a nested path and a
label that only contains the word.
The identity (`A.sum(axis=1)` = each instrument's counted rows, `A.sum()` =
`counted_rows`) is asserted on the toy matrix, as are `float32` and `weight ==
1/m`. Blocking is asserted invisible (`--block-rows 1` against the default), the
Slurm plumbing is driven through a fake `squeue` in its three states (plus
`--force` forwarded into the job and the previous `.done` dropped), and the
page is built with a stub reducer.
`tests/test_unique_instruments.py` covers the selection on toy records — the
newest original date, a three-member group, accent/double-space folding, a
date tie resolved by the larger id, `DD-MM-YYYY` parsing, leyes and cross-
collection names never merged, and the snapshot-date lookup against a stub
reader. `tests/test_umap_scripts.py` runs `prepare_umap_input.py
--unique-names` over a toy corpus with a duplicated lineamiento: the dropped
instrument's exclusive and shared-file-only vectors get no row, and
`unique-instruments.json`/`input.json` say what happened (the default keeps
everything and writes no report). `tests/test_export_atlas_data.py` imports
the same toy corpus and checks the atlas export against it: the schema and
key order, `p`/`in`/`out`/`inc` against the matrix and `strongest_targets`,
rounded unit-square projections, no refit, and each refusal above (including
a work directory prepared without `--unique-names`).
`tests/test_export_atlas_pairs.py` does the same for the pair explanations:
one file per `strongest_targets` pair, each file's rows exactly
`nearest.parquet`'s and adding up to its cell, every winning target text
re-checked with numpy, the tie, the three-owner boilerplate and the repeated
text of the toy corpus, `unit_label` on every type, the manifest, the sums,
the reproducible tarball, a `PUBLICAR.md` that replaces the existing release
first, the rerun/`--force`/`--install` rules and the refusals.
The lexical baseline (issue #267) adds `tests/test_scoring.py` (the dense
scorer against the normalised product by hand; the BM25 scorer against a
three-document corpus whose Lucene scores are worked out in the file's comments,
against `bm25s.BM25.get_scores` row by row, the exact tie of equal token
multisets, a document with no term, `tie_mask` under both rules, `scorer_for`
choosing dense when `input.json` has no `method`) and
`tests/test_tune_bm25.py` (the grid, the metrics over hand-built tables, weights recomputed
from token counts against every bm25s method's own index, a sample that still masks a
source's unsampled texts, `--dry-run` computing nothing) and
`tests/test_prepare_bm25_input.py` (the copied tables are byte-equal, no vectors,
headings and transitorios are not documents, `input.json`, the refusals,
`.done`/`--force`). `test_instrument_matrix.py` keeps its dense suite unchanged —
the byte-for-byte guard of the refactor — and adds a BM25 run over the same toy
corpus whose matrix is derived on paper (a six-way relative tie giving ⅕ to each
of five instruments, an identical-shared drop decided by `text_sha1`, a row with
no foreign match), `test_export_atlas_pairs.py` a BM25 export (`-bm25` assets
only, `asset_names("bm25")`, the per-pair recomputation) and
`test_export_atlas_data.py` the file's `meta.method` and `--instruments-as`.
`test_atlas_page.py` pins the workflow step and the gitignore line for the BM25
set and that `atlas-bm25.json` lists the same instruments as the default.

### Measured, 2026-09-29

Prepared on the login node in 156.6 s (315 + 863 + 125 instruments, 145,788
distinct texts). The matrix, one job (42240) on `geoint1` (62 threads,
`--exclude=geoint0`):

| phase | seconds |
|---|---|
| load (the join + `vectors.npy`) | 1.5 |
| normalise (145,788 rows, in place) | 0.3 |
| sweep (133,421 searched rows against 145,788 texts, blocked) | 117.3 |
| write | 0.2 |
| **total** | **119.7 s** |

Peak RSS **2.02 GB** of the ~245 GB a node has — a 1,024-row block and one
normalised copy of the matrix is all that is ever live.

| what | value |
|---|---|
| unit rows | 161,989 |
| heading rows excluded | 28,568 |
| searched rows | 133,421 (21,344 of them transitorios) |
| identical winners shared by several instruments, not counted | 9,783 |
| transitorios at similarity 0.99 or more, not counted | 3,497 (3,357 `article`, 140 `loose`; 3,217 with `m == 1`) |
| **counted rows** | **120,141** (9,786 of them transitorios) |
| vector rows | 145,788 (3,370 owned by more than one instrument once headings are set aside) |
| instruments | 1,303 |
| `A.sum()` | **120,141.0** — one counted unit row, one unit of weight |
| every row sums to that instrument's counted rows | yes (`row_sums_equal_counted_rows`) |
| non-zero cells | 38,073 of 1,697,809 (2.2 %) |
| searched rows with a tie | 1,947 (all two-way) |
| largest `m` | 157 |
| mean similarity of the winner | 0.806 (0.785 over the counted rows) |
| searched rows whose winner is an identical text (cosine 1) | 14,455 (10.8 %): 4,672 with `m == 1`, which count unless they are transitorios, and 9,783 shared, which do not |

The `m` histogram over the searched rows — how many instruments a row's
answer is split over:

| `m` | 1 | 2 | 3–5 | 6–20 | 21–100 | 101+ |
|---|---|---|---|---|---|---|
| unit rows | 119,840 | 3,848 | 4,095 | 2,472 | 2,510 | 656 |

90 % of searched rows have a single answer and are credited a whole 1; a
row answered by many instruments adds one between them unless its winner is a
word-for-word text or a near-copy transitorio, in which case it adds nothing.

The four fits, on the login node, `random_state=0`:

| `n_neighbors` | 4 | 8 | 16 | 32 | total |
|---|---|---|---|---|---|
| seconds | 14.3 | 5.7 | 6.5 | 7.4 | **33.9** |

The research page: **1.2 MB** for 1,303 instruments, with the footer present
exactly once.

### Measured, the 4B, 2026-09-30 (issue #261)

`emb-run-atlas-4b/`, prepared on the login node in **369.8 s** (K = 2560; 315 +
863 + 125 instruments, 145,788 distinct texts, `instruments.parquet` and
`vector_ids.parquet` equal to `emb-run-atlas/`'s). The matrix, one job (42303)
on `geoint1` (62 threads, `--exclude=geoint0`):

| phase | 0.6B (seconds) | 4B (seconds) |
|---|---|---|
| load (the join + `vectors.npy`) | 1.5 | 8.9 |
| normalise | 0.3 | 0.8 |
| sweep | 117.3 | 183.3 |
| write | 0.2 | 0.2 |
| **total** | **119.7** | **193.5** |
| peak RSS | 2.02 GB | **3.17 GB** |

| what | 0.6B | 4B |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| identical winners shared by several instruments, not counted | 9,783 | 9,783 |
| transitorios at similarity 0.99 or more, not counted | 3,497 | 3,611 |
| **counted rows** | **120,141** | **120,027** |
| non-zero cells | 38,073 | 35,316 |
| searched rows with a tie | 1,947 | 1,923 |
| largest `m` | 157 | 157 |
| mean similarity of the winner (searched rows) | 0.806 | 0.795 |
| searched rows whose winner is an identical text | 14,455 | 14,455 |
| `row_sums_equal_counted_rows` | yes | yes |

The four fits took 13.9, 5.2, 6.3 and 6.9 s (`n_neighbors` 4/8/16/32, 32.3 s).
The research page, `output/umap-instruments-qwen3-4b.html`, is 1.2 MB;
`website/pages/atlas/atlas-qwen3-4b.json` is **422,998 bytes** (`atlas.json`:
422,737).

Pair explanations: **6,352 pairs** (the 0.6B has 6,372: fewer instruments have
five distinct targets), 78,384 excerpts, 176.4 MB of JSON, largest file
`pairs/1122-1059.json` (86 rows, 1.19 MB), the tarball **37.5 MB**, 73 s.

How much the model changes the picture: over the 1,303 instruments, the five
closest instruments (`out`) of the two models share 3.4 of 5 on average (4
instruments share none), and the closest one is the same for 1,069.

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
model — weights first, then the instrument:

| | 0.6B | 4B |
|---|---|---|
| `cpeum` | 41.0 ESTATUTO de Gobierno del Distrito Federal · 39.9 LEY General de Instituciones y Procedimientos Electorales · 34.4 CÓDIGO Penal Federal · 27.4 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 24.7 LEY Orgánica del Poder Judicial de la Federación | 42.0 ESTATUTO de Gobierno del Distrito Federal · 42.0 LEY General de Instituciones y Procedimientos Electorales · 36.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 26.2 CÓDIGO Penal Federal · 24.7 LEY Orgánica del Poder Judicial de la Federación |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Agraria · 1.0 LEY Orgánica del Instituto Nacional de Antropología e Historia · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 9.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 CONSTITUCIÓN Política de los Estados Unidos Mexicanos · 1.0 LEY General de Educación · 1.0 LEY de Organizaciones Ganaderas |

(The names are the corpus' own spelling; the next issue's tests read the lists
from the JSON, not from this table. The
Constitution's total `in` weight — how hard everything else points at it — rises
from 680.9 to 799.6 under the 4B.)

The `PUBLICAR.md` this run wrote is `emb-run-atlas-4b/atlas-pairs/PUBLICAR.md`;
nothing was uploaded.

### Measured, both models, 2026-09-30 (issue #265)

The rebuild under issue #264's rule — headings *and* transitorios are neither
source rows nor candidates — over the two work directories issue #261 left
(`emb-run-atlas/`, `emb-run-atlas-4b/`), **not** re-prepared: the units, the
unique-name selection and the vectors did not change, only the matrix rule.
The matrices are two jobs (42438 on `geoint1`, 42439 on `geoint2`, 62 threads,
`--exclude=geoint0`), submitted together with `--force`:

| phase | 0.6B (seconds) | 4B (seconds) |
|---|---|---|
| load (the join + `vectors.npy`) | 6.4 | 11.2 |
| normalise | 0.3 | 0.8 |
| sweep (112,077 searched rows against 145,788 texts, blocked) | 102.8 | 162.6 |
| write | 0.2 | 0.2 |
| **total** | **110.0** | **175.2** |
| peak RSS | 1.98 GB | **3.19 GB** |

| what | 0.6B | 4B |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| **transitorio rows excluded** | **21,344** | **21,344** |
| searched rows | 112,077 | 112,077 |
| identical winners shared by several instruments, not counted | 1,715 | 1,715 |
| **counted rows** | **110,362** | **110,362** |
| vector rows | 145,788 (1,158 owned by more than one instrument, once headings and transitorios are set aside) | same |
| instruments | 1,303 | 1,303 |
| `A.sum()` | **110,362.0** | **110,362.0** |
| every row sums to that instrument's counted rows | yes | yes |
| non-zero cells | 32,539 (was 38,073) | 30,213 (was 35,316) |
| searched rows with a tie | 864 (all two-way) | 842 (all two-way) |
| largest `m` | 14 (was 157) | 14 |
| mean similarity of the winner | 0.787 (0.783 over the counted rows) | 0.775 (0.771) |
| searched rows whose winner is an identical text (cosine 1) | 3,802: 2,087 with `m == 1`, which count, and 1,715 shared, which do not | the same |

The exclusion is independent of the model (the same 28,568 + 21,344 rows), and
so are the 1,715 shared identical winners: an identical text is an exact tie
under either. Counted rows fell by 9,779 for the 0.6B (120,141) and 9,665 for
the 4B (120,027), and the two models now count the same 110,362. The `m`
histogram over the searched rows:

| `m` | 1 | 2 | 3–5 | 6–20 | 21–100 | 101+ |
|---|---|---|---|---|---|---|
| 0.6B unit rows | 108,533 | 1,692 | 1,611 | 241 | 0 | 0 |
| 4B unit rows | 108,626 | 1,654 | 1,542 | 255 | 0 | 0 |

97 % of searched rows have a single answer. The boilerplate that used to fill
the last buckets (up to 157 owners) was the transitorios.

The four fits took 17.6, 7.1, 7.3 and 8.3 s (0.6B, 40.3 s with the write) and
16.3, 6.4, 7.4 and 8.5 s (4B, 38.6 s); the research pages are 1.2 MB each.
`website/pages/atlas/atlas.json` is **421,007 bytes** and `atlas-qwen3-4b.json`
**421,192**, both with `meta.transitorio_rows_excluded` 21,344 and no
`transitorio_near_identical_dropped`; `--instruments-as` accepted the 4B export
(the same 1,303 instruments in the same positions).

Pair explanations, 0.6B / 4B:

| | 0.6B | 4B |
|---|---|---|
| pairs | 6,288 | 6,265 |
| excerpts the pairs explain | 71,348 (weight 70,006.9) | 73,822 (weight 72,531.1) |
| excerpts per pair | median 4, p90 25, max 702 | median 4, p90 27, max 747 |
| JSON | 159.0 MB | 169.0 MB |
| tarball | 33.6 MB | 35.9 MB |
| largest file by size | `pairs/1122-1059.json` (1.19 MB, 84 rows) | `pairs/1122-1059.json` (1.19 MB, 86 rows) |
| export + install | 31 s + tarball 48 s | 49 s + tarball 65 s |

How much the rule moved the picture, against the committed files of the
previous build (`git show master:website/pages/atlas/atlas.json` and
`atlas-qwen3-4b.json`; same instruments, same positions): the five closest
instruments (`out`) share 4.37 of 5 on average for the 0.6B (4.41 for the 4B),
one instrument shares none in either model, and the closest one is unchanged
for **1,239** of the 1,303 (0.6B) and **1,240** (4B). The two models now agree
on the closest instrument for 1,077 (they did on 1,069) and share 3.30 of 5 on
average (3.4). The instruments' total incoming weight fell most where the
transitorios had pointed: the Constitution's `in` went from 680.9 to 246.0
(0.6B) and from 799.6 to 384.0 (4B).

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
model — weights first, then the instrument:

| | 0.6B | 4B |
|---|---|---|
| `cpeum` | 15.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Poder Judicial de la Federación · 8.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.5 REGLAMENTO DEL SENADO DE LA REPUBLICA · 4.0 ESTATUTO de Gobierno del Distrito Federal | 16.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 9.0 LEY Orgánica del Poder Judicial de la Federación · 6.0 ESTATUTO de Gobierno del Distrito Federal · 3.0 CÓDIGO Civil Federal |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 9.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 6.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY General de Educación · 1.0 LEY Monetaria de los Estados Unidos Mexicanos · 1.0 LEY Orgánica de la Universidad Nacional Autónoma de México |

The Constitution has 1,328 excerpts and 137 count (136 articles and the
preamble); the pair behind its heaviest weight, `cpeum` → `lgipe`, lists 15
excerpts, each a whole 1, from article 96 at similarity 0.861 (to article
494) down to article 125 at 0.606 (to article 11). The Chapingo law has 28
excerpts and 18 count, all answered by a single instrument each (`m == 1`):
10 to the UAM, 7 to Antonio Narro, 1 to the IPN. The page's worked examples
(`atlas.qmd`) were rewritten from these two files and the installed pair
files; the Constitution's old example, 38 transitory articles matched to the
statute's transitory articles, no longer exists under the rule.

#### Counted excerpts on the page (issue #271, measured 2026-10-05)

The three committed files were regenerated from the three local work
directories with the commands above (a pure read: no refit, pair files and
`atlas-pairs` release untouched, so no publish step) and gained `pc`:
`sum(pc)` is 110,362 (0.6B), 110,362 (4B) and 110,000 (BM25), `cpeum` is 137 in
all three, the smallest `pc` is 1, the median 48 and the largest 3,063 against
a largest `p` of 3,589. `c`/`k`/`n`/`p`, `in`/`out`/`inc` and the projections
are byte-identical to before; only `pc`, `meta.generated`, `meta.commit` and —
for the two embedding files, committed before #267 — `meta.method` changed. A
circle's area is now proportional to `pc`, so the Constitution (137 of 1,328)
is a small circle and more than half of the instruments sit on the 2.5 px
floor; switching to BM25 resizes the 197 circles whose `pc` differs.

The hand-offs this run wrote are `emb-run-atlas/atlas-pairs/PUBLICAR.md` and
`emb-run-atlas-4b/atlas-pairs/PUBLICAR.md`; nothing was uploaded.

### Measured, BM25, 2026-10-01 (issue #267)

`emb-run-atlas-bm25/`, prepared from `emb-run-atlas/` on the login node in
**16.3 s** (tokenising 110,185 texts 7.2 s, building the index 5.4 s; 1.6 GB
peak; `bm25-index/` 59 MB, `tokens.parquet` 21 MB): 63,694 terms, **0 documents
without a term**. The matrix, one job (42489) on `geoint1` (62 threads,
`--exclude=geoint0`), beside the 0.6B's (issue #265):

| phase | 0.6B (seconds) | BM25 (seconds) |
|---|---|---|
| load (the join + the scorer) | 6.4 | 5.3 |
| normalise | 0.3 | — |
| sweep (112,077 searched rows against 145,788 texts, blocked) | 102.8 | 419.9 |
| write | 0.2 | 0.2 |
| **total** | **110.0** | **425.7** |
| peak RSS | 1.98 GB | 2.4 GB |

| what | 0.6B | BM25 |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| transitorio rows excluded | 21,344 | 21,344 |
| searched rows | 112,077 | 112,077 |
| identical winners shared by several instruments, not counted | 1,715 | 2,077 |
| rows with no foreign match (`no_match`) | — | 0 |
| **counted rows** | **110,362** | **110,000** |
| non-zero cells | 32,539 | 33,199 |
| searched rows with a tie | 864 | 4,487 (2,790 two-way, 1,241 three to five, 456 six or more) |
| largest `m` | 14 | 16 |
| `m == 1` | 108,533 | 106,933 |
| `row_sums_equal_counted_rows` | yes | yes |

BM25 ties far more often than the embeddings (4,487 rows against 864): a short
text made of a few common words has many equally scoring neighbours, where a
dense vector almost never does. The four UMAP fits took 14.2, 5.4, 6.2 and 7.0 s
(32.8 s in all); `output/umap-instruments-bm25.html` is 1.2 MB and its footer
says `method bm25`. `website/pages/atlas/atlas-bm25.json` is **421,725 bytes**
(`atlas.json`: 421,007), accepted by `--instruments-as` (the same 1,303
instruments in the same positions).

Pair explanations: **6,289 pairs** (the 0.6B has 6,288), 72,931 excerpts,
173.5 MB of JSON, largest file `pairs/1122-1059.json` (92 rows, 1.27 MB), the
tarball `atlas-pairs-bm25.tar.gz` **36.3 MB**, 29 s; every file's `sum(1/m)`
equals `matrix[i, j]` to 1e-3 (the export asserts it).

How much the score changes the picture, over the 1,303 instruments: the closest
instrument is the same as the 0.6B's for **906** and as the 4B's for 933; the
five closest share 2.64 of 5 with the 0.6B on average, and 32 instruments share
none. The Constitution's total incoming weight (`in`) is 346.0 (0.6B: 246.0).

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
score — weights first, then the instrument:

| | 0.6B | BM25 |
|---|---|---|
| `cpeum` | 15.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Poder Judicial de la Federación · 8.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.5 REGLAMENTO DEL SENADO DE LA REPUBLICA · 4.0 ESTATUTO de Gobierno del Distrito Federal | 17.0 ESTATUTO de Gobierno del Distrito Federal · 12.0 LEY General de Instituciones y Procedimientos Electorales · 8.0 LEY Orgánica del Poder Judicial de la Federación · 6.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.0 LEY Federal de Responsabilidades de los Servidores Públicos |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 12.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 2.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 2.0 REGLAMENTO INTERIOR DEL COLEGIO DE POSTGRADUADOS · 1.0 LEY Orgánica del Instituto Politécnico Nacional · 1.0 REGLAMENTO DE FUNCIONAMIENTO DE LOS CONCURSOS PROTOUCH INICIAL DE PRONOSTICOS PARA LA ASISTENCIA PUBLICA |

The hand-off this run wrote is `emb-run-atlas-bm25/atlas-pairs/PUBLICAR.md`;
nothing was uploaded.

### Measured, BM25 tuning, 2026-10-01 (issue #267, review fix)

Question: does a better BM25 configuration move the lexical baseline toward the
Qwen3-Embedding-4B's result? **Objective** (decided before running, and the only
thing optimised): *row-level winner agreement* with the 4B -- a searched row is
a **hit** when the set of instruments owning BM25's tied winners intersects the
set the 4B's `nearest.parquet` records for that row; `hit_rate` is hits over the
rows the 4B counted (a row with no BM25 match is a miss). Reported beside it:
`jaccard` (mean Jaccard of the two owner sets), `closest` (instruments, of 1,303,
whose closest instrument is the 4B's), `top5` (mean size of the intersection of
the two top-five lists), `tie_rows`, `id_shared` (`identical_shared_dropped`),
`no_match`, `mean_m`. **Space**: `k1` {0.5, 0.9, 1.2, 1.5, 2.0, 3.0} x `b` {0,
0.25, 0.5, 0.75, 1.0} x method {lucene, robertson, atire, bm25l, bm25+} x query
{binary, tf-saturated} = 300; not the tokeniser, tie tolerance, identity rule,
mask or `1/m`. **Cost**: the grid on a stratified (by collection) 10 % sample --
11,208 of the 112,077 searched unit rows, seed 0 -- in two `geoint` jobs
(42494, 42495; 28 processes each, ~6 minutes in all, median 61 s per
configuration, weights recomputed from `tokens.parquet` with no re-tokenising and
no index rebuild); then the top 3 by sample `hit_rate` plus the defaults as the
control, **full sweeps**, one job each (42496-42499, ~7 minutes each).

Checks the numbers rest on: recomputed weights equal bm25s' saved index for all
five methods (a test); a sweep over a sample gives the rows the full run gives
(identical targets for all 11,208 sampled rows of the defaults); the control's
full `matrix.npy` is byte-equal to the committed data set's (`instrument-matrix/`
of 2026-10-01), so the 933 below is the same 933 as before. A first draft of the
sample sweep masked only the *sampled* rows of a source's own texts, so its own
unsampled texts won against it (40 % `no_match`); the mask now covers all of the
source's searched rows (`own_rows`), and a test pins it.

Sample grid, best ten of 300 (the defaults are 86th, 0.4635):

| method | k1 | b | query | hit_rate | jaccard | tie_rows | no_match |
|---|---|---|---|---|---|---|---|
| robertson | 0.9 | 1 | tf-saturated | 0.4773 | 0.4602 | 589 | 5 |
| robertson | 1.2 | 1 | tf-saturated | 0.4762 | 0.4590 | 607 | 5 |
| lucene | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| atire | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| bm25+ | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| lucene | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 564 | 0 |
| atire | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 565 | 0 |
| bm25+ | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 565 | 0 |
| robertson | 2 | 0.75 | tf-saturated | 0.4757 | 0.4620 | 481 | 5 |
| bm25l | 2 | 0.75 | tf-saturated | 0.4752 | 0.4633 | 461 | 0 |

What the grid says: the best configuration of every method is within 0.002 of
the others (robertson 0.4773, lucene/atire/bm25+ 0.4760, bm25l 0.4752); `b` is
what matters (best `hit_rate` 0.248 at `b` 0, 0.397 at 0.25, 0.452 at 0.5, 0.476
at 0.75, 0.477 at 1), `k1` hardly (0.473 to 0.477 across all six values); the
best binary configuration (robertson, `k1` 0.9, `b` 1) reaches 0.4713, the
saturated query adds about 0.006. `lucene`, `atire` and `bm25+` coincide where
they should (the constant `bm25+` adds to a query term is dropped, and does not
change a ranking).

Full sweeps (112,077 rows, 109,000+ counted), the four configurations measured
against the 4B:

| method | k1 | b | query | hit_rate | jaccard | closest | top5 | tie_rows | id_shared | no_match |
|---|---|---|---|---|---|---|---|---|---|---|
| robertson | 1.2 | 1 | tf-saturated | 0.4760 | 0.4575 | **944** | 2.764 | 6,314 | 2,769 | 39 |
| robertson | 0.9 | 1 | tf-saturated | 0.4748 | 0.4565 | 939 | 2.759 | 6,117 | 2,769 | 39 |
| lucene | 0.9 | 1 | tf-saturated | 0.4730 | 0.4576 | 933 | 2.754 | 5,646 | 2,764 | 0 |
| lucene | 1.5 | 0.75 | binary (**defaults**) | 0.4593 | 0.4470 | **933** | 2.708 | 4,487 | 2,077 | 0 |

**Decision: the defaults stay.** The row-level `hit_rate` does rise, by 1.7
points (0.4593 to 0.4760), but the quantity the Atlas shows -- the closest
instrument of each of the 1,303 -- agrees with the 4B for 944 instruments at
best against the defaults' 933: **+11, 0.8 percentage points**, under the
materiality bar fixed beforehand (+2 points, 26 instruments). Two of the three
winners do not move it at all or by 6. The mean top-five overlap with the 4B
moves from 2.708 to 2.764 of 5. The better row-level scores also tie more (6,314
rows against 4,487) and drop more shared word-for-word rows (2,769 against
2,077): part of the hit rate is bought by ties, where a row names several
instruments and one of them agrees. So `atlas-bm25.json`, the pair files, the
`atlas.qmd` prose and the `PUBLICAR.md` hand-off (`emb-run-atlas-bm25/atlas-pairs/`)
are exactly what #267/#268 produced, and nothing was regenerated or uploaded.


### Comparing the three data sets against strong links (issue #272)

Until here the only comparison between the 0.6B, the 4B and BM25 was
*agreement* (the closest instrument coincides for 1,077 of 1,303 between the two
embeddings, 933 and 906 for BM25), and `tune_bm25.py` even optimised BM25 toward
the 4B. Agreement measures consensus, not quality. This adds a gold standard
external to all three: a federal *reglamento* (and a few *lineamientos*) exists
to develop one specific law, and says so itself. A **strong link** is an
instrument (the *source*, a reglamento or lineamiento) -> a law (the *target*,
one of the 315 `leyes`), found by two offline signals over the 1,303 unique
instruments of `emb-run-atlas/instruments.parquet`:

* **A -- the name.** `REGLAMENTO DE LA LEY FEDERAL DEL TRABAJO` contains the
  name of a law. Names are folded (NFKD, accents, case, whitespace; punctuation
  kept), the 315 folded law names form one alternation, longest first, and a
  match nested inside a longer matched law name is dropped. A folded name two
  laws share (`LEY de Amnistía`: `lamn`, `lamni`) is *ambiguous* and gives no
  link (none of the 1,303 names hit it). A name that says `ley` or `codigo` and
  resolves to no law is `unresolved`: the law is not among the 315 (abrogated:
  *Ley Federal de Turismo*, *Ley Forestal*, ...). Unresolved names are counted
  and reported, never gold and never a negative -- no model can be right or
  wrong about a law that is not in the corpus. A law with a long name
  (`LEY de Amparo, Reglamentaria de los artículos 103 y 107 ...`) is found only
  by its full name; a reglamento that spells it shorter in its title is a miss
  of signal A (it can still be found by B).
* **B -- the "objeto" sentence.** "El presente Reglamento tiene por objeto
  reglamentar la Ley ...". Over the instrument's `article` and `loose` unit
  rows that are not in a transitorios section, the sentences (split on `.`
  followed by whitespace) in which an instrument word (`presente`/`este`/...
  `reglamento`/`ordenamiento`/`lineamientos`/...) is followed, within 120
  characters, by `tiene(n) por objeto` or `objeto reglamentar|regular|
  establecer|desarrollar`. The gold laws are the ones named **in that same
  sentence**, not anywhere in the unit. The evidence kept is the unit's `eId`
  and the sentence. **Tightened after reading `gold.md`:** the first version
  (the phrase anywhere in a non-heading, non-transitorio unit) gave 263
  instruments and its B-only links were full of false positives: a preamble's
  "con fundamento en los artículos ... de la Constitución" clause, the objeto of
  a *chapter* of a larger reglamento, a commission's own objeto ("la Comisión
  tiene por objeto"), and definitions articles whose one run-on sentence held the
  phrase far from any subject. It is why the subject is now required, why only
  `article` and `loose` units are read, and why the Constitution is never a
  signal-B target (about thirty regulations name it as the *basis* of an organ's
  powers; it stays a valid target of A). `gold.json` is never edited by hand.

Direction is source -> target only: `A` is directed by design and a law points
at many things, so the reverse is not a strong link. The gold of an instrument is
A union B; `laws_A` keeps A alone, which is the high-precision control. B agrees
with A where both speak: of the 80 instruments with both signals, B names the
law A names in all 80.

**Four metrics at the instrument level**, per gold instrument `i` with gold laws
`G` and row `A[i]` (`evaluate_links.py`): *closest* (a gold law is rank 1),
*top-5* (a gold law has rank <= 5), *reciprocal rank* of the best-ranked gold
law (0 when every gold law has weight 0), and *weight share*,
`sum(A[i, G]) / A[i].sum()`. Rank is the **competition rank** (1 + the instruments
with a strictly greater weight, tolerance 1e-6): equal weights are frequent in
BM25's rows and `argmax` would break them toward the lowest column, which is
always a law, so the optimistic rank is stated and used for every model, and the
table reports how many "closest" were a tie with a non-gold instrument. An
instrument whose row sums to 0 in any model is skipped in all (none was).

**Significance, fixed before the numbers were read.** For every pair of models and
every metric, a paired bootstrap over the gold instruments (seed 0, 10,000
resamples) gives the 95 % percentile interval of the mean difference; for
closest and top-5, McNemar's exact test (binomial on the discordant pairs,
`scipy.stats.binomtest`) is added. A difference is **real only if the interval
excludes 0 and, for a binary metric, McNemar's p < 0.05**; otherwise it is a tie.

```bash
uv run --group viz python scripts/embeddings/gold_links.py --work-dir emb-run-atlas
#   ~6 s: the unit texts come from legalvec's cache; writes emb-run-atlas/gold-links/gold.json, gold.md
uv run --group viz python scripts/embeddings/gold_links.py --report            # offline
uv run --group viz python scripts/embeddings/evaluate_links.py
#   the three default work directories (0.6B, 4B, BM25); writes evaluation.json, evaluation.md
uv run --group viz python scripts/embeddings/evaluate_links.py --report        # offline
```

`evaluate_links.py` refuses a work directory whose `instruments.parquet` is not
the gold's (`coleccion`, `clave` at every `i`) or whose `matrix.npy` is missing
or of the wrong shape, naming the path; it never rebuilds either. Nothing here
uses Slurm or the network, and `emb-run-atlas/gold-links/` is covered by the
`/emb-run*/` ignore rule. The gold is model-independent, so it lives in the
0.6B's directory by convention. Tests: `tests/test_gold_links.py`,
`tests/test_evaluate_links.py`.

### Measured, strong links, 2026-10-05 (issue #272)

Gold (`gold_links.py`, commit `7546baf2`): of the 988 reglamentos (863) and
lineamientos (125) of the Atlas, **132 instruments by name (A)** (131 reglamentos,
1 lineamiento), **160 by the objeto sentence (B)** (153 + 7), **212 in A union B**
(204 + 8), 80 in both, 80 by B only; 227 instrument -> law links (132 by A, 175
by B, 95 of them B-only), 12 instruments whose B names more than one law, 9 with
two gold laws and 3 with three. **55 unresolved names** (50 say `DE LA LEY`, 3 more name an abrogated law
otherwise, 2 name the *Código Federal de Instituciones y Procedimientos
Electorales*; 3 of the 55 are lineamientos), 0 ambiguous. Every B link carries an
`eId` and a sentence. The issue counted 53 unresolved names while planning, over
names saying `DE LA LEY ...`; the rule here (`ley` or `codigo` in the name, no
resolved law) is wider and gives 55. A loose first version of B had 263 instruments; reading ten B-only links
by eye is what tightened it (above).

Metrics, count (share) of the instruments scored, none skipped:

| gold | metric | 0.6B | 4B | BM25 |
|---|---|---|---|---|
| A (132) | closest is a gold law | 100 (0.758) | 96 (0.727) | 101 (0.765) |
| A (132) | a gold law among the five closest | 123 (0.932) | 122 (0.924) | 126 (0.955) |
| A (132) | mean reciprocal rank | 0.836 | 0.819 | 0.845 |
| A (132) | weight share on gold laws | 0.373 | 0.400 | 0.367 |
| A union B (212) | closest is a gold law | 126 (0.594) | 122 (0.575) | 126 (0.594) |
| A union B (212) | a gold law among the five closest | 180 (0.849) | 177 (0.835) | 181 (0.854) |
| A union B (212) | mean reciprocal rank | 0.701 | 0.690 | 0.700 |
| A union B (212) | weight share on gold laws | 0.283 | 0.303 | 0.279 |

"Closest" ties with a non-gold instrument: 2 / 0 / 0 (A) and 3 / 1 / 3 (A union
B) for 0.6B / 4B / BM25, so the competition rank gave BM25 and the 0.6B at most
three instruments each.

Verdicts under the rule above (difference of the first minus the second; the
full intervals and McNemar p-values are in `evaluation.md`):

| pair | closest | top-5 | MRR | weight share |
|---|---|---|---|---|
| 0.6B vs 4B, A | tie (+0.030) | tie (+0.008) | tie (+0.016) | **4B better** (-0.027) |
| 0.6B vs BM25, A | tie | tie | tie | tie |
| 4B vs BM25, A | tie | tie | **BM25 better** (-0.026) | **4B better** (+0.033) |
| 0.6B vs 4B, A union B | tie | tie | tie | **4B better** (-0.021) |
| 0.6B vs BM25, A union B | tie | tie | tie | tie |
| 4B vs BM25, A union B | tie | tie | tie | **4B better** (+0.024) |

**Reading.** On what the page shows -- the closest instrument and the five
closest -- **the three are indistinguishable**: all differences are a handful of
instruments (at most 5 of 132 on A, 4 of 212 on A union B), no McNemar test comes
near 0.05 (smallest 0.125), and the control (A alone) and the wider gold (A union
B) say the same thing. Three differences are real: the **4B puts a larger share
of each row's weight on the gold law** (0.400 against 0.373 and 0.367 on A; 0.303
against 0.283 and 0.279 on A union B), against both the 0.6B and BM25 -- the link
is more concentrated in the 4B, even though it is not more often first -- and,
on A alone, BM25's reciprocal rank is better than the 4B's (+0.026, interval
[0.002, 0.054], a small effect that does not survive on the wider gold). The 4B
is also the model BM25 was tuned toward, and is the one with the *fewest* gold
laws first: agreement with it was never evidence of quality. A lexical baseline
with no learned model is as good as either embedding at finding the law a
reglamento develops, which is what the page's BM25 option already suggested.

### Row level: explicit citations (issue #273)

#272 asks, per instrument, whether the law a reglamento develops is its closest
instrument. A unit row that cites "artículo 123 de la Ley Federal del Trabajo"
names the law it is about, so the same three data sets can be asked where that law
ranks among the 1,302 foreign instruments *for that one row*. `nearest.parquet`
keeps only a row's winner, which is why the answer needs the row's whole score
vector, and therefore a re-scoring pass.

**Signal C** (`gold_links.py`, `citations.parquet`). Over every unit row of a
reglamento or lineamiento that is neither a heading nor in a transitorios section,
on folded text (an ordinal's full stop, `1o.`, removed): `articulo(s) N[ bis|ter|...]`,
at most 80 characters with no `.` or `;` between, then `de la`, `del` or `de el` and
a law name (the same longest-first alternation as signal A). A gap that says
`reglamento` is a citation of the regulation behind the law, not of the law
("artículo 3 del Reglamento de la Ley Federal del Trabajo"), and is skipped;
a name two laws share resolves to nothing. One row of the table per `(source i,
text_sha1, cited law)`, with the first article and matched span as evidence,
`hits` and `single_law` (the text cites exactly one distinct law). Law level
only: the cited *article* is not resolved (renumbering, `Bis` articles and ranges
are a project of their own).

**Scoring** (`evaluate_citations.py` -- a script of its own rather than
`evaluate_links.py --level row`, which would have had to carry a scorer, a mask and
a row join the instrument level never needs; it imports that script's bootstrap,
McNemar test and instrument check). For each model's work directory: its own
scorer (`scoring.scorer_for`: the exact cosine or BM25), the unit-row join, and
exactly the mask the matrix used -- the citing instrument's *exclusively owned*
texts and the texts no searched row owns removed (`instrument_matrix.owners_of_rows`
and `own_rows_of`, imported). Each instrument's score is the best over the vector
rows it owns, and the cited law's **competition rank** is 1 + the foreign
instruments scoring strictly more (tolerance 1e-6, relative for BM25). A text the
row shares word for word with the cited law is a column the mask keeps, so it ranks
the law first -- the matrix's own rule, kept, and counted (`law_owns_text`; zero in
this run: no citing text is also a text of the law it cites). A law with no score
at all has no rank (reciprocal rank 0, never recalled, `n_foreign + 1` for the
median). Two checks keep it an evaluation of the published data sets rather than of
a fourth method: the best foreign score recomputed here equals `nearest.parquet`'s
`similarity` for **every** citing row (a `SystemExit` otherwise; the largest
difference was 4.8e-7 for the 0.6B, 3.6e-7 for the 4B and 0 for BM25), and a row is
scored only if the matrix *counted* it in every model, so the models are compared on
the same rows (the `identical_shared` and `no_match` rules remove the rest, and the
report says how many).

**Metrics**: recall@1, recall@5, recall@10, MRR and the median rank, per model; per
collection; without the Constitution; and for the ten most cited laws. Significance
is #272's rule, fixed beforehand: paired bootstrap over rows (seed 0, 10,000
resamples), 95 % percentile interval of the mean (median) difference, McNemar's
exact test for recall@k, real only if the interval excludes 0 and p < 0.05,
otherwise a tie.

```bash
uv run --group viz python scripts/embeddings/gold_links.py --work-dir emb-run-atlas
#   writes citations.parquet next to gold.json (about 25 s)
uv run --group viz python scripts/embeddings/evaluate_citations.py
#   re-scores the three work directories: about two minutes, no Slurm, no network
uv run --group viz python scripts/embeddings/evaluate_citations.py --report     # offline
```

It refuses a work directory that is not the gold's instruments, a missing
`nearest.parquet` (naming the path), a missing scorer input, a citing text with no
vector row, and any disagreement with `nearest.parquet`; it rebuilds none of them.
Tests: `tests/test_evaluate_citations.py` (an independent oracle over the toy corpus
for every `(source, text, cited instrument)`, the mask, a shared text, both scorers)
and the signal-C cases of `tests/test_gold_links.py`.

### Measured, row-level citations, 2026-10-05 (issue #273)

Signal C (commit `82ec9f4e`): **3,647 citations**, **2,537 distinct citing unit rows**
(`(source, text)`) from **832 instruments** citing **179 laws**; 3,104 rows of
`citations.parquet`; **2,010 cite exactly one law** (527 cite several: counted, not
scored). The planning figures with a looser regex were 3,575 / 2,566 / 822 / 172 /
2,107; the difference is `del` before a `Código`, the ordinal full stop and the
`reglamento` guard. Seven citing rows are `identical_shared` rows (the same seven
for the 0.6B, the 4B and BM25) and are removed, leaving **2,003 scored rows**
(1,831 in reglamentos, 172 in lineamientos); 1,444 without the Constitution, which
alone is 559 of them (28 %) because every kind of regulation cites it.

| metric | 0.6B | 4B | BM25 |
|---|---|---|---|
| recall@1 | 191 (0.095) | 207 (0.103) | 182 (0.091) |
| recall@5 | 507 (0.253) | 546 (0.273) | 483 (0.241) |
| recall@10 | 701 (0.350) | 761 (0.380) | 694 (0.346) |
| MRR | 0.180 | 0.194 | 0.173 |
| median rank (of 1,302) | 29 | 21 | 29 |
| *without the Constitution (1,444)* recall@5 | 486 (0.337) | 522 (0.361) | 467 (0.323) |
| *without the Constitution* MRR | 0.238 | 0.252 | 0.231 |
| *without the Constitution* median rank | 13 | 11 | 13 |

Verdicts (first minus second; intervals and p-values in `evaluation-rows.md`):

| pair | recall@1 | recall@5 | recall@10 | MRR | median rank |
|---|---|---|---|---|---|
| 0.6B vs 4B | tie | **4B better** (p 0.001) | **4B better** (p < 0.0001) | **4B better** | **4B better** (29 vs 21) |
| 0.6B vs BM25 | tie | tie | tie | tie | tie |
| 4B vs BM25 | tie (p 0.054) | **4B better** (p 0.0003) | **4B better** (p 0.0004) | **4B better** | **4B better** (21 vs 29) |

**Reading.** Unlike the instrument level, where the three are tied on what the
page shows, **the 4B is better at this question**: the cited law is in its five
closest instruments for 27.3 % of the rows against 25.3 % (0.6B) and 24.1 %
(BM25), and its median rank is 21 against 29. The two 4B differences are small
(two to three points of recall) but not noise; the 0.6B and BM25 are
indistinguishable, and nobody separates on recall@1, which is under 11 % for all
three. The absolute level is low: a row that cites an article of a law is
usually not about that law's *other* texts, and the Constitution alone (559
rows, recall@5 of 3 to 4 % in every model) drags it down, which is why the
without-Constitution rows are also reported; the order is the same without it.
The split by law is where the lexical baseline is not beaten: of the ten most cited
laws BM25's recall@5 is far higher for the *Código Fiscal de la Federación*
(0.684 against 0.439 for the 4B and 0.351 for the 0.6B) and lower for the *Ley
Orgánica del Poder Judicial de la Federación* (0.195 against 0.439 and 0.488), and
the three are within a few rows on the others. Per collection, the order holds in
reglamentos (1,831 rows); in lineamientos (172) the 4B leads and BM25 is last
(recall@10 0.523 / 0.500 / 0.390), without a test. Law level only: this says
nothing about whether the right *article* ranks first.
