The per-instrument files behind the website's Instruments page: for each of the
2,676 unique federal laws, regulations and guidelines and international treaties
of the [Atlas](https://ingeotec.github.io/LegalIA/pages/atlas.html), one JSON
file holding the document, the tree `md2akn` read in it, the excerpts it was cut
into, parse-quality flags and, on every searched excerpt, its five nearest texts
in other instruments.

| Asset | Contents |
|---|---|
| `atlas-instruments-qwen3-4b.tar.gz` | `files/<coleccion>/<clave>.json`, one file per instrument, plus `manifest.json`; built from **Qwen/Qwen3-Embedding-4B** (K = 2560) |
| `manifest-qwen3-4b.json` | What produced the files: model, commit, `md2akn` version, counts, sizes, flag totals and the nearest-texts run, the same file the tarball carries as `manifest.json` |
| `SHA256SUMS-qwen3-4b.txt` | The digest of the two assets above |

Every asset name carries the model's slug (`legalvec.model_slug`); a further
model would add its own three assets to this release.

Each file shows an instrument **at the one snapshot its vectors were built
from** (the `archivo` of the vectors release's `units.parquet`), parsed again
with `md2akn` and checked, excerpt by excerpt, against the released
`units.parquet` before it is written. Keys: `meta` (the Atlas position `i`, `c`,
`k`, `n`, the snapshot, `md2akn_version`, the model, the numbering mode and the
flag counts), `markdown` (the raw document, stored once), `tree` (every node as
`{t, e, n, h, r, s, l, x, a, c}`: type, `eId`, number, heading, `refersTo`,
start, end, chapeau/tail marks, reform annotations, children) and `units` (one
per excerpt: `u`, `e`, `p`, `pe`, `s`, `l`, `sha`, `v`, `x` when it was searched
by the Atlas, `f` its flags and, on searched excerpts, `near`: up to five
`[j, eId, piece, similarity, also]` entries, `j` being the position of the
owning instrument in the Atlas' `instruments` array and `also` the other owners
of the same text). **Offsets `s`/`l` are UTF-16 code units**, so a browser
slices `markdown` directly. A flag is a count, not a judgement: `loose`,
`duplicate_number`, `numbering_gap`, `over_window`, `no_articles`, `uncovered`
and `no_transitorios` say where to look first.

The website's publish workflow downloads the tarball and unpacks it into the
site: GitHub release assets carry no CORS header, so a browser cannot read them
from here. Built by `scripts/embeddings/export_instrument_files.py` (issue #289)
from the gitignored `emb-run-atlas-4b/` work directory and `nearest_texts.py`'s
output (issue #288), and published by a human, never by a workflow (issue #115,
Hallazgo C). The SCJN is not an official source of legal text; the DOF is.
