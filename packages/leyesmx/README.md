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
python -m leyesmx --ley reglamentos    # all 137 federal regulations
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

Regulations live in `data/reformas/reglamentos/`, with their own
`reglamentos.json`. They are kept apart because their identifiers come from
Diputados' file names (`reg_ladua`) and the laws' from its index
(`reg_senado`), so nothing stops the two from colliding one day.

### How much is linked

| | Laws | Regulations |
|---|---|---|
| Instruments | 316 | 137 |
| Entries | 3,450 | 287 |
| With a `codNota` | 3,440 | 277 |
| Verbatim title match | 3,337 | 261 |

Every one of the **3,136** numbered reforms of a law is linked. The ten
unlinked entries are all original publications, and each for a reason worth
keeping visible rather than papering over: the Código de Comercio's (1889) and
the Ordenanza General de la Armada's (1912) predate the DOF archive itself;
some days carry very few notes in the dataset and not the one wanted (the Ley
Aduanera's 15-12-1995 has 12); and the Ley de Fondos de Inversión was
published under its former name, Ley de Sociedades de Inversión.

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

## Matching a note to an entry

Which metric applies depends on what LeyesBiblio gives. A numbered reform of a
law comes with the decree's own title, and the DOF title is typically that
title without Diputados' editorial summary, so containment settles it.

An original publication comes with no title at all — only the instrument's
name — and so does **every** entry of a regulation. There the question is the
other way round: does the DOF title *name* this instrument? Using the first
metric where only a name is available is not merely weaker but wrong: it
linked the Ley Federal del Trabajo's 1970 publication to a Mexico City
traffic-regulation decree, the Código Fiscal's to the 1982 budget, and a
reform of the Reglamento de la Ley de Aeropuertos to a mining-claim notice.

Name matches also carry a floor, below which the entry is left unlinked. A
busy day carries a hundred notes, and half a name's words matching is as
likely to be coincidence as not — an unlinked entry says less than a wrong
one.

## Reading LeyesBiblio

Three page layouts, told apart by content. The Constitution has a chronological
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

Regulations (`regla.htm`) are laid out differently again: there is no page per
regulation, so the whole history sits inline in the index row, one anchor per
entry — the file name says what kind of entry it is and the link text is the
date. A single paragraph can switch kind part-way ("Reformas *a*, *b*, Fe de
E. *c*"), so the row is walked in order with the last label carried forward.

Three naming generations coexist and only the newest states the reform number
(`Reg_LAero_ref03_29sep17`, then `Reg_LAero_ref080800`, then plain
`Reg_LGP_29nov06`), so the number comes from chronological order instead.
Diputados numbers them chronologically anyway, and every number it *does*
state agrees with that position — checked with `numeracion_declarada()`, which
reports no disagreement across all 137.

Of those 137 rows, 49 link no history at all. They still state their
publication date in the row's own column, so they are recorded with that and
no reforms. `norma/reglamento.htm` ("Reglamentos Federales Vigentes") is a
directory of current texts with no history whatsoever — 145 dates and not one
`_refNN_` file — so no reform list can come from it.
