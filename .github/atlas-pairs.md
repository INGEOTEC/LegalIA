The evidence behind every **Closest instruments** weight of the website's
[Atlas](https://ingeotec.github.io/LegalIA/pages/atlas.html): for each of the
1,303 unique federal laws, regulations and guidelines, and each of the (at most) five
instruments it points at hardest, which of its provisions point there, the
text they matched on the other side, and how much each one adds to the weight.

| Asset | Contents |
|---|---|
| `atlas-pairs.tar.gz` | The **Qwen/Qwen3-Embedding-0.6B** set: `pairs/<i>-<j>.json`, one file per pair (6,372 files, 165 MB of JSON, 35 MB compressed), plus `manifest.json` |
| `manifest.json` | What produced the files: model, commit, matrix summary, tolerance, counts, sizes |
| `SHA256SUMS.txt` | The digest of the two assets above |
| `atlas-pairs-qwen3-4b.tar.gz` | The same, built from **Qwen/Qwen3-Embedding-4B** (K = 2560): `pairs/<i>-<j>.json` plus `manifest.json` |
| `manifest-qwen3-4b.json` | The 4B set's manifest, the same file the tarball carries as `manifest.json` |
| `SHA256SUMS-qwen3-4b.txt` | The digest of the two 4B assets above |

The two sets are independent: each is checked and unpacked on its own, into
`website/pages/atlas/pairs/` and `website/pages/atlas/pairs-qwen3-4b/`. The
unsuffixed names have always meant the 0.6B, which stays the Atlas' default; a
further model would add its own `legalvec.model_slug` to each name the same way.

`i` and `j` are positions in the Atlas' own `instruments` array (`atlas.json` for
the 0.6B, `atlas-qwen3-4b.json` for the 4B; both list the same instruments in
the same positions),
and every file names both instruments' `clave` (`source.k`/`target.k`), so a
tarball built against a different instrument table is detectable. A file's
rows are one per provision of the source instrument whose nearest text outside
it belongs to the target; each adds `1/m`, `m` being how many instruments own
that winning text, so the rows add up to the weight the Atlas shows (issue
#242's rule). Texts are Markdown as `md2akn` emits it, each stored once per
file.

The website's publish workflow downloads these assets and unpacks them into the
site: GitHub release assets carry no CORS header, so a browser cannot read
them from here. Built by `scripts/embeddings/export_atlas_pairs.py` (issue
#249) from the gitignored `emb-run-atlas/` (0.6B) and `emb-run-atlas-4b/` (4B,
issue #261) work directories, and published by a
human, never by a workflow (issue #115, Hallazgo C). The SCJN is not an
official source of legal text; the DOF is.
