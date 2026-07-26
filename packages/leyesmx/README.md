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
python -m leyesmx --ley cpeum          # -> data/reformas/cpeum.csv
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

`data/reformas/cpeum.csv` — the Constitution's 284 reforms, 1917–2026:

| columna | qué es |
|---|---|
| `ley` | LeyesBiblio abbreviation (`cpeum`) |
| `no` | Diputados' own reform number; empty for the original 1917 text |
| `fecha` | DOF publication date, `DD-MM-YYYY` |
| `codNota` | the DOF note that published it |
| `confianza` | title-match score, `1.0` when the DOF title appears verbatim |
| `titulo_dof` | the note's title as the DOF published it |
| `decreto_dip` | the decree as Diputados records it |

Current state: **284 of 285 rows carry a `codNota`** (267 verbatim matches, the
rest wording variants above 0.89).

### One reform has no note

Reform 139 (`08-03-1999`, amending articles 16, 19, 22 and 123) has no note,
because **the DOF's own open-data service returns zero notes for that day** —
confirmed against the live service, not an artifact of this pipeline. It is
kept in the table with an empty `codNota` rather than dropped: a gap in the
source is worth surfacing.

## Scope

Only the Constitution so far. Ordinary laws use a different LeyesBiblio page
layout (`ref/<abbr>.htm`, unnumbered rows), which `pagina_de_reformas` already
routes to but the parser has not been verified against.
