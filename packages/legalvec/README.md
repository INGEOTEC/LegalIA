# legalvec

A vector for every text of every Mexican federal instrument — the reader.

This project embeds the *current* text of three SCJN corpora — every federal
law, every federal *reglamento*, every federal *lineamiento*, segmented into
retrieval units by [`md2akn`](../md2akn) — one vector per distinct text, and
publishes the result as three GitHub releases: `scjn-leyes-vectors`,
`scjn-reglamentos-vectors` and `scjn-lineamientos-vectors`. This package is
how they are read back (issue #227's Fase 4, which is #218's Fase 3 widened
to three corpora).

It is deliberately **not** part of [`scjn`](../scjn): `scjn` means "client
for the Court", and these vectors are this project's own, derived on a GPU
from a specific model revision rather than anything the SCJN publishes.
Reading them needs `pyarrow` and `numpy`, which `scjn` has no other reason to
take. Nothing here imports any other package of this monorepo
(`tests/test_boundary.py` greps for it).

```python
import legalvec

legalvec.download_vectors_assets("leyes", ["lft"])      # the only network call
conjunto = legalvec.load_vectors("leyes", "lft", "Qwen/Qwen3-Embedding-0.6B")
conjunto.vectors.shape                                   # (n, 1024), float16
units = legalvec.load_units("leyes")                     # text_sha1 -> the text
```

`clave` — `"lft"` above — is a law's slug and the `id_ordenamiento` of a
reglamento or a lineamiento, so a reader never branches on which collection
it is reading. `load_vectors` always reads two files and unions them: a text
several instruments share is published once, in
`vectors-shared-<model>-<K>.parquet`, and the per-instrument file holds only
what is that instrument's alone.

Disk-first, the posture `scjn` took in issue #209: every reader raises
`AssetNotCached` rather than downloading, and `download_vectors_assets` is
the only thing that talks to the network. The cache is its own directory —
`$LEGALVEC_CACHE_DIR`, `~/.cache/legalvec` by default — separate from
`scjn`'s and `nota2md`'s.

Both models stay live: `Qwen/Qwen3-Embedding-0.6B` at K=1,024 and
`Qwen/Qwen3-Embedding-4B` at K=2,560, in separate files. Issue #217's proxy
evaluation has not been run, so nothing has earned the right to drop either
— and putting both in one file per instrument would make whoever wants the
0.6B download the 4B's 2,560 floats to throw them away.

## Development

```bash
pip install -e "packages/legalvec[test]"
pytest packages/legalvec
```

No test here makes a network request: the readers are exercised against a
synthetic on-disk release, and the downloader with its HTTP layer patched
out.

## Changelog

- **0.1.0** — the package's first release: `download_vectors_assets`,
  `load_vectors`, `load_units`, `VectorSet`, `AssetNotCached`, `model_slug`
  and its own `$LEGALVEC_CACHE_DIR` (issue #227, Fase 4).

## License

Apache License 2.0. See [LICENSE](../../LICENSE).
