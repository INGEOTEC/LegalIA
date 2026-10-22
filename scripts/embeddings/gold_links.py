"""The gold set of strong instrument -> instrument links (issues #272, #283 fix-1).

The Atlas has three data sets (Qwen3-Embedding-0.6B, 4B and a BM25 baseline) built
over exactly the same comparison, and the only thing ever measured between them
was *agreement*, which cannot say which is right. An instrument that names another
-- a *reglamento* that develops one law, a law that refers to another, a treaty that
is "reglamentado" by a regulation -- is a link no similarity method was used to find,
a gold standard external to all three. **Every instrument of the Atlas is a potential
source and a potential target** (laws, regulations, guidelines and treaties, in any
direction); the one thing that never counts is an instrument naming **itself**. Three
signals, all offline, over the unique instruments of the Atlas
(`<work-dir>/instruments.parquet`):

* **A -- the instrument's name.** `REGLAMENTO DE LA LEY FEDERAL DEL TRABAJO`
  contains the name of another instrument. Names are folded (NFKD, accents, case and
  whitespace -- `unique_instruments.name_key`, punctuation kept); the folded names of
  *all* instruments form one alternation, longest first, so a match nested inside a
  longer matched name is dropped. The source's own name is matched first like any
  other (it is in the alternation) and is then **a self-mention**: it links to nothing
  and the names nested inside it are looked for again without it, so `REGLAMENTO DE
  LA LEY FEDERAL DEL TRABAJO` still links to the *Ley Federal del Trabajo* (and a
  name that is another instrument's whole name wins over the shorter one nested in
  it: a regulation of a regulation links to the regulation). A folded name two
  instruments share (`LEY de Amnistía`) is `ambiguous` and gives no link. A name that
  refers to an instrument ("de la Ley ...", "del Código ...", "del Reglamento ...",
  "del Tratado ...") and resolves to none is recorded as `unresolved` (the abrogated
  laws: *Ley Federal de Turismo*, ...) -- counted and reported, never gold and never a
  negative.
* **B -- the instrument's own "objeto" article.** "El presente Reglamento tiene
  por objeto reglamentar la Ley ..."; "La presente Ley tiene por objeto ...";
  "Este Convenio tiene por objeto ...". Over the instrument's `article`, `loose` and
  `intro` unit rows that are not in a transitorios section
  (`instrument_matrix.is_transitorio_path`), the sentences (split on `.` followed by
  whitespace) whose folded text matches `OBJECTO_PHRASE`; the targets are the
  instruments named **in that same sentence**, not anywhere in the unit, which keeps
  "sin perjuicio de lo dispuesto en la Ley X" clauses elsewhere in the article out.
  The evidence is the unit's `eId` and the sentence. A source never targets itself.
* **C -- explicit citations (issue #273), row level.** A unit that cites
  "artículo 123 de la Ley Federal del Trabajo" names the instrument it is about.
  Over every unit row of any instrument that is neither a heading nor in a
  transitorios section, `CITATION` finds `articulo(s) N[ bis|ter|...]`, at most 80
  characters without `.`/`;` and without the word `reglamento`, then `de la|del|de el`
  and an instrument name (the same longest-first alternation as A). A citation that
  resolves to the source itself is a **self-citation**: counted in the summary
  (`self_citations`), never written to `citations.parquet`, never scored.
  `citations.parquet` has one row per `(source i, text_sha1, cited instrument)` with
  the first cited article and matched span as evidence, `hits` (how many times that
  row cites that instrument) and `single_target` (the row cites exactly one distinct
  instrument: the only rows `evaluate_citations.py` scores).

The gold of an instrument is A union B; `targets_A` keeps A alone, which
`evaluate_links.py` reports as the high-precision control. Before issue #283's
fix-1 only `reglamentos` and `lineamientos` were sources and only `leyes` were
targets (`TARGET_COLLECTION`/`SOURCE_COLLECTIONS`, `LawMatcher`, `laws_A`, ...): the
old names are gone from the data (`targets_*`, `target`, `single_target`,
`target_names`), `LawMatcher` stays an alias of `InstrumentMatcher`, and nothing on PyPI
depends on any of them.

    python gold_links.py                      # writes <work-dir>/gold-links/
    python gold_links.py --report             # the summary, offline

Outputs, under `<work-dir>/gold-links/` (gitignored with the rest of `emb-run-*`):
`gold.json`, `gold.md` (a readable table of the B-only links, the ones a human
may want to spot-check), `links.md` (every link of the gold with its signal, the
table the website's Atlas page includes) and `citations.parquet` (signal C). The gold
is model-independent, so it lives in the 0.6B's directory by convention. Nothing here
needs the network or Slurm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _atomic import atomic_write_table, atomic_write_text  # noqa: E402
from instrument_matrix import EXCLUDED_UNIT_TYPES, is_transitorio_path  # noqa: E402
from unique_instruments import name_key  # noqa: E402

DEFAULT_WORK_DIR = Path("emb-run-atlas")
SUBDIR = "gold-links"
GOLD_JSON = "gold.json"
GOLD_MD = "gold.md"
LINKS_MD = "links.md"
CITATIONS = "citations.parquet"

#: Every collection of the Atlas is a source and a target (issue #283, fix-1).
COLLECTIONS = ("leyes", "reglamentos", "lineamientos", "tratados")

#: What makes a sentence an "objeto" sentence, on folded text (accents removed):
#: the instrument itself ("el presente Reglamento", "este ordenamiento", "los
#: presentes Lineamientos") followed within `SUBJECT_REACH` characters by the
#: "tiene(n) por objeto" phrase or "objeto reglamentar|regular|establecer|
#: desarrollar". The first version of this rule (the phrase anywhere) matched,
#: on the spot-check of `gold.md`, a preamble's "con fundamento en" clause, the
#: objeto of a *chapter* of a larger reglamento ("el presente Capitulo tiene por
#: objeto ..."), a commission's or programme's objeto ("la Comision tiene por
#: objeto"), and definitions articles whose single run-on sentence contained the
#: phrase far from any subject; it is why the subject is required and why only
#: `article`, `loose` and `intro` units are read (`SOURCE_UNIT_TYPES`).
SUBJECT_REACH = 120
OBJECTO_PHRASE = re.compile(
    r"\b(?:presentes?|este|esta|estos|estas)\s+"
    r"(?:reglamento|ordenamiento|lineamientos?|instrumento|disposiciones|reglas|manual|"
    r"acuerdo|decreto|estatuto|ley|codigo|tratado|convenio|convencion|protocolo)\b"
    r"[^.]{0,%d}?"
    r"(?:\btienen? por objeto\b|\bobjeto (?:de )?(?:reglamentar|regular|establecer|desarrollar)\b)"
    % SUBJECT_REACH)

#: Laws signal B never credits. The Constitution is named in the "tiene por objeto"
#: sentence of about thirty regulations as the *basis* of an organ's powers ("las
#: atribuciones que le confiere la Constitucion ..."), not as the law the
#: instrument develops (spot-check of `gold.md`, issue #272); it stays a valid
#: target of signal A, where the instrument's own name says so.
B_EXCLUDED_NAMES = ("constitucion politica de los estados unidos mexicanos",)

#: The unit types signal B reads: a preamble, a conclusion or a heading is never
#: the instrument's "objeto" article. `intro` is what `loose` text under a container
#: became with md2akn 0.5.1 (rule 12, issue #291): an "objeto" sentence that opens a
#: chapter is the same text as before, only labelled differently, so it is read as
#: before (issue #294; without it the gold lost 6 links to a relabelling).
SOURCE_UNIT_TYPES = ("article", "loose", "intro")

#: A name that refers to one of these ("de la Ley ...", "del Tratado ...") and
#: resolves to no instrument refers to one that is not in the corpus (abrogated):
#: `unresolved`. Before fix-1 the test was the bare word `ley` or `codigo` anywhere in
#: the name, which for a law (whose own name says `ley`) would flag every law.
INSTRUMENT_WORD = re.compile(
    r"\b(?:de la|del|de los|de las)\s+"
    r"(?:ley|leyes|codigo|reglamento|lineamientos?|tratado|convenio|convencion|protocolo)\b")

SENTENCE_BREAK = re.compile(r"(?<=\.)\s+")

#: Evidence kept per (source, law, signal), and the sentence's length in `gold.md`.
MAX_EVIDENCE = 3
MD_SENTENCE_CHARS = 300


#: Ordinal suffix with its full stop in folded text (`1o.`): the stop would end the
#: `[^.;]` gap of `CITATION`, so `find_citations` drops it first.
ORDINAL_STOP = re.compile(r"(?<=\d)o\.")

#: The cited article's number as written, with its `bis`/`ter`/... suffix.
ARTICLE_NUMBER = (r"\d+[a-z]?(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|"
                  r"decies))?")

#: Longest stretch between an article number and the law name (`CITATION`).
CITATION_REACH = 80


#: A treaty's name ends in what makes it one treaty among many with the same title
#: -- `(CANADA, 1991)`, `(MULTILATERAL, 1980)` -- or goes on after a comma with when and
#: where it was adopted. Nobody cites that: the text says "la Convención Americana sobre
#: Derechos Humanos". `treaty_alias` is the name without it.
TREATY_QUALIFIER = re.compile(r"\s*[\(\[][^()\[\]]*[\)\]]?\s*$")
ALIAS_MIN_WORDS = 5


def treaty_alias(nombre) -> str | None:
    """The short name a treaty is cited by -- `nombre` without its trailing
    `(party, year)` and without anything after the first comma -- or `None` when it
    is not shorter than the name or has fewer than `ALIAS_MIN_WORDS` words (`CONVENIO
    COMERCIAL`, `ACTAS` are titles of dozens of treaties and of nobody in particular)."""
    short = TREATY_QUALIFIER.sub("", str(nombre or "")).split(",")[0].strip()
    if not short or short == str(nombre).strip() or len(short.split()) < ALIAS_MIN_WORDS:
        return None
    return short


def fold(text) -> str:
    """`text` as the matching sees it: `name_key`'s fold with the Markdown
    emphasis marks (`*`, `\\`) a unit's text carries removed first."""
    return name_key(str(text or "").replace("*", "").replace("\\", ""))


