"""The outline of a consolidated text, as the SCJN states it (review fix-2 of
#291).

`GET /api/SCOW/Articulos` does not return a flat text: it returns the
instrument as ordered **blocks**, each with an `orden`, a `numero` (the
article number, `0` for what is not an article) and a `referencia`, a label
that names what the block *is* -- `ENCABEZADO`, `CONSIDERANDO`, `ÍNDICE`,
`TÍTULO PRIMERO`, `ARTÍCULO 6 TER`, `TRANSITORIO`, `ANEXO 1`, `PARTE 3`,
`D.O.F. 10 DE NOVIEMBRE DE 2023`. That label is the document's own outline,
and the Markdown writer used to throw it away: a reader (`md2akn`) then had
to guess the structure back from the flattened text, and guessed wrong where
the text alone is ambiguous -- an index read as containers, the 83,828
characters of a reform decree that reprints the whole instrument read as a
second copy of it, an annex that numbers its articles from 1 again.

So the writer now keeps the outline. Each block opens with one **marker
line**, an HTML comment on a line of its own::

    <!-- scjn:block orden=6 numero=0 kind=toc label="ÍNDICE" -->

An HTML comment is invisible to every Markdown renderer, changes no character
of the block's own text, and is one line a reader can recognise with a
regular expression (`MARKER`). The attributes are `key=value` pairs separated
by one space, in this order:

- `orden`, `numero`: the API's own, verbatim.
- `kind`: what the block is, one of `KINDS`.
- `type`, only on a `container`: `book`, `title`, `chapter`, `section`,
  `apartado`, `part`, `annex` or `appendix`.
- `num`, on a `container`, an `article`, a `provision` and a `decree`: the
  number as the label spells it (accents and case kept, whitespace
  collapsed), as a JSON string.
- `label`: the API's `referencia`, verbatim, as a JSON string (so a quote or
  a backslash in it cannot end the marker) -- always last.

`kind` is derived from the label, never from the instrument (`classify`): the
vocabulary is open (841 distinct shapes over 214 sampled snapshots once the
digits are folded), so a label that matches nothing is `other` and still
carries its `label`.

`strip_markers` takes the lines back out, for the readers that want the text
as it always was (a reader's own Markdown, a diff between two snapshots).
"""

from __future__ import annotations

import json
import re
import unicodedata

#: Every `kind` a marker can carry.
KINDS = (
    "header",        # ENCABEZADO: the instrument's title block
    "recitals",      # CONSIDERANDO
    "preamble",      # PREÁMBULO
    "toc",           # ÍNDICE
    "transitorios",  # TRANSITORIO(S)
    "decree",        # D.O.F. <fecha>: a reform decree's own text
    "container",     # LIBRO/TÍTULO/CAPÍTULO/SECCIÓN/APARTADO/PARTE/ANEXO/APÉNDICE
    "article",       # ARTÍCULO N
    "provision",     # PRIMERO / ÚNICO / 1.1.0: an acuerdo's numbered point
    "other",
)

#: `type` of a `container` marker.
CONTAINER_TYPES = (
    "book", "title", "chapter", "section", "apartado", "part", "annex", "appendix",
)

MARKER_PREFIX = "<!-- scjn:block "

#: A marker line, whole. `md2akn.patterns.MARCADOR_ESQUEMA` is a copy of this
#: on purpose (`md2akn` imports nothing else of this monorepo); a test in
#: `md2akn` reads a fixture this package wrote, so the two cannot drift.
MARKER = re.compile(r"^<!-- scjn:block (?P<attrs>.*?) -->[ \t]*$", re.MULTILINE)

_SPACES = re.compile(r"\s+")

_CONTAINER_WORD = {
    "LIBRO": "book",
    "TITULO": "title",
    "CAPITULO": "chapter",
    "SECCION": "section",
    "APARTADO": "apartado",
}

