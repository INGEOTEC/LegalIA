"""The line patterns that recognize a law's structure.

Every pattern here was written against counts measured over the 315 laws of
the SCJN corpus (the most recent snapshot of each), not against a guess at
what a law looks like. Where a count is quoted in a comment it is that
measurement, and it is the reason the pattern is shaped the way it is.

The scan is line-oriented: a block's **first line** decides what the block
is. That is not a simplification of Markdown, it is what Markdown is — a
block format, where what opens a block determines its kind.

What is here, and which issue put it there:

- #158 — the frontmatter fence.
- #159 — containers (LIBRO/TÍTULO/CAPÍTULO/SECCIÓN/APARTADO), articles,
  transitorios and the closing signatures.
- #160 — fracciones, incisos and subincisos.
- #161 — the `(REFORMADO, D.O.F. ...)` annotations.
"""

import re

# --------------------------------------------------------------------- #158

#: The `---` fence around the YAML frontmatter the SCJN corpus files open
#: with. Matched at the very start of the document only — a `---` in the body
#: is a horizontal rule, not a frontmatter fence.
FRONTMATTER_FENCE = re.compile(r"^---[ \t]*\r?\n")

#: One `key: value` line of the frontmatter. The value is everything after
#: the first colon, so a value that itself contains ": " (a title with a
#: subtitle, say) survives intact.
FRONTMATTER_ENTRY = re.compile(r"^([A-Za-z_][\w.-]*)[ \t]*:[ \t]*(.*)$")


# --------------------------------------------------------------------- #159

#: Ordinal numerals written as words. Containers and transitorio articles are
#: numbered this way far more often than with digits (`**TÍTULO PRIMERO**`,
#: 114; `**Primero.**` opening a transitorio, 310), and the corpus reaches
#: well past the small ones — hence the tens (`VIGÉSIMO`, `TRIGÉSIMO`,
#: `QUINCUAGÉSIMO`), which combine with a unit (`VIGÉSIMO PRIMERO`).
_UNIDAD_ORDINAL = (
    r"PRIMER[OA]?|SEGUND[OA]|TERCER[OA]?|CUART[OA]|QUINT[OA]|SEXT[OA]|"
    r"S[EÉ]PTIM[OA]|OCTAV[OA]|NOVEN[OA]|D[EÉ]CIM[OA]|UND[EÉ]CIM[OA]|"
    r"DUOD[EÉ]CIM[OA]|[UÚ]NIC[OA]"
)
_DECENA_ORDINAL = (
    r"D[EÉ]CIM[OA]|VIG[EÉ]SIM[OA]|TRIG[EÉ]SIM[OA]|CUADRAG[EÉ]SIM[OA]|"
    r"QUINCUAG[EÉ]SIM[OA]|SEXAG[EÉ]SIM[OA]|SEPTUAG[EÉ]SIM[OA]|"
    r"OCTOG[EÉ]SIM[OA]|NONAG[EÉ]SIM[OA]|CENT[EÉ]SIM[OA]"
)
#: An ordinal in words, tens-then-units, matched case-insensitively at use
#: (`PRIMERO`, `Primero`, `VIGÉSIMO PRIMERO`, `ÚNICO`).
ORDINAL_PALABRA = rf"(?:(?:{_DECENA_ORDINAL})\s+)?(?:{_UNIDAD_ORDINAL})"

#: The Latin suffixes a Mexican article number takes when a reform inserts an
#: article between two existing ones: `27 Bis`, `27 Bis 1`, `27 Ter`. Measured
#: in the corpus as `**ARTICULO N BIS` (363) and `**Artículo N Bis` (174).
#:
#: Matched case-insensitively, and that is not cosmetic: the LFT writes
#: `**Artículo 3° bis.**` and `**Artículo 3° Ter.**` in lower and title case
#: within three blocks of each other, and reading those as plain "article 3°"
#: silently merges three different articles into one number.
SUFIJO_LATINO = (
    r"BIS|TER|QU[AÁ]TER|QUINQUIES|SEXIES|SEPTIES|OCTIES|NONIES|DECIES|"
    r"UNDECIES|DUODECIES|TERDECIES|QUATERDECIES"
)