class InstrumentMatcher:
    """The instruments of the Atlas as one compiled alternation of folded names.

    Longest name first, so at one position the longest name wins, and the scan is
    non-overlapping, so a name nested inside a longer matched one (`Constitución
    Política de los Estados Unidos Mexicanos` inside the name of the *Ley de Amparo*)
    is never reported. A folded name that two instruments share is `ambiguous`: it
    matches, but resolves to no instrument.

    `find(folded, self_i=i)` is the per-source view: a match whose name is the
    source's own (`i` among its instruments) is a **self-mention**, never reported,
    and the names nested inside it are looked for again without it -- so the name
    `reglamento de la ley federal del trabajo` found in the source of that very name
    still reports the `ley federal del trabajo` it contains, and a text that merely
    mentions its own title swallows nothing.
    """

    def __init__(self, instruments):
        """`instruments`: `(i, nombre)` of every instrument."""
        by_name: dict[str, list[int]] = {}
        for i, nombre in instruments:
            key = name_key(nombre)
            if key:
                by_name.setdefault(key, []).append(int(i))
        self.by_name = by_name
        names = sorted(by_name, key=lambda name: (-len(name), name))
        self._names = names
        self.pattern = (re.compile(r"(?<!\w)(?:" + _trie_alternation(names) + r")(?!\w)")
                        if names else None)
        self._nested_patterns: dict[tuple, re.Pattern | None] = {}

    @property
    def citation_pattern(self):
        """`articulo(s) N ... de la|del|de el <name>` over folded text, or `None`
        without instruments. Named groups: `article`, `gap`, `law` (the cited
        instrument's folded name)."""
        if self.pattern is None:
            return None
        if not hasattr(self, "_citation"):
            names = _trie_alternation(self._names)
            self._citation = re.compile(
                r"(?<!\w)articulos?\s+(?P<article>%s)(?P<gap>[^.;]{0,%d}?)\s+"
                r"(?:de la|del|de el)\s+(?P<law>%s)(?!\w)" % (ARTICLE_NUMBER, CITATION_REACH, names))
        return self._citation

    def names_in(self, folded: str) -> list[str]:
        """The folded names that occur in `folded`, longest first."""
        return [name for name in self._names if name in folded]

    def pattern_over(self, folded: str, banned=frozenset()):
        """The alternation over the names that occur in `folded` and are not in
        `banned`, or `None` when there are none."""
        names = [name for name in self.names_in(folded) if name not in banned]
        return (re.compile(r"(?<!\w)(?:" + _trie_alternation(names) + r")(?!\w)")
                if names else None)

    def _nested(self, name: str, self_i: int):
        """The pattern over the names that are proper substrings of `name` and are
        not the source's own, or `None` when there are none."""
        key = (name, self_i)
        if key not in self._nested_patterns:
            inside = [other for other in self._names
                      if other != name and other in name and self_i not in self.by_name[other]]
            self._nested_patterns[key] = (re.compile(
                r"(?<!\w)(?:" + _trie_alternation(inside) + r")(?!\w)") if inside else None)
        return self._nested_patterns[key]

    def find_spans(self, folded: str, self_i: int | None = None):
        """`(folded name, [instrument i], start)` of every instrument named in
        `folded` (already folded), in order of appearance; `[instrument i]` has
        several entries when the name is ambiguous. With `self_i`, a self-mention is
        skipped (see the class)."""
        if self.pattern is None:
            return []
        found = []
        for match in self.pattern.finditer(folded):
            name = match.group(0)
            candidates = self.by_name[name]
            if self_i is not None and self_i in candidates:
                inner = self._nested(name, self_i)
                if inner is not None:
                    found += [(m.group(0), self.by_name[m.group(0)], match.start() + m.start())
                              for m in inner.finditer(name)]
                continue
            found.append((name, candidates, match.start()))
        return found

    def find(self, folded: str, self_i: int | None = None) -> list[tuple[str, list[int]]]:
        """`find_spans` without the positions."""
        return [(name, candidates) for name, candidates, _ in self.find_spans(folded, self_i)]