_ORDINAL = (
    r"(?:UNICO|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SEPTIMO|OCTAVO|NOVENO|"
    r"DECIMO(?:\s+(?:PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SEPTIMO|OCTAVO|NOVENO))?|"
    r"(?:VIGESIMO|TRIGESIMO|CUADRAGESIMO|QUINCUAGESIMO|SEXAGESIMO|SEPTUAGESIMO|OCTOGESIMO|"
    r"NONAGESIMO|CENTESIMO)(?:\s+(?:PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SEPTIMO|"
    r"OCTAVO|NOVENO))?)"
)

_RE_CONTAINER = re.compile(
    r"^(LIBRO|TITULO|CAPITULO|SECCION|APARTADO)\s+(?P<num>\S.*)$"
)
_RE_PART = re.compile(r"^PARTE\s+(?P<num>(?!FINAL\b)\S.*)$")
# `PRIMERA PARTE`, `I PARTE`: the number first.
_RE_PART_FIRST = re.compile(
    r"^(?P<num>(?:UNICA|PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SEPTIMA|OCTAVA|NOVENA|"
    r"DECIMA(?:\s+(?:PRIMERA|SEGUNDA|TERCERA))?|[IVXLCDM]+))\s+PARTE$"
)
_RE_ANNEX = re.compile(r"^ANEXO(?:\s+(?P<num>\S.*))?$")
_RE_APPENDIX = re.compile(r"^APENDICE(?:\s+(?P<num>\S.*))?$")
# `ARTÍCULO I: ENMIENDA` is article `I`: what follows a colon is a title.
_RE_ARTICLE = re.compile(r"^ARTICULOS?\s+(?P<num>[^:\s](?:[^:]*[^:\s])?)\s*(?::.*)?$")
# `D.O.F. 10 DE NOVIEMBRE DE 2023`, and `D.O. 23 DE JULIO DE 1949` for what the
# gazette was called before 1977.
_RE_DECREE = re.compile(r"^D\.?\s*O\.?(?:\s*F\.?)?\s+(?P<num>\d.*)$")
_RE_PROVISION = re.compile(rf"^(?P<num>{_ORDINAL}|\d+(?:\.\d+)*)\.?$")
_RE_TRANSITORIOS = re.compile(r"^(?:ARTICULOS\s+|DISPOSICIONES\s+)?TRANSITORI[OA]S?$")


def _fold(text: str) -> tuple[str, list[int]]:
    """`text` upper-cased and without accents, one character at a time, and
    for each character of the result the index it came from in `text` -- what
    a label is matched against while its number is still read off the
    original, accents and all."""
    folded: list[str] = []
    origin: list[int] = []
    for i, char in enumerate(text):
        plain = "".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c))
        for c in plain.upper():
            folded.append(c)
            origin.append(i)
    return "".join(folded), origin


def _num(original: str, origin: list[int], match: re.Match) -> str:
    """The number `match` captured in the folded label, read off `original`:
    whitespace collapsed, a trailing full stop dropped."""
    start, end = match.span("num")
    return _SPACES.sub(" ", original[origin[start]: origin[end - 1] + 1]).strip().rstrip(".")


