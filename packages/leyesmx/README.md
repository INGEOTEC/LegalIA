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

`data/reformas/cpeum.json` — the Constitution's reforms, 1917–2026, as a plain
list of `codNota`, oldest first:

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

**Index N is reform N**, and index 0 is the original 1917 text — the invariant
holds across all 284 reforms, which is why a reform without a note is written
as `null` instead of being skipped.

### One reform has no note

Reform 139 (`08-03-1999`, amending articles 16, 19, 22 and 123) is that
`null`, because **the DOF's own open-data service returns zero notes for that
day** — confirmed against the live service, not an artifact of this pipeline.

## Scope

Only the Constitution so far. Ordinary laws use a different LeyesBiblio page
layout (`ref/<abbr>.htm`, unnumbered rows), which `pagina_de_reformas` already
routes to but the parser has not been verified against.