#: The name `LawMatcher` had when only laws were targets (before issue #283's fix-1).
LawMatcher = InstrumentMatcher


def _trie_alternation(names) -> str:
    """One regex alternation matching exactly the `names`, longest match first at
    each position, with the common prefixes factored out (a trie): Python's `re`
    tries an alternation branch by branch, which at 2,676 names is the difference
    between seconds and minutes over 200,000 unit rows."""
    trie: dict = {}
    for name in names:
        node = trie
        for char in name:
            node = node.setdefault(char, {})
        node[""] = True

    def render(node: dict) -> str:
        branches = []
        for char, child in sorted(node.items()):
            if char == "":
                continue
            literal = char
            # A chain of single-child, non-terminal nodes is one literal: the nesting
            # depth is then the number of branch points, not the name's length (which
            # overflows the regex parser's recursion limit).
            while len(child) == 1 and "" not in child:
                (next_char, child), = child.items()
                literal += next_char
            branches.append(re.escape(literal) + render(child))
        if not branches:
            return ""
        body = "(?:" + "|".join(branches) + ")"
        # A name that ends here is optional: the greedy group prefers going deeper.
        return body + "?" if "" in node else body

    return "(?:" + render(trie) + ")" if names else "(?!)"


def signal_a(matcher: InstrumentMatcher, nombre: str, self_i: int | None = None) -> dict:
    """Signal A for one instrument's name: `{"targets": [...], "ambiguous": [...],
    "unresolved": bool}`, never naming `self_i` itself. `unresolved` is true when
    the name refers to an instrument (`INSTRUMENT_WORD`) and none resolved from it.

    The alternation is rebuilt per source, over the names that occur in its name, and
    a name is **banned** -- looked for again without it -- when it is

    * a name the source itself carries (it is in the alternation, longest first, and
      would swallow everything inside it). A name the source shares with another
      instrument -- two laws called `LEY de Amnistía`, the Senate's regulation filed by
      the SCJN both as a law and as a reglamento -- is the source's own name too: the
      other is a twin, the same instrument as far as a reader is concerned, and a link
      to it would be a link to itself, or
    * a name at **position 0** that is not the whole name: a title that *begins* with
      another instrument's title is a sibling or an extension of it, not a reference
      to it (`REGLAMENTO DE LA LEY DE NAVEGACION Y COMERCIO MARITIMOS` begins with the
      older `REGLAMENTO DE LA LEY DE NAVEGACION`, a double-taxation convention "... SOBRE
      LA RENTA Y EL PATRIMONIO" begins with every "... SOBRE LA RENTA"). A name in the
      middle ("... EN RELACION CON EL TRATADO SOBRE LA NO PROLIFERACION ...", "LEY que
      aprueba ... el Convenio Constitutivo del Banco ...") is a reference.
    """
    folded = fold(nombre)
    banned = {name for name in matcher.names_in(folded)
              if self_i is not None and self_i in matcher.by_name[name]}
    while True:
        pattern = matcher.pattern_over(folded, banned)
        matches = list(pattern.finditer(folded)) if pattern is not None else []
        if matches and matches[0].start() == 0 and matches[0].group(0) != folded:
            banned.add(matches[0].group(0))
            continue
        break
    targets: list[int] = []
    ambiguous: list[dict] = []
    for match in matches:
        name = match.group(0)
        candidates = [c for c in matcher.by_name[name] if c != self_i]
        if len(candidates) == 1:
            if candidates[0] not in targets:
                targets.append(candidates[0])
        elif candidates:
            ambiguous.append({"name": name, "targets": candidates})
    unresolved = not targets and not ambiguous and bool(INSTRUMENT_WORD.search(folded))
    return {"targets": targets, "ambiguous": ambiguous, "unresolved": unresolved}


