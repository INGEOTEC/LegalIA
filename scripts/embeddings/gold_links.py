"""The gold set of strong reglamento/lineamiento -> law links (issue #272).

The Atlas has three data sets (Qwen3-Embedding-0.6B, 4B and a BM25 baseline) built
over exactly the same comparison, and the only thing ever measured between them
was *agreement*, which cannot say which is right. A federal *reglamento* (and a
few *lineamientos*) exists to develop one specific law and says so itself, which
is a gold standard external to all three. Two signals name that law, both
offline, over the unique instruments of the Atlas (`<work-dir>/instruments.parquet`):

* **A -- the instrument's name.** `REGLAMENTO DE LA LEY FEDERAL DEL TRABAJO`
  contains the name of one of the laws. Names are folded (NFKD, accents, case and
  whitespace -- `unique_instruments.name_key`, punctuation kept); the folded law
  names form one alternation, longest first, so a match nested inside a longer
  matched law name is dropped. A folded name two laws share (`LEY de Amnistía`) is
  `ambiguous` and gives no link. A name that says `ley` or `codigo` and names no
  resolved law is recorded as `unresolved` (the abrogated laws: *Ley Federal de
  Turismo*, ...) -- counted and reported, never gold and never a negative.
* **B -- the instrument's own "objeto" article.** "El presente Reglamento tiene
  por objeto reglamentar la Ley ...". Over the instrument's `article`
  and `loose` unit rows that are not in a transitorios section
  (`instrument_matrix.is_transitorio_path`), the sentences (split on `.`
  followed by whitespace) whose folded text matches `OBJECTO_PHRASE`; the gold laws
  are the ones named **in that same sentence**, not anywhere in the unit, which
  keeps "sin perjuicio de lo dispuesto en la Ley X" clauses elsewhere in the
  article out. The evidence is the unit's `eId` and the sentence.

Only `reglamentos` and `lineamientos` are sources and only `leyes` are targets:
`A` is directed by design and the reverse (law -> reglamento) is not a strong
link. The gold of an instrument is A union B; `laws_A` keeps A alone, which
`evaluate_links.py` reports as the high-precision control.

    python gold_links.py                      # writes <work-dir>/gold-links/
    python gold_links.py --report             # the summary, offline

Outputs, under `<work-dir>/gold-links/` (gitignored with the rest of `emb-run-*`):
`gold.json` and `gold.md` (a readable table of the B-only links, the ones a human
may want to spot-check). The gold is model-independent, so it lives in the 0.6B's
directory by convention. Nothing here needs the network or Slurm.
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

from _atomic import atomic_write_text  # noqa: E402
from instrument_matrix import EXCLUDED_UNIT_TYPES, is_transitorio_path  # noqa: E402
from unique_instruments import name_key  # noqa: E402

DEFAULT_WORK_DIR = Path("emb-run-atlas")
SUBDIR = "gold-links"
GOLD_JSON = "gold.json"
GOLD_MD = "gold.md"

TARGET_COLLECTION = "leyes"
SOURCE_COLLECTIONS = ("reglamentos", "lineamientos")

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
#: `article` and `loose` units are read (`SOURCE_UNIT_TYPES`).
SUBJECT_REACH = 120
OBJECTO_PHRASE = re.compile(
    r"\b(?:presentes?|este|esta|estos|estas)\s+"
    r"(?:reglamento|ordenamiento|lineamientos?|instrumento|disposiciones|reglas|manual|"
    r"acuerdo|decreto|estatuto)\b"
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
#: the instrument's "objeto" article.
SOURCE_UNIT_TYPES = ("article", "loose")

#: A name that says one of these and resolves to no law is a law that is not in
#: the corpus (abrogated): `unresolved`.
LAW_WORD = re.compile(r"\b(?:ley|codigo)\b")

SENTENCE_BREAK = re.compile(r"(?<=\.)\s+")

#: Evidence kept per (source, law, signal), and the sentence's length in `gold.md`.
MAX_EVIDENCE = 3
MD_SENTENCE_CHARS = 300


def fold(text) -> str:
    """`text` as the matching sees it: `name_key`'s fold with the Markdown
    emphasis marks (`*`, `\\`) a unit's text carries removed first."""
    return name_key(str(text or "").replace("*", "").replace("\\", ""))


class LawMatcher:
    """The laws of the Atlas as one compiled alternation of folded names.

    Longest name first, so at one position the longest law name wins, and the
    scan is non-overlapping, so a law name nested inside a longer matched one
    (`Constitución Política de los Estados Unidos Mexicanos` inside the name of
    the *Ley de Amparo*) is never reported. A folded name that two laws share is
    `ambiguous`: it matches, but resolves to no law.
    """

    def __init__(self, laws):
        """`laws`: `(i, nombre)` of every law."""
        by_name: dict[str, list[int]] = {}
        for i, nombre in laws:
            key = name_key(nombre)
            if key:
                by_name.setdefault(key, []).append(int(i))
        self.by_name = by_name
        names = sorted(by_name, key=lambda name: (-len(name), name))
        self.pattern = (re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)")
                        if names else None)

    def find(self, folded: str) -> list[tuple[str, list[int]]]:
        """`(folded name, [law i])` of every law named in `folded` (already
        folded), in order of appearance; `[law i]` has several entries when
        the name is ambiguous."""
        if self.pattern is None:
            return []
        return [(m.group(0), self.by_name[m.group(0)]) for m in self.pattern.finditer(folded)]


