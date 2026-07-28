# leyesmx

Reform history of Mexican federal legislation, linked to the DOF notes that
published it.

## Why

Nobody publishes the evolution of a Mexican law as data. The two halves of the
story live apart:

- The **Cámara de Diputados** ([LeyesBiblio](https://www.diputados.gob.mx/LeyesBiblio/))
  curates, per law, the list of decrees that reformed it and the date each was
  published. This is the authoritative account of *which* decrees changed
  *which* law — the DOF itself never says so.
- The **DOF** publishes those decrees as notes, each with a `codNota` that
  addresses its full text (see [`dofjson`](../dofjson)).

`leyesmx` joins them, so each reform of a law becomes a row pointing at the
primary source that enacted it.

## Use

```bash
pip install -e packages/dofjson -e packages/leyesmx
python -m leyesmx --ley cpeum          # -> data/reformas/cpeum.json
python -m leyesmx --ley lft            # any LeyesBiblio abbreviation
python -m leyesmx --ley todas          # all 316 laws in the index
```

The first run downloads the titles dataset (~35 MB) unless `--titulos` points
at an existing one.

```python
from leyesmx import diputados, dof
from microtc.utils import tweet_iterator

reformas = diputados.parse_reformas(diputados.descarga("cpeum"), "cpeum")
enlazadas = dof.enlaza(reformas, tweet_iterator("titulos.jsonl.gz"))
```

## The data

One file per law, `data/reformas/<abbr>.json`, plus `leyes.json` with the
catalogue itself (number, abbreviation, name, counts). Each law's file is a
plain list of `codNota`:

```json
[
 4432273,
 4426823,
 ...
]
```

Only the codNota is stored. A note's title, date and issuing branch already
live in the dataset `dofjson.titulos.download_titulos` builds, and come back
by joining on codNota — keeping a second copy here would only let the two
drift apart:

```python
import json
from microtc.utils import tweet_iterator

reformas = set(json.load(open("data/reformas/cpeum.json")))
for nota in tweet_iterator("titulos.jsonl.gz"):
    if nota["codNota"] in reformas:
        print(nota["fecha"], nota["titulo"])
```

**Index N is reform N**, and index 0 is the law's original publication. Each
entry is placed by its number rather than by position, so the invariant holds
even where it otherwise would not: `ccf` and `ccom` have no original
publication on their page, and a reform without a note stays put as `null`
instead of shifting everything after it.

### Every reform is linked

All **3,136** reforms across the 316 laws have a `codNota`. The only two
entries without one are original publications that predate the DOF archive
itself: the Código de Comercio's (13-12-1889) and the Ordenanza General de la
Armada's (08-01-1912), both before the gazette's 1917 record begins.

Reform 139 of the Constitution (`08-03-1999`, amending articles 16, 19, 22 and
123) used to be a `null` too. The date was never a Diputados mistake — the
reform was published that day, and the gap was in SIDOF, which reports the
days it has lost as days with no gazette. `dofjson` now recovers those from the
DOF's own website (see `dofjson.dofweb`), so the note is in the dataset and the
reform is linked.

### A second route to the decree

**Diputados mirrors each decree itself**, independently of the DOF:

```bash
python -m leyesmx --ley cpeum --decretos decretos/
#   decreto 139 (08-03-1999) desde Diputados -> decretos/cpeum_ref_139_08-03-1999.pdf
```

`--decretos` downloads, from LeyesBiblio, exactly the decrees the DOF cannot
serve — none, as of now. The PDF opens on the gazette's own header — *"DIARIO OFICIAL Lunes 8 de
marzo de 1999"* — and carries extractable text, so the primary source stays
reachable by a second, independent route. Every one of the Constitution's 285
rows has such a PDF (184 also have a scan of the printed page), so this
fallback is complete rather than best-effort.

Each `Reforma` exposes that URL as `.pdf`, whether or not the DOF has the note.

## Reading LeyesBiblio

Two page layouts, told apart by content. The Constitution has a chronological
table (`ref/cpeum_crono.htm`) with each field in its own cell. Every ordinary
law uses `ref/<abbr>.htm`, where a row's cell holds the decree's title and its
date together, written either as `DOF DD-MM-YYYY` or bare as `DD-MM-YYYY`.

The reform table is not only reforms. Diputados files a dozen other kinds of
instrument in it — restatements of peso amounts (`_cant`), errata (`_fe`),
SCJN rulings (`_sent`, `_voto`), entry-into-force declarations (`_decla`),
`_acuerdo`, `_tarifa`, `_abro` and more — each numbered from 1 in the same
column. So whether a row is a reform is decided by the file it links, and
which reform it is comes from the numbering column, which is the more accurate
of the two where they disagree. Both sources are needed: plenty of rows leave
the column empty (`reg_senado` for reforms 23-29, `lft` for 35-36), while a
few link the wrong file (`lgpsedmtp`'s reform 4 links reform 3's PDF,
`loapf`'s reform 47 links another law's).

With that, all 316 laws parse with contiguous reform numbering, no duplicates
and no gaps.