#: An article's number, as written. Three shapes, in one alternation:
#:
#: - digits, optionally ordinal-marked (`1o.`, `1°`, `1º`), optionally with a
#:   letter suffix (`27-A`, seen as `**ARTICULO N-A.-`), optionally with one
#:   or more Latin suffixes and their own digit (`27 Bis 1`);
#: - **several numbers in one heading** — `Art. 30,31.` (501 occurrences).
#:   Kept as a single `num` of `"30,31"` rather than split into two nodes:
#:   the two articles share one heading and one body, and dividing that text
#:   between them is not possible without guessing. #162's sweep counts them.
#: - an ordinal in words, which is how transitorio articles are numbered.
NUM_ARTICULO = (
    r"(?:"
    # The ordinal marker binds to the digits: `(?:[ \t]*[oº°])?`, not
    # `[ \t]*[oº°]?`. Written the loose way, the optional whitespace is
    # consumed even when no marker follows, and `ARTICULO 28 Bis` comes out
    # as article "28 " with the suffix stranded -- which silently merged 28
    # and 28 Bis into one number across 136 laws before it was caught.
    r"\d+(?:[ \t]*[oº°])?"
    # A Latin suffix may be joined by a space or by a dash: the CCF writes
    # `ARTICULO 103-Bis.-` where every other law writes `ARTICULO 103 Bis`.
    rf"(?:(?:[ \t]*[-–][ \t]*|[ \t]+)(?i:{SUFIJO_LATINO})\b(?:[ \t]+\d+)?)*"
    # A single-letter suffix: `27-A`, and the CCF's space-separated `410 A.-`.
    # The space-separated form is admitted only when the letter is
    # immediately followed by the heading's separator -- without that
    # lookahead, "Artículo 5 A las personas..." would swallow its own first
    # word as a suffix.
    r"(?:[ \t]*[-–][ \t]*[A-Za-zÁÉÍÓÚÑ]\b|[ \t]+[A-ZÁÉÍÓÚÑ](?=[ \t]*[.\-–]))?"
    r"(?:[ \t]*,[ \t]*\d+(?:[ \t]*[oº°])?)*"
    rf"|{ORDINAL_PALABRA}"
    r")"
)

#: The word that opens an article heading. `Art.` without bold is how the
#: oldest laws write it (`Art. N.` 990 times, `Art. N.-` 132), so the bold
#: markers are optional throughout.
_PALABRA_ARTICULO = r"(?:ART[IÍ]CULO|ARTICULO|Art[ií]culo|Articulo|ART[IÍ]C|ART|Art)"

#: An article heading at the start of a block. Not anchored to the end of the
#: line: an article heading is followed by the article's own text on the same
#: line far more often than not.
#:
#: A doubled space between the word and the number is admitted (`**Artículo
#: <n>.`, 569 occurrences) — `[ \t]*` covers it.
#:
#: There is deliberately no `No.` here. Issue #159 lists `**ARTICULO No.-`
#: among the measured shapes, but that reading is an artefact of the
#: measurement: `N` stands for the digits that were folded out, so the shape
#: is `**ARTICULO 1o.-` — an ordinal marker, which `NUM_ARTICULO` already
#: handles. Grepping the corpus for a literal "ARTICULO No." returns nothing.
ARTICULO = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*"
    + _PALABRA_ARTICULO
    + r"S?\.?[ \t]*"
    + rf"(?P<num>{NUM_ARTICULO})"
    + r"(?P<sep>[ \t]*(?:\.\-|\.|\-|–)?)",
    re.UNICODE,
)

#: A transitorio article written without the word "artículo" at all — just
#: its ordinal, in bold: `**Primero.** El presente Decreto entrará en vigor...`
#: (310 occurrences), `**Único.-**` (254), `**Primero.-**` (194).
#:
#: This one is **context-dependent and must not be applied on its own**: a
#: paragraph of ordinary law text can open with a bolded ordinal too. The
#: builder only reads it as an article inside a transitorios section, where
#: it is the normal way provisions are numbered.
ARTICULO_ORDINAL = re.compile(
    r"^[ \t]*\*\*[ \t]*(?P<num>(?i:" + ORDINAL_PALABRA + r"))"
    r"[ \t]*(?:\.\-|\.|\-|–)?[ \t]*\*\*",
    re.UNICODE,
)

#: The container words, mapped to the Akoma Ntoso element each becomes.
#: `APARTADO` has no element in the standard at all and is expressed as
#: `level` plus `refers_to="#apartado"` — the same escape hatch
#: `nota2md/akoma_ntoso.py` uses for transitorios.
CONTENEDORES = {
    "LIBRO": "book",
    "TITULO": "title",
    "CAPITULO": "chapter",
    "SECCION": "section",
    "APARTADO": "level",
}

#: Precedence, largest container first. Containers nest by rank rather than
#: by any explicit depth marker in the text: on meeting one, every open
#: container of equal or lower rank is closed. That is what makes a law that
#: opens with `CAPÍTULO I` and only later reaches `TÍTULO PRIMERO` come out
#: with the chapter closed rather than swallowed by the title.
PRECEDENCIA = {"book": 1, "title": 2, "chapter": 3, "section": 4, "level": 5}

#: A container heading. The **whole line** must be the heading — otherwise
#: "conforme al Capítulo III de esta Ley" in the middle of a sentence would
#: open a chapter. Bold is optional: `Capítulo I` with no markup at all is
#: the second most common form in the corpus (302 occurrences, against 285
#: for `**CAPÍTULO I**`).
CONTENEDOR = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"
    r"(?P<clase>LIBRO|Libro|T[IÍ]TULO|T[ií]tulo|CAP[IÍ]TULO|Cap[ií]tulo|"
    r"SECCI[OÓ]N|Secci[oó]n|APARTADO|Apartado)"
    r"[ \t]+(?P<num>[^*\n]{1,40}?)"
    r"[ \t]*\.?[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: A TRANSITORIOS marker, opening a block of transitional provisions. The