def classify(referencia: str) -> dict[str, str]:
    """What a block's `referencia` says it is, as ``{"kind": ..., ["type": ...],
    ["num": ...]}`` -- the number spelled as the label spells it.

    >>> classify("ÍNDICE")
    {'kind': 'toc'}
    >>> classify("CAPÍTULO ÚNICO")
    {'kind': 'container', 'type': 'chapter', 'num': 'ÚNICO'}
    >>> classify("ARTÍCULO  6 TER")
    {'kind': 'article', 'num': '6 TER'}
    >>> classify("D.O.F. 10 DE NOVIEMBRE DE 2023")
    {'kind': 'decree', 'num': '10 DE NOVIEMBRE DE 2023'}
    >>> classify("PARTE FINAL DEL ACUERDO")
    {'kind': 'other'}
    """
    # Whitespace is collapsed *before* folding, so `origin` indexes `original`.
    original = _SPACES.sub(" ", referencia.strip())
    label, origin = _fold(original)
    if label == "ENCABEZADO":
        return {"kind": "header"}
    if re.fullmatch(r"CONSIDERANDOS?", label):
        return {"kind": "recitals"}
    if label == "PREAMBULO":
        return {"kind": "preamble"}
    if label in ("INDICE", "TABLA DE CONTENIDO", "TABLA DE CONTENIDOS"):
        return {"kind": "toc"}
    if _RE_TRANSITORIOS.match(label):
        return {"kind": "transitorios"}
    m = _RE_DECREE.match(label)
    if m:
        return {"kind": "decree", "num": _num(original, origin, m)}
    m = _RE_CONTAINER.match(label)
    if m:
        return {
            "kind": "container", "type": _CONTAINER_WORD[m.group(1)],
            "num": _num(original, origin, m),
        }
    m = _RE_PART.match(label)
    if m:
        return {"kind": "container", "type": "part", "num": _num(original, origin, m)}
    m = _RE_PART_FIRST.match(label)
    if m:
        return {"kind": "container", "type": "part", "num": _num(original, origin, m)}
    m = _RE_ANNEX.match(label)
    if m:
        return _annex("annex", original, origin, m)
    m = _RE_APPENDIX.match(label)
    if m:
        return _annex("appendix", original, origin, m)
    m = _RE_ARTICLE.match(label)
    if m:
        return {"kind": "article", "num": _num(original, origin, m)}
    m = _RE_PROVISION.match(label)
    if m:
        return {"kind": "provision", "num": _num(original, origin, m)}
    return {"kind": "other"}


def _annex(type_: str, original: str, origin: list[int], match: re.Match) -> dict[str, str]:
    out = {"kind": "container", "type": type_}
    if match.group("num"):
        out["num"] = _num(original, origin, match)
    return out


def _json(value: str) -> str:
    """`value` as a JSON string that cannot end an HTML comment early: `--` is
    the one sequence a label could use to do it (`\\u002d` is the same
    character to a JSON reader), and `ensure_ascii` off keeps the accents
    readable."""
    return json.dumps(value, ensure_ascii=False).replace("--", "-\\u002d")


def marker_line(orden: int | None, numero: int | None, referencia: str) -> str:
    """The marker that opens a block, one line.

    >>> marker_line(6, 0, "ÍNDICE")
    '<!-- scjn:block orden=6 numero=0 kind=toc label="ÍNDICE" -->'
    >>> marker_line(8, 2, "CAPÍTULO II")
    '<!-- scjn:block orden=8 numero=2 kind=container type=chapter num="II" label="CAPÍTULO II" -->'
    """
    fields = classify(referencia)
    parts = [f"orden={'' if orden is None else orden}", f"numero={'' if numero is None else numero}"]
    parts += [
        f"{key}={_json(value)}" if key == "num" else f"{key}={value}" for key, value in fields.items()
    ]
    return f"{MARKER_PREFIX}{' '.join(parts)} label={_json(referencia)} -->"


_ATTRIBUTE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_marker(line: str) -> dict[str, str] | None:
    """The attributes of a marker line (`num`/`label` decoded), or `None` when
    `line` is not one.

    >>> parse_marker(marker_line(8, 2, "CAPÍTULO II"))["type"]
    'chapter'
    """
    m = MARKER.match(line.strip())
    if m is None:
        return None
    return {
        key: json.loads(value) if value.startswith('"') else value
        for key, value in _ATTRIBUTE.findall(m.group("attrs"))
    }


def strip_markers(markdown: str) -> str:
    """`markdown` without its marker lines: what the snapshot read as before
    the outline was kept. A marker is a paragraph of its own, so its line and
    the blank line after it go together, which makes this the exact inverse
    of what `articulos_a_markdown(..., outline=True)` adds.

    >>> strip_markers(marker_line(1, 0, "ENCABEZADO") + "\\n\\nLEY\\n")
    'LEY\\n'
    """
    return re.sub(r"^<!-- scjn:block .*? -->[ \t]*\n(?:[ \t]*\n)?", "", markdown, flags=re.MULTILINE)