def find_citations(matcher: InstrumentMatcher, text, self_i: int | None = None):
    """Signal C over one unit's text: `([{"target": i, "article", "span"}], self_hits)`,
    one per citation of an instrument the matcher resolves, in order of
    appearance, and how many citations were of `self_i` itself (dropped). A gap
    that says `reglamento` is a citation of the regulation, not of the instrument
    behind it, and a name two instruments share resolves to none."""
    pattern = matcher.citation_pattern
    if pattern is None:
        return [], 0
    folded = ORDINAL_STOP.sub("o", fold(text))
    found = []
    self_hits = 0
    for match in pattern.finditer(folded):
        candidates = matcher.by_name[match.group("law")]
        if self_i is not None and self_i in candidates:
            self_hits += 1
            continue
        if len(candidates) != 1 or "reglamento" in match.group("gap"):
            continue
        found.append({"target": candidates[0], "article": match.group("article"),
                      "span": match.group(0)})
    return found, self_hits


def sentences_of(text: str) -> list[str]:
    return [piece for piece in SENTENCE_BREAK.split(str(text or "")) if piece.strip()]


def signal_b(matcher: InstrumentMatcher, units, skip=frozenset(),
             self_i: int | None = None) -> dict[int, list[dict]]:
    """Signal B over one instrument's unit rows: `{target i: [{"eId", "sentence"}]}`.

    `units` yields `(eId, text)` of the unit rows that are neither a heading nor
    in a transitorios section. The targets of a unit are the instruments named in a
    sentence that itself matches `OBJECTO_PHRASE`; a name two instruments share
    gives none, and neither does a target in `skip` or the source (`self_i`) itself.
    At most `MAX_EVIDENCE` distinct sentences are kept per target.
    """
    found: dict[int, list[dict]] = {}
    for e_id, text in units:
        if "objeto" not in str(text or "").lower():
            continue
        for sentence in sentences_of(text):
            folded = fold(sentence)
            if not OBJECTO_PHRASE.search(folded):
                continue
            for _, candidates in matcher.find(folded, self_i):
                if len(candidates) != 1 or candidates[0] in skip:
                    continue
                evidence = found.setdefault(candidates[0], [])
                if len(evidence) < MAX_EVIDENCE and all(e["sentence"] != sentence.strip()
                                                       for e in evidence):
                    evidence.append({"eId": None if e_id is None else str(e_id),
                                     "sentence": sentence.strip()})
    return found


