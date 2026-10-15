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
- #227 — `NUMERAL_DECIMAL`, for the *acuerdo*-shaped instrument that numbers
  its provisions `1.` / `2.1` rather than `Artículo N` (rule 8).
- #279 — `NUMERO_ROMANO`, a roman-numbered article (rule 10).
- #291 — `NUMERO_DECIMAL_ARTICULO` (rule 11), a decimal article number
  (`Artículo 1.1`), and `FILA_TABLA`/`ETIQUETA_TABLA` (rule 13), the table
  rows `scjn` writes since #253.
- review fix-1 of #291 (repeated article numbers, rules 14 to 17) — the whole
  article number (`NUM_ARTICULO`, `SUFIJO_LATINO`, `ORDINAL_PALABRA`,
  `NUMERO_DECIMAL_ARTICULO` read what the SCJN actually writes: `1o. Bis`,
  `1,055 Bis 1`, `8-01`, `195-Z-1`, `4B`, `Quintus`, `DECIMOSEGUNDO`,
  `2.A.1`), `MARCADOR_INDICE` (rule 15, a table of contents) and
  `CONTENEDOR_ANEXO`/`CONTENEDOR_PARTE` (rule 16, `PARTE`/`ANEXO`/`APÉNDICE`
  containers).
"""

import re

# --------------------------------------------------------------------- #158

#: The `---` fence around the YAML frontmatter the SCJN corpus files open
#: with. Matched at the very start of the document only — a `---` in the body
#: is a horizontal rule, not a frontmatter fence.
FRONTMATTER_FENCE = re.compile(r"^---[ \t]*\r?\n")

#: One ``key: value`` line of the frontmatter. The value is everything after
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
#: (`PRIMERO`, `Primero`, `VIGÉSIMO PRIMERO`, `ÚNICO`). The tens and the unit
#: may be written as one word (`DECIMOSEGUNDO`, `ARTICULO DECIMO**TERCERO`:
#: `tratados/11340`, `23710`, `11379`, `reglamentos/21196`) and the elided
#: `DECIMOCTAVO` is one too -- review fix-1 of #291: `DECIMO` alone is the
#: tenth, so reading `DECIMOSEGUNDO` as article `DECIMO` repeated eight
#: articles of one treaty under one number.
ORDINAL_PALABRA = (
    rf"D[EÉ]CIMOCTAV[OA]|VIG[EÉ]SIMOCTAV[OA]|(?:(?:{_DECENA_ORDINAL})[ \t]*)?(?:{_UNIDAD_ORDINAL})"
)

#: The Latin suffixes a Mexican article number takes when a reform inserts an
#: article between two existing ones: `27 Bis`, `27 Bis 1`, `27 Ter`. Measured
#: in the corpus as `**ARTICULO N BIS` (363) and `**Artículo N Bis` (174).
#:
#: Matched case-insensitively, and that is not cosmetic: the LFT writes
#: `**Artículo 3° bis.**` and `**Artículo 3° Ter.**` in lower and title case
#: within three blocks of each other, and reading those as plain "article 3°"
#: silently merges three different articles into one number.
_UNIDAD_LATINA = (
    r"PRIM[UO]S|SEC[UA]ND[UA]S|TERTIUS|QUARTUS|QUINTUS|SEXTUS|S[EÉ]PTIMUS|OCTAVUS|NONUS"
)
SUFIJO_LATINO = (
    # The compounds first: an alternation takes the first branch that fits,
    # and `QUINQUIES` alone would leave the `DECIES` of `73 Quinquies decies`.
    # `78 Quinquies**decies**` and `73 SEXIES DECIES`: the SCJN writes the
    # compound with or without a space (review fix-1 of #291, `laero`/`lac`).
    r"(?:QUINQUIES|SEXIES|SEPTIES|OCTIES|NONIES)[ \t]*DECIES|"
    r"BIS|TER|QU[AÁ]TER|QUINQUIES|SEXIES|SEPTIES|OCTIES|NONIES|DECIES|"
    r"UNDECIES|DUODECIES|TERDECIES|QUATERDECIES|"
    # The `-us` series some laws use instead (`Artículo 12 Quintus`,
    # `Artículo 188 Quartus decimus`, `Artículo 188 Vicesimus primus`): `cpf`,
    # `lic`, `cjm`, `lfd`, `lfpc` and the reglamento 109182, which runs it up
    # to the twenty-first (`Vicesimus primus`).
    rf"(?:(?:UNDE|DUODE)?(?:VICESIMUS|TRICESIMUS)|UNDECIMUS|DUODECIMUS|DECIMUS)"
    rf"(?:[ \t]+(?:{_UNIDAD_LATINA}))?|"
    rf"(?:{_UNIDAD_LATINA})(?:[ \t]+DECIMUS)?"
)

#: A roman-numbered article's number (issue #279's rule 10): `ARTÍCULO I`,
#: `**ARTÍCULO IV.-**`, `ARTÍCULO XV BIS`. International treaties write their
#: own articles this way -- over the 1,373 cached `scjn-tratados` with text,
#: 489 carry 6,646 such openers (`**ARTÍCULO R**` 5,661, `ARTÍCULO R` 705,
#: `ARTÍCULO R.` 137, `**ARTÍCULO R.**` 73, `ARTÍCULO R.-` 62, with `R` the
#: roman number) and 229 had no `article` unit at all before this alternative.
#: Across the three published corpora it reaches exactly one instrument, the
#: reglamento 182464 (30 lines, `ARTÍCULO I.` .. `ARTÍCULO XXX.`); no law and
#: no lineamiento carries one.
#:
#: Upper case only and a *valid* roman numeral (`M{0,3}(CM|CD|D?C{0,3})...`),
#: not just a run of roman letters: `ARTÍCULO MIL...` and `ARTÍCULO CIVIL`
#: are all roman letters and are not numbers. The lookahead admits only what
#: may follow a digit number (a separator, whitespace, a closing `**`, the end
#: of the line), so `ARTÍCULO VIGÉSIMO` is not read as `V` + `IGÉSIMO` and is
#: left to `ORDINAL_PALABRA` below. The Latin suffix is admitted as after
#: digits (`XV BIS`).
NUMERO_ROMANO = (
    r"(?=[IVXLCDM])"
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
    r"(?=[ \t]*(?:\.|\-|–)|\s|\*|$)"
    rf"(?:[ \t]+(?i:{SUFIJO_LATINO})\b)*"
)

#: An article's number, as written. Four shapes, in one alternation:
#:
#: - digits (`28`, or `1,055` with the thousands separator the Código de
#:   Comercio and the Código Civil Federal write), optionally ordinal-marked
#:   (`1o.`, `1°`, `1º`, `1/o`), followed by any number of **components**,
#:   each of which extends the number rather than ending it:
#:
#:   * a Latin suffix (`27 Bis`, `103-Bis`, `3bis`, `12 Quintus`), which may
#:     be followed by its own digits (`27 Bis 1`) and, after an ordinal's
#:     dot, stands on its own (`1o. Bis`: 24 laws and regulations repeated the
#:     article `1o` for it before review fix-1 of #291);
#:   * hyphen-digits (`1-01`, `58-1`, `19-1`, `8-01 bis`), how the ALADI
#:     agreements and the Ley Federal de Derechos number their insertions;
#:   * a letter -- hyphenated (`27-A`, `195-Z-1`), space-separated before the
#:     heading's own separator (`410 A.-`, admitted only there: without the
#:     lookahead "Artículo 5 A las personas..." would swallow its first word),
#:     adjacent (`4B.-`, `3A`) or after an ordinal's own separator
#:     (`1o.-A.-`, `7o.-A.`, how the LIVA and the LIEPS write theirs; the
#:     letter must be followed directly by a separator, so `1o.-A falta de..`
#:     stays a sentence);
#: - **several numbers in one heading** — `Art. 30,31.` (501 occurrences).
#:   Kept as a single `num` of `"30,31"` rather than split into two nodes:
#:   the two articles share one heading and one body, and dividing that text
#:   between them is not possible without guessing. #162's sweep counts them.
#:   A comma followed by exactly three digits is a thousands separator, not a
#:   list.
#: - a roman numeral (`NUMERO_ROMANO`, issue #279), which is how treaties
#:   number their articles;
#: - an ordinal in words, which is how transitorio articles are numbered.
_ENTERO = r"(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)"
_COMPONENTE_ARTICULO = (
    r"(?:"
    # A Latin suffix, joined by a dash, a space, nothing at all (`3bis`) or,
    # right after an ordinal, its dot (`1o. Bis`); its own digits may follow.
    rf"(?:[ \t]*[-–][ \t]*|[ \t]*|\.[ \t]+)(?i:{SUFIJO_LATINO})\b(?:[ \t]+\d+)?"
    # Hyphen-digits, or a hyphenated letter.
    r"|[ \t]*[-–][ \t]*(?:\d+|[A-Za-zÁÉÍÓÚÑ]\b)"
    # The space-separated letter (`410 A.-`) needs the separator right after.
    r"|[ \t]+[A-ZÁÉÍÓÚÑ](?=[ \t]*[.\-–])"
    # An adjacent capital letter (`4B.-`, `3A`) ends the heading or is followed
    # by a separator.
    r"|(?<=\d)[A-ZÁÉÍÓÚÑ](?=[ \t]*[.\-–:]|[ \t]*$)"
    # A capital letter after an ordinal's own separator (`1o.-A.-`).
    r"|(?<=[oº°])[ \t]*\.?[ \t]*[-–]?[ \t]*[A-ZÁÉÍÓÚÑ](?=[.\-–])"
    r")"
)
NUM_ARTICULO = (
    r"(?:"
    # The ordinal marker binds to the digits: `(?:[ \t]*[oº°])?`, not
    # `[ \t]*[oº°]?`. Written the loose way, the optional whitespace is
    # consumed even when no marker follows, and `ARTICULO 28 Bis` comes out
    # as article "28 " with the suffix stranded -- which silently merged 28
    # and 28 Bis into one number across 136 laws before it was caught.
    rf"{_ENTERO}(?:[ \t]*/?[oº°])?"
    rf"{_COMPONENTE_ARTICULO}*"
    r"(?:[ \t]*,[ \t]*\d+(?:[ \t]*[oº°])?)*"
    rf"|{NUMERO_ROMANO}"
    rf"|{ORDINAL_PALABRA}"
    r")"
)

#: A decimal article number (issue #291's rule 11): `Artículo 1.1:`,
#: `Artículo 12.3.-`, `Artículo 3.2.1` -- chapter and article, how the T-MEC
#: and six other `scjn-tratados` instruments number their articles; since
#: review fix-1 also the T-MEC's annex articles, `Artículo 2.A.1:`,
#: `Artículo 12.D.1:` (a capital letter between two dots, and only when a
#: digit follows it). Tried
#: *before* `NUM_ARTICULO` inside `ARTICULO` only (never part of
#: `NUM_ARTICULO` itself, which other shapes build on): digits would otherwise
#: stop at `1` and read `Artículo 1.2`, `1.3`, ... all as article `1`. It
#: needs a digit after every dot, so `Artículo 1.-` and `Artículo 1.` are read
#: exactly as before; a `:` after the number is not a separator, it is simply
#: not consumed (`ARTICULO` is not anchored to the end of the line).
NUMERO_DECIMAL_ARTICULO = r"\d+(?:\.(?:\d+|[A-Z](?=\.\d)))+"

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
    + rf"(?P<num>{NUMERO_DECIMAL_ARTICULO}|{NUM_ARTICULO})"
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

#: A decimal numeral opening a block — `1.`, `2.1.`, `3.2.1` — the second way
#: an *acuerdo*-shaped instrument numbers its provisions when it never writes
#: `Artículo N` (issue #227's rule 8; 37 of the 89 articleless lineamientos
#: carry five or more of these).
#:
#: Like `ARTICULO_ORDINAL` this one is **context-dependent and must not be
#: applied on its own**: `1.` is also the most common fracción label in the
#: corpus (21,416 unadorned `1.` blocks over the 315 laws, most of them tariff
#: rows), so the builder reads it as an article only in a document that
#: numbers nothing with `Artículo N` at all.
#:
#: A bare integer is admitted only when a separator follows it (`1.`, `2)`,
#: `3.-`); a dotted numeral (`2.1`) needs none, since prose does not open with
#: one. Without that asymmetry a paragraph opening "2003 fue el año..." would
#: be read as provision 2003.
NUMERAL_DECIMAL = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*"
    r"(?P<num>\d+(?:\.\d+)+|\d+(?=[ \t]*(?:\.\-|\.|\)|\-|–)))"
    r"[ \t]*(?:\.\-|\.|\)|\-|–)?[ \t]*(?:\*\*)?(?=[ \t]|$)",
    re.UNICODE,
)


#: The container words, mapped to the Akoma Ntoso element each becomes.
#: `APARTADO` has no element in the standard at all and is expressed as
#: `level` plus `refers_to="#apartado"` — the same escape hatch this package
#: uses for transitorios (`REFERS_TO_TRANSITORIOS` in `md2akn.model`).
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
PRECEDENCIA = {
    "attachment": 0, "part": 1, "book": 2, "title": 3, "chapter": 4, "section": 5, "level": 6,
}

#: Where a `PARTE` sits when the document opens its chapters *before* its
#: first part (SOLAS: `Capítulo II-2` holds `Parte A`, `Parte B`): inside the
#: chapter, so just below it. Every other document -- the IMDG Code, whose
#: seven parts hold the chapters -- keeps `PRECEDENCIA["part"]`.
PRECEDENCIA_PARTE_EN_CAPITULO = 4.5

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

#: The number a `PARTE`, `ANEXO` or `APÉNDICE` carries (review fix-1 of #291,
#: rule 16): `1`, `4.31`, `1-A`, `II`, `III-B`, `A`, `B-1`, `PRIMERA`, `ÚNICO`.
#: A *number token*, never a word: `Parte Contratante` and `Anexo a la
#: solicitud` are prose, and the whole line has to be the heading.
_NUM_DE_PARTE = (
    r"(?:\d+(?:[.\-]\d+)*(?:[ \t]*-?[ \t]*[A-Z])?|[IVXLCDM]{1,8}(?:[ \t]*-[ \t]*[A-Z0-9]+)?|"
    r"[A-Z](?:-\d+)?|(?i:" + ORDINAL_PALABRA + r")|UNO|Uno|[UÚ]NIC[OA]|[Úú]nic[oa])"
)

#: What may follow the number on the same line: a parenthesised remark
#: (`ANEXO 3 (Rev.CMR-07)`, `ANEXO III (cont.)`), or a short title behind a
#: separator (`PARTE I. DISPOSICIONES GENERALES`, `PARTE II - Fecha de
#: entrada en vigor`) or, in capitals, directly (`PARTE 1 DISPOSICIONES
#: GENERALES, DEFINICIONES Y CAPACITACIÓN`).
_RESTO_DE_PARTE = (
    r"(?P<resto>[ \t]*(?:\([^)\n]{1,30}\)|[.:\-–—][ \t]*[^\n*]{1,80}|"
    r"[ \t]+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ,]{2,80}))?"
)

#: A `PARTE` heading, in the two shapes the corpus writes it: `PARTE 1`,
#: `Parte A`, `PARTE I. DISPOSICIONES GENERALES`, and the ordinal word before
#: the noun (`PRIMERA PARTE`, `SEGUNDA PARTE. FORMATOS E INSTRUCTIVOS`: the
#: reglamento 975 and 47726). Rule 16, review fix-1 of #291: 114738 (the
#: IMDG Code) is divided into seven of them, and without a container for it
#: every chapter `1.1` of every part hung off the root.
CONTENEDOR_PARTE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"
    r"(?:(?P<ord>(?i:" + ORDINAL_PALABRA + r"))[ \t]+PARTE|PARTE[ \t]+(?P<num>" + _NUM_DE_PARTE + r")|"
    r"Parte[ \t]+(?P<num2>" + _NUM_DE_PARTE + r"))"
    + _RESTO_DE_PARTE + r"[ \t]*\.?[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: An `ANEXO` or `APÉNDICE` heading: `ANEXO II`, `**ANEXO**` alone (163
#: occurrences, every one the heading of an annex), `Apéndice A`, `APÉNDICE 3
#: (Rev.CMR-07)`. Rule 16, review fix-1 of #291. An annex is its own text and
#: usually numbers its own articles from 1: it is a container of its own
#: (Akoma Ntoso's `attachment`) that closes every other open container.
CONTENEDOR_ANEXO = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"
    r"(?P<clase>ANEXO|Anexo|AP[ÉE]NDICE|Ap[ée]ndice)"
    r"(?:[ \t]+(?P<num>" + _NUM_DE_PARTE + r"))?"
    + _RESTO_DE_PARTE + r"[ \t]*\.?[ \t]*(?:\*\*)?[ \t]*$",
    re.UNICODE,
)

#: A table of contents' own marker (rule 15, review fix-1 of #291): a block
#: that is nothing but `Índice`, `**INDICE**`, `Contenido`, `Tabla de
#: contenido`. 100 instruments carry one; what follows it is a list of
#: headings that the rest of the document then repeats as its body.
MARCADOR_INDICE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?\**[ \t]*(?:tabla[ \t]+de[ \t]+)?"
    r"(?:[íi]ndice|contenidos?)(?:[ \t]+general)?[ \t]*[.:]?[ \t]*\**[ \t]*$",
    re.IGNORECASE | re.UNICODE,
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

#: The heading that opens an instrument's recitals: `**CONSIDERANDO**`,
#: `**CONSIDERANDO:**`, `CONSIDERANDOS` (rule 17, review fix-1 of #291). A
#: lineamiento or acuerdo numbers its recitals `PRIMERO.` .. `NOVENO.` and then
#: numbers its provisions `PRIMERO.` again; the recitals are the preamble, not
#: articles.
CONSIDERANDO = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*CONSIDERANDOS?[ \t]*[.:]?[ \t]*(?:\*\*)?[ \t]*$",
    re.IGNORECASE | re.UNICODE,
)

#: The heading that ends the recitals and opens what the instrument orders
#: (`**LINEAMIENTOS:**`, `**ACUERDO**`, `ACUERDA:`), a whole line, in capitals.
MARCADOR_DISPOSITIVO = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"
    r"(?:ACUERDOS?|ACUERDAN?|LINEAMIENTOS|EXPIDEN?|DISPONEN?|RESUELVEN?|DECRETA|BASES|REGLAS|"
    r"CRITERIOS|NORMAS|DISPOSICIONES|SE\s+EMITEN\s+LOS\s+SIGUIENTES\s+LINEAMIENTOS)"
    r"[ \t]*[.:]?[ \t]*(?:\*\*)?[ \t]*$",
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
#: and their counts over the 315 laws::
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


# --------------------------------------------------------------------- #291

#: A table row as `scjn.api` writes one since issue #253: a whole block that
#: opens with `|` and closes with `|` (`| 0101.21.01 | Reproductores de raza
#: pura. | Cbza | 10 | Ex. |`), matched against the block's stripped text. No
#: GFM header separator exists in the corpus (#253's decision: one row per
#: paragraph, no `|---|`). Measured on the latest snapshot of every instrument
#: (2026-10-14): leyes 31 instruments carry such rows (19,073 of them),
#: reglamentos 97 (2,126), lineamientos 22 (290), tratados 134 (18,083).
FILA_TABLA = re.compile(r"^\|.*\|$", re.DOTALL)

#: The one non-row block that does *not* end a table (rule 13): a lone,
#: all-caps, parenthesised label sitting between two rows -- `**(ARANCEL)**`,
#: which the LIGIE puts between the header row of every chapter's tariff and
#: its body (97 of the 97 tables of that law). Without it every one of those
#: tables would be cut into a one-row table holding the real column header and
#: a second one whose "first row" is a sub-header. Letters and spaces only, so
#: no reform annotation (`(REFORMADO, D.O.F. ...)`, which has a comma, a date
#: and a full stop) can match, and an annotation never joins a table. The
#: other blocks measured between two rows (`Sub-Sector:`, `Tabla B`, a
#: reform annotation) are *not* tolerated: they are a table's end.
ETIQUETA_TABLA = re.compile(r"^\*{0,2}\([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]{1,30}\)\*{0,2}$")