#: dominant form is already a Markdown heading (`## Transitorios` 301,
#: `## Transitorio` 154); the rest are bold, with or without the word
#: "ARTÍCULOS" and with or without a trailing colon or period.
TRANSITORIOS = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"
    r"(?:(?:ART[IÍ]?CULOS?|Art[ií]culos?)[ \t]+)?"
    r"(?:TRANSITORIOS?|Transitorios?)"
    r"(?:[ \t]+(?:DEL?|DE[ \t]+LAS?|Del?)[ \t][^*\n]{0,80}?)?"
    r"[ \t]*[.:]?[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: `**D.O.F. 15 DE SEPTIEMBRE DE 2024.**` — the header the corpus puts before
#: each *further* block of transitional provisions, one per reform decree
#: that added some (724 occurrences of the December form alone). It opens a
#: new transitorios section rather than continuing the previous one: those
#: are separate blocks of provisions from separate decrees, and merging them
#: would lose which decree each belongs to.
DOF_TRANSITORIOS = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*D\.\s*O\.\s*F\.[ \t]+(?P<fecha>[^*\n]{4,60}?)"
    r"[ \t]*\.?[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: The closing signatures. Only 85 of the 315 laws have any — a consolidated
#: SCJN text usually ends at its last transitorio — so detection is
#: deliberately narrow: the rubric word itself, or the constitutional
#: promulgation formula that always precedes it.
CONCLUSIONES = re.compile(
    r"R[uú]bricas?\.|En cumplimiento de lo dispuesto por la fracci[oó]n I "
    r"del [AaÁá]rt[ií]culo 89",
    re.UNICODE | re.IGNORECASE,
)

#: How far back from the end of the document `CONCLUSIONES` is looked for. A
#: rubric is the last thing in a law; the same words appearing in the middle
#: of one are quoting a decree, not signing this text.
CONCLUSIONES_ULTIMOS_BLOQUES = 4

#: A block that is nothing but bold text — the shape a container's epigraph
#: takes when the container line itself was bold.
SOLO_NEGRITAS = re.compile(r"^[ \t]*\*\*(?P<texto>[^*]+)\*\*[ \t]*\.?[ \t]*$")

#: Longest a block may be to be taken as a container's epigraph. Epigraphs
#: are titles; a paragraph of normative text that happened to follow a
#: container heading is not one.
MAX_EPIGRAFE = 200

#: An editorial note the SCJN inserts, e.g. "(NOTA: EL 1 DE JUNIO DE 2021, EL
#: PLENO DE LA SUPREMA CORTE...)" (882 occurrences of the June form). Not an
#: annotation in #161's sense — it records a court ruling, not a reform — and
#: never a structural marker, so it is recognized only to be left as content.
NOTA_EDITORIAL = re.compile(r"^[ \t]*(?:\*\*)?[ \t]*\((?:NOTA|Nota)[ \t]*:", re.UNICODE)

#: A reform annotation as a block of its own. Whether the parenthesis really
#: is one is decided by `md2akn.annotations.es_anotacion` on the captured
#: body, not here: the corpus writes plenty of other things in the same shape
#: — `(NOTA: EL 22 DE JUNIO DE 2023, EL PLENO DE LA SUPREMA CORTE…)`,
#: `(ARANCEL)`, `(VÉASE TABLA ANEXA)` — and none of them is a reform.
ANOTACION = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*\((?P<cuerpo>[A-ZÁÉÍÓÚÑ][^)\n]*)\)[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: An annotation written *inside* an article's own heading rather than as a
#: block of its own: `**ARTICULO 5.- (DEROGADO, D.O.F. 3 DE MAYO DE 1999)**`.
#: 4,557 occurrences, almost all of them DEROGADO — which makes sense: a
#: repealed article has no text left to put a block annotation above.
ANOTACION_EN_LINEA = re.compile(r"\((?P<cuerpo>[A-ZÁÉÍÓÚÑ][^)\n]*)\)", re.UNICODE)


# --------------------------------------------------------------------- #160

#: A fracción/inciso/subinciso marker at the head of a block. Measured shapes
#: and their counts over the 315 laws:
#:
#:     55398  **I.**      (bold Roman, the fracción)      9268  I.-
#:      8198  **a)**      (bold inciso)                    929  a)
#:     21416  1.          (unadorned digits)               364  **A.**
#:
#: Recognizing a marker is not the same as accepting one: the label this
#: captures is only a list item if `md2akn.lists` can place it, which is what
#: keeps `C.` in "El C. Primer Jefe" and the tariff schedules' tens of
#: thousands of `1.` lines out of the tree.
#:
#: The separator is mandatory — a bare word at the head of a block is not a
#: marker — and bold is optional on either side, since the corpus writes
#: `**I.**`, `**I.` and `I.-` all three.
MARCADOR_LISTA = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*"
    r"(?P<etiqueta>[A-Za-z]+(?:[ \t]+(?i:" + SUFIJO_LATINO + r")\b(?:[ \t]+\d+)?)?|\d+)"
    r"[ \t]*(?P<sep>\)|\.\-|\.|\-)"
    r"(?:\*\*)?(?=[ \t]|$)",
    re.UNICODE,
)