def commit_of() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parent, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def manifest_hashes(cache_dir, collections) -> dict:
    """SHA-256 of each cached `corpus-manifest.json` (null where absent), so a
    gold file names the corpora it was read from."""
    import legalvec

    hashes = {}
    for coleccion in collections:
        try:
            path = Path(legalvec.cache.CACHE_DIR if cache_dir is None else cache_dir) \
                / f"scjn-{coleccion}-vectors" / "corpus-manifest.json"
        except AttributeError:
            hashes[coleccion] = None
            continue
        hashes[coleccion] = (hashlib.sha256(path.read_bytes()).hexdigest()
                             if path.is_file() else None)
    return hashes


def source_units(coleccion: str, keys: set, cache_dir) -> dict[str, list[tuple]]:
    """`{clave: [(eId, unit_type, text, text_sha1), ...]}` of the unit rows that
    are neither a heading nor in a transitorios section (`instrument_matrix`'s own
    rule) of the Atlas instruments `keys` of one collection, read from
    `legalvec`'s cached `units.parquet`."""
    import legalvec

    table = legalvec.load_units(coleccion, cache_dir=cache_dir)
    columns = ["clave", "unit_type", "eId", "text", "text_sha1"]
    if "path" in table.column_names:
        columns.append("path")
    frame = table.select(columns).to_pandas()
    frame = frame[frame["clave"].astype(str).isin(keys)
                  & ~frame["unit_type"].isin(EXCLUDED_UNIT_TYPES)]
    if "path" in frame.columns:
        frame = frame[[not is_transitorio_path(path) for path in frame["path"]]]
    units: dict[str, list[tuple]] = {}
    for clave, e_id, unit_type, text, sha1 in zip(frame["clave"].astype(str), frame["eId"],
                                                  frame["unit_type"], frame["text"],
                                                  frame["text_sha1"]):
        units.setdefault(clave, []).append((e_id, unit_type, text, sha1))
    return units


#: Cheap test before folding a text for signal C: a citation is "artículo ..." and
#: "culo" is all that survives the accent and the plural.
CITATION_WORD = "culo"


