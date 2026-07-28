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
python -m leyesmx --ley normas         # all 4,674 Normas Oficiales Mexicanas
python -m leyesmx --ley tratados       # all 1,956 international treaties
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

## Normas Oficiales Mexicanas

NOMs need no second source, and that is the whole difference. For a law the DOF
never says which law a decree amends, which is why Diputados' curation is
indispensable — but a NOM's DOF title **contains the NOM's own code**, so the
link is intrinsic and the history reads off the titles dataset alone. Just as
well, because LeyesBiblio does not carry NOMs at all: not one "NOM-" appears
across its pages.

A NOM's life in the gazette, as `NOM-001-SCFI-1993` has it:

```
03-05-1993  PROYECTO de Norma Oficial Mexicana NOM-001-SCFI-1993…
11-10-1993  RESPUESTA a los comentarios recibidos respecto del Proyecto…
13-10-1993  NORMA Oficial Mexicana NOM-001-SCFI-1993, aparatos electrónicos…
19-12-2017  Proyecto de Norma Oficial Mexicana PROY-NOM-001-SCFI-2017…
17-09-2019  Norma Oficial Mexicana NOM-001-SCFI-2018…
14-05-2020  Modificación al Transitorio Primero…
```

A draft is keyed to the NOM it drafts, so `PROY-` is stripped. A note citing
several codes belongs to each: the note issuing a revision usually also cancels
the edition it replaces, which is how a lineage stays traceable.

| | |
|---|---|
| NOMs | 4,674 |
| DOF notes | 8,880 |
| With more than one note | 2,044 |

`data/normas/noms.json` maps each code to its notes, oldest first;
`catalogo.json` adds the span, the count and a descriptive title — taken from
the note that *is* the norm, since the most recent one is as often a notice of
public consultation and says nothing about the subject. One file rather than
one per NOM: 4,674 files each holding a handful of numbers would cost more than
they tell.

**Codes are not decomposed.** Sixty years of the gazette have left 253 distinct
code shapes — `NOM-001-SCFI-1993`, `NOM-150-1979`, `NOM-C-247-1978`,
`NOM-EM-002-SSA2-1993`, `NOM-015-SCT-2-1993`. Parsing the parts invites reading
a year as a dependency, which mislabelled 927 notes on the first attempt, so
the normalized code string is the identifier.

**Codes cited short.** Titles often cite a NOM by part of its code. Where
exactly one full code extends it the citation is folded in — `NOM-186-SSA1`
into `NOM-186-SSA1-2000` — which recovers 114 such citations. Where several do,
it cannot be resolved: `NOM-021` is equally the ASEA, the SAG and the SCT4
norm. Those 287 codes and their 669 notes go to `citas-ambiguas.json` rather
than being dropped or guessed at — the notes do concern a NOM, only which one
cannot be told.

## International treaties

No spine exists for these either, and here not for want of one. LeyesBiblio
does not carry treaties, and `cja.sre.gob.mx/tratadosmexico` — the SRE's
official register — answers a Radware bot-management challenge instead of data,
so it is not a source a program can read. The gazette is read directly again,
but a treaty has no code, only a name, which makes this the least certain of
the four.

A treaty reaches the DOF as two decrees, months or years apart:

```
10-01-1995  DECRETO por el que se aprueba el Convenio entre los Estados Unidos
            Mexicanos y la República de Corea, para evitar la Doble Imposición…
16-03-1995  DECRETO de promulgación del Convenio entre los Estados Unidos
            Mexicanos y la República de Corea para Evitar la Doble Imposición…
```

Pairing them is the whole problem: the same instrument is worded differently
each time — "2007" against "dos mil siete", "dado en Madrid" against "adoptado
en Madrid".

| | |
|---|---|
| Treaties | 1,956 |
| DOF decrees | 2,745 |
| Both decrees, names identical | 517 |
| Both decrees, matched | 272 |
| A single decree | 1,167 |

**A treaty with one decree is the norm, not a miss.** Publishing both is a
recent practice: the pairing rate climbs from 0% in the 1970s to about half in
the 2010s, and for older treaties the gazette simply ran one of the two. Every
one of the 2,745 decrees is accounted for in some treaty.

**Names are weighted by word rarity, not compared as strings.** Treaty names
are formulaic — "convenio entre el gobierno de los estados unidos mexicanos y
el gobierno de la república de X para…" — so plain string similarity is
dominated by the boilerplate and rates unrelated instruments highly: it gave
**0.88** to a 1977 trade agreement with Gabon paired against a 1994 framework
agreement, higher than it gave real pairs. Weighting each word by how rare it
is puts that false pair at 0.56 and real ones at 0.72–0.78, so the threshold
sits at 0.70. A promulgation is never paired with an approval that follows it,
and each decree is claimed once.

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
