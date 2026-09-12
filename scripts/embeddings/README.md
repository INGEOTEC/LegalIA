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
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/build_units.py --work-dir emb-run-reglamentos \
    --coleccion reglamentos
python scripts/embeddings/build_units.py --work-dir emb-run-lineamientos \
    --coleccion lineamientos

# 2. Dedup by text_sha1, sort by estimated token length, balance into shards.
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes --num-shards 12

# 3. One model at a time: download it, submit every pending shard, wait, retry, delete it.
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
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
```

Steps 2-4 repeat per collection against its own work directory; nothing
below `build_units.py`/`merge_shards.py` knows what a collection is — a
shard is keyed by `text_sha1` alone.

Incremental re-embedding after a reform: replan against what a model already
has, then resubmit — only the new units reach the GPU at all.

```bash
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes \
    --against emb-run-leyes/runs/qwen3-0.6b
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
```

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
  cast to `float16` — no normalization, no quantization, no truncation.
- A failing shard writes `shard-XXXX.failed` and does not fail the run;
  `status.py` says what to relaunch.
