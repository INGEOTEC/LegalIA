The evidence behind every **Closest instruments** weight of the website's
[Atlas](https://ingeotec.github.io/LegalIA/pages/atlas.html): for each of the
1,523 federal laws, regulations and guidelines, and each of the (at most) five
instruments it points at hardest, which of its provisions point there, the
text they matched on the other side, and how much each one adds to the weight.

| Asset | Contents |
|---|---|
| `atlas-pairs.tar.gz` | `pairs/<i>-<j>.json`, one file per pair, plus `manifest.json` |
| `manifest.json` | What produced the files: commit, matrix summary, tolerance, counts, sizes |
| `SHA256SUMS.txt` | The digest of the two assets above |

`i` and `j` are positions in the Atlas' own `atlas.json` `instruments` array,
and every file names both instruments' `clave` (`source.k`/`target.k`), so a
tarball built against a different instrument table is detectable. A file's
rows are one per provision of the source instrument whose nearest text outside
it belongs to the target; each adds `1/m`, `m` being how many instruments own
that winning text, so the rows add up to the weight the Atlas shows (issue
#242's rule). Texts are Markdown as `md2akn` emits it, each stored once per
file.

The website's publish workflow downloads this asset and unpacks it into the
site: GitHub release assets carry no CORS header, so a browser cannot read
them from here. Built by `scripts/embeddings/export_atlas_pairs.py` (issue
#249) from the gitignored `emb-run-umap/` work directory, and published by a
human, never by a workflow (issue #115, Hallazgo C). The SCJN is not an
official source of legal text; the DOF is.