def citations_of(matcher: InstrumentMatcher, row: dict, units) -> tuple[list[dict], int, int]:
    """Signal C over one instrument's unit rows `(eId, unit_type, text, text_sha1)`:
    `(rows, hits, self_hits)`, one row per `(text_sha1, cited instrument)` (the first
    citation is the evidence, `hits` counts the row's citations of that instrument),
    the number of citations found before that grouping, and how many of them were of
    the instrument itself (dropped: an instrument never cites itself)."""
    grouped: dict[tuple, dict] = {}
    hits = 0
    self_hits = 0
    for e_id, unit_type, text, sha1 in units:
        lowered = str(text or "")
        if CITATION_WORD not in lowered.lower():
            continue
        found_all, mine = find_citations(matcher, lowered, int(row["i"]))
        self_hits += mine
        for found in found_all:
            hits += 1
            key = (sha1, found["target"])
            if key in grouped:
                grouped[key]["hits"] += 1
                continue
            grouped[key] = {"i": int(row["i"]), "coleccion": row["coleccion"],
                            "clave": row["clave"], "text_sha1": sha1,
                            "eId": None if e_id is None else str(e_id), "unit_type": unit_type,
                            "article": found["article"], "target": int(found["target"]),
                            "span": found["span"], "hits": 1}
    rows = list(grouped.values())
    targets_per_text: dict[str, set] = {}
    for item in rows:
        targets_per_text.setdefault(item["text_sha1"], set()).add(item["target"])
    for item in rows:
        item["single_target"] = len(targets_per_text[item["text_sha1"]]) == 1
    return rows, hits, self_hits


def citation_summary(rows: list[dict], hits: int, self_hits: int = 0) -> dict:
    """The counts the README records for signal C."""
    texts = {(r["i"], r["text_sha1"]) for r in rows}
    return {"hits": hits, "self_citations": self_hits, "distinct_rows": len(texts),
            "instruments": len({r["i"] for r in rows}),
            "targets": len({r["target"] for r in rows}),
            "single_target_rows": len({(r["i"], r["text_sha1"]) for r in rows
                                       if r["single_target"]}),
            "links": len(rows)}


def build(work_dir: Path, *, cache_dir=None, log=print) -> dict:
    """The gold record (what `gold.json` holds) of the Atlas in `work_dir`."""
    import pyarrow.parquet as pq

    path = Path(work_dir) / "instruments.parquet"
    if not path.is_file():
        raise SystemExit(f"{path} does not exist: prepare the Atlas work directory first "
                         "(this script never builds it)")
    instruments = pq.read_table(path).to_pandas().sort_values("i").reset_index(drop=True)
    instruments["i"] = instruments["i"].astype(int)
    instruments["clave"] = instruments["clave"].astype(str)
    unknown = sorted(set(instruments["coleccion"]) - set(COLLECTIONS))
    if unknown:
        raise SystemExit(f"{path} holds collection(s) {unknown} this script does not know")

    names = list(zip(instruments["i"], instruments["nombre"]))
    aliases = [(i, alias) for i, nombre, coleccion
               in zip(instruments["i"], instruments["nombre"], instruments["coleccion"])
               if coleccion == "tratados" and (alias := treaty_alias(nombre))]
    matcher = InstrumentMatcher(names + aliases)
    log(f"{len(instruments)} instruments, {len(aliases)} treaty aliases, "
        f"{len(matcher.by_name)} distinct folded names")

    b_skip = frozenset(i for name in B_EXCLUDED_NAMES for i in matcher.by_name.get(name, ()))
    entries: dict[int, dict] = {}
    unresolved: list[dict] = []
    ambiguous: list[dict] = []
    citation_rows: list[dict] = []
    citation_hits = 0
    self_citations = 0

    for _, row in instruments.iterrows():
        a = signal_a(matcher, row["nombre"], int(row["i"]))
        for item in a["ambiguous"]:
            ambiguous.append({"i": int(row["i"]), "coleccion": row["coleccion"],
                              "clave": row["clave"], "nombre": row["nombre"], **item})
        if a["unresolved"]:
            unresolved.append({"i": int(row["i"]), "coleccion": row["coleccion"],
                               "clave": row["clave"], "nombre": row["nombre"]})
        if a["targets"]:
            entries[int(row["i"])] = {
                "i": int(row["i"]), "coleccion": row["coleccion"], "clave": row["clave"],
                "nombre": row["nombre"], "targets_A": list(a["targets"]), "targets_B": [],
                "evidence": [{"target": target, "signal": "A"} for target in a["targets"]]}
    log(f"signal A: {len(entries)} instruments, {len(unresolved)} unresolved names")

    for coleccion in COLLECTIONS:
        wanted = instruments[instruments["coleccion"] == coleccion]
        by_clave = {row["clave"]: row for _, row in wanted.iterrows()}
        units = source_units(coleccion, set(by_clave), cache_dir)
        log(f"{coleccion}: {sum(len(v) for v in units.values())} searchable unit rows, "
            f"{len(by_clave)} instruments")
        for clave, rows in units.items():
            row = by_clave[clave]
            cited, found_hits, mine = citations_of(matcher, row, rows)
            citation_rows += cited
            citation_hits += found_hits
            self_citations += mine
            found = signal_b(matcher, [(e, t) for e, u, t, _ in rows if u in SOURCE_UNIT_TYPES],
                             skip=b_skip, self_i=int(row["i"]))
            if not found:
                continue
            entry = entries.setdefault(int(row["i"]), {
                "i": int(row["i"]), "coleccion": row["coleccion"], "clave": row["clave"],
                "nombre": row["nombre"], "targets_A": [], "targets_B": [], "evidence": []})
            for target, evidence in sorted(found.items()):
                entry["targets_B"].append(target)
                entry["evidence"] += [{"target": target, "signal": "B", **e} for e in evidence]

    gold = []
    for i in sorted(entries):
        entry = entries[i]
        entry["targets"] = sorted(set(entry["targets_A"]) | set(entry["targets_B"]))
        entry["signals"] = [s for s, key in (("A", "targets_A"), ("B", "targets_B")) if entry[key]]
        gold.append(entry)

    collection_of = {int(i): c for i, c in zip(instruments["i"], instruments["coleccion"])}
    summary = summarise(gold, unresolved, ambiguous, instruments, collection_of)
    summary["citations"] = citation_summary(citation_rows, citation_hits, self_citations)
    summary["citation_pairs"] = pair_counts(
        [(r["coleccion"], collection_of[r["target"]], r["i"]) for r in citation_rows])
    log(f"signal C: {summary['citations']}")
    return {
        "summary": summary,
        "citations": citation_rows,
        "provenance": {
            "commit": commit_of(),
            "instruments": int(len(instruments)),
            "corpus_manifest_sha256": manifest_hashes(cache_dir, COLLECTIONS),
            "objeto_phrase": OBJECTO_PHRASE.pattern,
        },
        "instruments": [[row["coleccion"], row["clave"]] for _, row in instruments.iterrows()],
        "target_names": {str(int(i)): nombre
                         for i, nombre in zip(instruments["i"], instruments["nombre"])},
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "entries": gold,
    }