def signal_a(matcher: LawMatcher, nombre: str) -> dict:
    """Signal A for one instrument's name: `{"laws": [...], "ambiguous": [...],
    "unresolved": bool}`. `unresolved` is true when the name says `ley` or
    `codigo` and no law resolved from it."""
    folded = fold(nombre)
    laws: list[int] = []
    ambiguous: list[dict] = []
    for name, candidates in matcher.find(folded):
        if len(candidates) == 1:
            if candidates[0] not in laws:
                laws.append(candidates[0])
        else:
            ambiguous.append({"name": name, "laws": candidates})
    unresolved = not laws and not ambiguous and bool(LAW_WORD.search(folded))
    return {"laws": laws, "ambiguous": ambiguous, "unresolved": unresolved}


def sentences_of(text: str) -> list[str]:
    return [piece for piece in SENTENCE_BREAK.split(str(text or "")) if piece.strip()]


def signal_b(matcher: LawMatcher, units, skip=frozenset()) -> dict[int, list[dict]]:
    """Signal B over one instrument's unit rows: `{law i: [{"eId", "sentence"}]}`.

    `units` yields `(eId, text)` of the unit rows that are neither a heading nor
    in a transitorios section. The laws of a unit are the ones named in a
    sentence that itself matches `OBJECTO_PHRASE`; a name two laws share gives
    none, and neither does a law in `skip`. At most `MAX_EVIDENCE` distinct
    sentences are kept per law.
    """
    found: dict[int, list[dict]] = {}
    for e_id, text in units:
        if "objeto" not in str(text or "").lower():
            continue
        for sentence in sentences_of(text):
            folded = fold(sentence)
            if not OBJECTO_PHRASE.search(folded):
                continue
            for _, candidates in matcher.find(folded):
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
    """`{clave: [(eId, text), ...]}` of the `SOURCE_UNIT_TYPES` unit rows not in
    a transitorios section of the Atlas instruments `keys` of one
    collection, read from `legalvec`'s cached `units.parquet`."""
    import legalvec

    table = legalvec.load_units(coleccion, cache_dir=cache_dir)
    columns = ["clave", "unit_type", "eId", "text"]
    if "path" in table.column_names:
        columns.append("path")
    frame = table.select(columns).to_pandas()
    frame = frame[frame["clave"].astype(str).isin(keys)
                  & frame["unit_type"].isin(SOURCE_UNIT_TYPES)
                  & ~frame["unit_type"].isin(EXCLUDED_UNIT_TYPES)]
    if "path" in frame.columns:
        frame = frame[[not is_transitorio_path(path) for path in frame["path"]]]
    units: dict[str, list[tuple]] = {}
    for clave, e_id, text in zip(frame["clave"].astype(str), frame["eId"], frame["text"]):
        units.setdefault(clave, []).append((e_id, text))
    return units


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

    laws = instruments[instruments["coleccion"] == TARGET_COLLECTION]
    matcher = LawMatcher(zip(laws["i"], laws["nombre"]))
    log(f"{len(laws)} laws, {len(matcher.by_name)} distinct folded names")

    sources = instruments[instruments["coleccion"].isin(SOURCE_COLLECTIONS)]
    b_skip = frozenset(i for name in B_EXCLUDED_NAMES for i in matcher.by_name.get(name, ()))
    entries: dict[int, dict] = {}
    unresolved: list[dict] = []
    ambiguous: list[dict] = []

    for _, row in sources.iterrows():
        a = signal_a(matcher, row["nombre"])
        for item in a["ambiguous"]:
            ambiguous.append({"i": int(row["i"]), "coleccion": row["coleccion"],
                              "clave": row["clave"], "nombre": row["nombre"], **item})
        if a["unresolved"]:
            unresolved.append({"i": int(row["i"]), "coleccion": row["coleccion"],
                               "clave": row["clave"], "nombre": row["nombre"]})
        if a["laws"]:
            entries[int(row["i"])] = {
                "i": int(row["i"]), "coleccion": row["coleccion"], "clave": row["clave"],
                "nombre": row["nombre"], "laws_A": list(a["laws"]), "laws_B": [], "evidence": [
                    {"law": law, "signal": "A"} for law in a["laws"]]}
    log(f"signal A: {len(entries)} instruments, {len(unresolved)} unresolved names")

    for coleccion in SOURCE_COLLECTIONS:
        wanted = sources[sources["coleccion"] == coleccion]
        by_clave = {row["clave"]: row for _, row in wanted.iterrows()}
        units = source_units(coleccion, set(by_clave), cache_dir)
        log(f"{coleccion}: {sum(len(v) for v in units.values())} searchable unit rows, "
            f"{len(by_clave)} instruments")
        for clave, rows in units.items():
            found = signal_b(matcher, rows, skip=b_skip)
            if not found:
                continue
            row = by_clave[clave]
            entry = entries.setdefault(int(row["i"]), {
                "i": int(row["i"]), "coleccion": row["coleccion"], "clave": row["clave"],
                "nombre": row["nombre"], "laws_A": [], "laws_B": [], "evidence": []})
            for law, evidence in sorted(found.items()):
                entry["laws_B"].append(law)
                entry["evidence"] += [{"law": law, "signal": "B", **e} for e in evidence]

    gold = []
    for i in sorted(entries):
        entry = entries[i]
        entry["laws"] = sorted(set(entry["laws_A"]) | set(entry["laws_B"]))
        entry["signals"] = [s for s, key in (("A", "laws_A"), ("B", "laws_B")) if entry[key]]
        gold.append(entry)

    return {
        "summary": summarise(gold, unresolved, ambiguous, sources),
        "provenance": {
            "commit": commit_of(),
            "instruments": int(len(instruments)),
            "corpus_manifest_sha256": manifest_hashes(cache_dir, ("leyes",) + SOURCE_COLLECTIONS),
            "objeto_phrase": OBJECTO_PHRASE.pattern,
        },
        "instruments": [[row["coleccion"], row["clave"]] for _, row in instruments.iterrows()],
        "law_names": {str(int(i)): nombre for i, nombre in zip(laws["i"], laws["nombre"])},
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "entries": gold,
    }