def pair_counts(links) -> dict:
    """`{"<source collection> -> <target collection>": {"links", "instruments"}}`
    over `(source collection, target collection, source i)` triples, one per link,
    in the order of `COLLECTIONS` (a pair with no link is absent)."""
    links = list(links)
    out = {}
    for source in COLLECTIONS:
        for target in COLLECTIONS:
            chosen = [i for s, t, i in links if s == source and t == target]
            if chosen:
                out[f"{source} -> {target}"] = {"links": len(chosen),
                                                "instruments": len(set(chosen))}
    return out


def summarise(gold, unresolved, ambiguous, instruments, collection_of) -> dict:
    """The counts the README records: instruments per signal and collection, links,
    the source-collection to target-collection table of the links, the unresolved
    and ambiguous names."""
    summary = {"source_instruments": dict(Counter(instruments["coleccion"])),
               "unresolved": len(unresolved), "ambiguous": len(ambiguous)}
    for name, predicate in (
            ("A", lambda e: bool(e["targets_A"])),
            ("B", lambda e: bool(e["targets_B"])),
            ("A_or_B", lambda e: True),
            ("A_and_B", lambda e: bool(e["targets_A"]) and bool(e["targets_B"])),
            ("B_only", lambda e: not e["targets_A"]),
            ("B_naming_several_targets", lambda e: len(e["targets_B"]) > 1)):
        chosen = [e for e in gold if predicate(e)]
        summary[name] = {"instruments": len(chosen),
                         "by_collection": dict(Counter(e["coleccion"] for e in chosen))}
    summary["links"] = {
        "A": sum(len(e["targets_A"]) for e in gold),
        "B": sum(len(e["targets_B"]) for e in gold),
        "A_or_B": sum(len(e["targets"]) for e in gold),
        "B_only": sum(len(set(e["targets_B"]) - set(e["targets_A"])) for e in gold),
    }
    summary["pairs"] = pair_counts(
        (e["coleccion"], collection_of[t], e["i"]) for e in gold for t in e["targets"])
    summary["self_links"] = sum(1 for e in gold if e["i"] in e["targets"])
    summary["B_links_without_evidence"] = sum(
        1 for e in gold for target in e["targets_B"]
        if not any(x["target"] == target and x["signal"] == "B" and x.get("eId") and x.get("sentence")
                   for x in e["evidence"]))
    return summary