def summarise(gold, unresolved, ambiguous, sources) -> dict:
    """The counts the README records: instruments per signal and collection,
    links, the unresolved and ambiguous names."""
    summary = {"source_instruments": Counter(sources["coleccion"]),
               "unresolved": len(unresolved), "ambiguous": len(ambiguous)}
    summary["source_instruments"] = dict(summary["source_instruments"])
    for name, predicate in (
            ("A", lambda e: bool(e["laws_A"])),
            ("B", lambda e: bool(e["laws_B"])),
            ("A_or_B", lambda e: True),
            ("A_and_B", lambda e: bool(e["laws_A"]) and bool(e["laws_B"])),
            ("B_only", lambda e: not e["laws_A"]),
            ("B_naming_several_laws", lambda e: len(e["laws_B"]) > 1)):
        chosen = [e for e in gold if predicate(e)]
        summary[name] = {"instruments": len(chosen),
                         "by_collection": dict(Counter(e["coleccion"] for e in chosen))}
    summary["links"] = {
        "A": sum(len(e["laws_A"]) for e in gold),
        "B": sum(len(e["laws_B"]) for e in gold),
        "A_or_B": sum(len(e["laws"]) for e in gold),
        "B_only": sum(len(set(e["laws_B"]) - set(e["laws_A"])) for e in gold),
    }
    summary["B_links_without_evidence"] = sum(
        1 for e in gold for law in e["laws_B"]
        if not any(x["law"] == law and x["signal"] == "B" and x.get("eId") and x.get("sentence")
                   for x in e["evidence"]))
    return summary


def render_markdown(record: dict) -> str:
    """`gold.md`: the B-only links, one row each, to be checked by eye."""
    names = {int(i): nombre for i, nombre in record["law_names"].items()}
    rows = []
    for entry in record["entries"]:
        for law in entry["laws_B"]:
            if law in entry["laws_A"]:
                continue
            evidence = next(x for x in entry["evidence"] if x["law"] == law and x["signal"] == "B")
            sentence = evidence["sentence"].replace("|", "\\|").replace("\n", " ")
            if len(sentence) > MD_SENTENCE_CHARS:
                sentence = sentence[:MD_SENTENCE_CHARS] + "..."
            rows.append((entry["coleccion"], entry["clave"], entry["nombre"],
                         names.get(law, str(law)), evidence["eId"], sentence))
    lines = ["# B-only links (signal B without signal A)", "",
             f"{len(rows)} links, to be spot-checked by eye.", "",
             "| collection | key | instrument | law | eId | sentence |",
             "|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def write(work_dir: Path, record: dict) -> Path:
    out = Path(work_dir) / SUBDIR
    atomic_write_text(out / GOLD_JSON, json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    atomic_write_text(out / GOLD_MD, render_markdown(record))
    return out


def report(work_dir: Path, log=print) -> dict:
    path = Path(work_dir) / SUBDIR / GOLD_JSON
    if not path.is_file():
        raise SystemExit(f"{path} does not exist: run gold_links.py first")
    record = json.loads(path.read_text(encoding="utf-8"))
    summary = record["summary"]
    log(f"gold links over {record['provenance']['instruments']} Atlas instruments "
        f"(commit {record['provenance']['commit']})")
    log(f"source instruments: {summary['source_instruments']}")
    for name in ("A", "B", "A_or_B", "A_and_B", "B_only", "B_naming_several_laws"):
        log(f"  {name:<22}{summary[name]['instruments']:>5}  {summary[name]['by_collection']}")
    log(f"links: {summary['links']}")
    log(f"unresolved names (abrogated laws): {summary['unresolved']}; "
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
    print(f"wrote {out / GOLD_JSON} and {out / GOLD_MD}")
    report(args.work_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