def render_markdown(record: dict) -> str:
    """`gold.md`: the B-only links, one row each, to be checked by eye."""
    names = {int(i): nombre for i, nombre in record["target_names"].items()}
    rows = []
    for entry in record["entries"]:
        for target in entry["targets_B"]:
            if target in entry["targets_A"]:
                continue
            evidence = next(x for x in entry["evidence"]
                            if x["target"] == target and x["signal"] == "B")
            sentence = evidence["sentence"].replace("|", "\\|").replace("\n", " ")
            if len(sentence) > MD_SENTENCE_CHARS:
                sentence = sentence[:MD_SENTENCE_CHARS] + "..."
            rows.append((entry["coleccion"], entry["clave"], entry["nombre"],
                         names.get(target, str(target)), evidence["eId"], sentence))
    lines = ["# B-only links (signal B without signal A)", "",
             f"{len(rows)} links, to be spot-checked by eye.", "",
             "| collection | key | instrument | target | eId | sentence |",
             "|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def link_signal(entry: dict, target: int) -> str:
    """Which signal found the link `entry` -> `target`: `A`, `B` or `A and B`."""
    return " and ".join(name for name, targets in (("A", entry["targets_A"]),
                                                   ("B", entry["targets_B"]))
                        if target in targets)


def render_links(record: dict) -> str:
    """`links.md`: every instrument -> instrument link of the gold, one row each,
    sorted by source name then target name, with the signal that found it. A pipe
    table with no heading, meant to be included in a page."""
    names = {int(i): nombre for i, nombre in record["target_names"].items()}
    rows = sorted(((entry["nombre"], names.get(target, str(target)), link_signal(entry, target))
                   for entry in record["entries"] for target in entry["targets"]),
                  key=lambda row: (row[0].casefold(), row[1].casefold(), row[2]))
    lines = ["<!-- The strong links of the Atlas instruments (signals A and B), one row per "
             "link. A generated file: regenerate it, do not edit it. -->",
             "", "| Instrument | Links to | Signal |", "|---|---|---|"]
    lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def write(work_dir: Path, record: dict) -> Path:
    out = Path(work_dir) / SUBDIR
    gold = {key: value for key, value in record.items() if key != "citations"}
    atomic_write_text(out / GOLD_JSON, json.dumps(gold, ensure_ascii=False, indent=1) + "\n")
    atomic_write_text(out / GOLD_MD, render_markdown(record))
    atomic_write_text(out / LINKS_MD, render_links(record))
    atomic_write_table(out / CITATIONS, citations_table(record["citations"]))
    return out


def citations_table(rows: list[dict]):
    """`citations.parquet`'s table: one row per `(i, text_sha1, target)`."""
    import pyarrow as pa

    types = {"i": pa.int32(), "coleccion": pa.string(), "clave": pa.string(),
             "text_sha1": pa.string(), "eId": pa.string(), "unit_type": pa.string(),
             "article": pa.string(), "target": pa.int32(), "span": pa.string(),
             "hits": pa.int32(), "single_target": pa.bool_()}
    return pa.table({name: pa.array([r[name] for r in rows], type=kind)
                     for name, kind in types.items()})


def report(work_dir: Path, log=print) -> dict:
    path = Path(work_dir) / SUBDIR / GOLD_JSON
    if not path.is_file():
        raise SystemExit(f"{path} does not exist: run gold_links.py first")
    record = json.loads(path.read_text(encoding="utf-8"))
    summary = record["summary"]
    log(f"gold links over {record['provenance']['instruments']} Atlas instruments "
        f"(commit {record['provenance']['commit']})")
    log(f"source instruments: {summary['source_instruments']}")
    for name in ("A", "B", "A_or_B", "A_and_B", "B_only", "B_naming_several_targets"):
        log(f"  {name:<24}{summary[name]['instruments']:>5}  {summary[name]['by_collection']}")
    log(f"links: {summary['links']}")
    log("links by source collection -> target collection (links, source instruments):")
    for pair, counts in summary["pairs"].items():
        log(f"  {pair:<30}{counts['links']:>6}{counts['instruments']:>6}")
    log(f"self links (must be 0): {summary['self_links']}")
    log(f"signal C (citations): {summary['citations']}")
    log("citation links by source -> target collection (links, source instruments):")
    for pair, counts in summary["citation_pairs"].items():
        log(f"  {pair:<30}{counts['links']:>6}{counts['instruments']:>6}")
    log(f"unresolved names (a referred instrument not on the map): {summary['unresolved']}; "
        f"ambiguous: {summary['ambiguous']}; "
        f"B links without evidence: {summary['B_links_without_evidence']}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR,
                        help="the Atlas work directory (default: emb-run-atlas)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="legalvec's cache (default: legalvec.cache.CACHE_DIR)")
    parser.add_argument("--report", action="store_true",
                        help="print the summary from the written files and exit")
    args = parser.parse_args(argv)
    if args.report:
        report(args.work_dir)
        return 0
    record = build(args.work_dir, cache_dir=args.cache_dir)
    out = write(args.work_dir, record)
    print(f"wrote {out / GOLD_JSON}, {out / GOLD_MD} and {out / LINKS_MD}")
    report(args.work_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
