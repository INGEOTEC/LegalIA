"""Best-effort conversion of nota2md's own Markdown (as `legal_provisions()`
and `reconstruct_legal_provisions()` write it) into Akoma Ntoso XML — the
OASIS LegalDocML vocabulary for structured legal documents. This is the
"first attempt" mapping issue #91 (a continuation of #90's review of the
formal specification) asked for, not a complete implementation of the
standard: it covers the part of a DOF legal provision the spec review found
well-defined (preamble / article / Transitorios), not the full bibliographic
identification machinery, which is still incomplete (see below).

Checked against the real thing, not just the prose spec: this module's
output is validated in `tests/test_akoma_ntoso.py` against akomantoso30.xsd,
the actual normative schema of the OASIS Standard (29 August 2018, vendored
in `tests/fixtures/akn/` — issue #91's original links pointed at an older,
pre-standard 2016 draft (csprd02) that even uses a different XML namespace;
the real one is `http://docs.oasis-open.org/legaldocml/ns/akn/3.0`, matching
what this module emits). Cross-checked against two of OASIS's own official
Spanish-language examples too (Uruguay, Chile).

That check corrected two assumptions from #91's own preliminary read of the
spec text:
- `<preamble>` is `<act>`'s own child, a sibling of `<body>` — NOT nested
  inside it. `<body>`'s content model only allows the hierarchical elements
  (article, section, ...), nothing else.
- **`fecha` (FRBRdate), not `número` (FRBRnumber), is the metadata issue #91
  should have worried about.** `FRBRWork`/`Expression`/`Manifestation` all
  require a `FRBRdate` (`coreProperties` in the schema — typed `xsd:date`,
  so no placeholder string can stand in for a missing one) and a
  `FRBRauthor`; `FRBRnumber` is genuinely optional. Without `fecha`, this
  module's output is NOT schema-valid — not just "not a resolvable IRI" as
  originally framed — see `test_invalido_sin_fecha`. `FRBRauthor` needs no
  parameter at all: the DOF is always the issuing authority, so it is filled
  in unconditionally (`href="#dof"`).

A second pass closed the three gaps that first schema check left open:
- Akoma Ntoso has no native element for "Transitorios" (nor
  "Considerandos"/"Vistos"); it is a plain <section refersTo="#transitorios">
  (not the `name` attribute #91 first guessed — `section`'s schema type
  declares no such attribute), matching the convention the official
  examples use for the same kind of gap. `refersTo`'s target is now
  actually declared, too: `<meta>/<references>` gets a matching
  `<TLCConcept eId="transitorios" .../>` whenever the note has a
  Transitorios section (see `_references`) — note that `eId` must be
  unique across the whole `<act>`, so the `<section>` itself is
  `eId="sec_transitorios"`, not "transitorios" again.
- Fracciones/incisos ("**I.**"/"**a)**" blocks inside an article) are now
  nested into Akoma Ntoso's own <paragraph>(fracción)/<point>(inciso)
  hierarchy instead of flattened into sibling <p>s — see
  `_cuerpo_articulo`. `hierarchy`'s content model is a strict choice
  between a flat <content> and nested hierarchy elements (never both), so
  an article either has one of the two, decided from whether it has any
  fracción at all. A DOF article can legally repeat the same fracción
  label under two separate "I. a X." lists (e.g. one for a body's
  composition, a later one for its members' eligibility) — `_eid_unico`
  disambiguates those instead of emitting a duplicate, schema-invalid eId.
- Settled "esp" vs. "spa" for Spanish (`FRBRlanguage`, and the IRI's own
  language segment): "esp", matching the official Uruguayan example, not
  the ISO 639-2 code this module used at first.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from nota2md.leyes import _analiza_bloque, _segmenta_original

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A markdown block's own "**Artículo N[.]**" lead-in, already promoted to
# <num> by the caller — stripped from the article's first paragraph so it
# is not duplicated. Mirrors leyes._ARTICULO's suffix handling.
_ENCABEZADO_ARTICULO = re.compile(
    r"^Art[íi]culo\s+\d+\s*(?:o\b\.?|[°º])?\s*"
    r"(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies)?\s*[.\-:]{0,2}$",
    re.I,
)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# A block's own Markdown heading marker ("## Al margen un sello.") — dropped
# before building its <p>, since Akoma Ntoso's <preamble>/transitorios
# <section> carry that as plain paragraph text (or, for "Transitorios"
# itself, as the <section>'s own <heading> instead — see
# `markdown_to_akoma_ntoso`).
_ENCABEZADO_MARKDOWN = re.compile(r"^#+\s*")


def _bloque_a_parrafo(texto_md: str) -> ET.Element:
    """A markdown block turned into one Akoma Ntoso <p>, with **bold** spans
    (the only inline markup nota2md's own Markdown output uses) becoming
    <b> children instead of being flattened away."""
    texto_md = _ENCABEZADO_MARKDOWN.sub("", texto_md, count=1)
    p = ET.Element("p")
    partes = _BOLD.split(texto_md)
    ultimo = None
    for i, parte in enumerate(partes):
        if i % 2 == 0:
            if not parte:
                continue
            if ultimo is None:
                p.text = (p.text or "") + parte
            else:
                ultimo.tail = (ultimo.tail or "") + parte
        else:
            b = ET.SubElement(p, "b")
            b.text = parte
            ultimo = b
    return p


def _quita_encabezado_articulo(p: ET.Element) -> None:
    """Drop `p`'s leading "**Artículo N.**" bold run in place — it is already
    represented by the <article>'s own <num> — along with whatever
    punctuation is left dangling right after it ("." / ".-")."""
    hijos = list(p)
    if not hijos or hijos[0].tag != "b":
        return
    if not _ENCABEZADO_ARTICULO.match((hijos[0].text or "").strip()):
        return
    cola = hijos[0].tail or ""
    p.remove(hijos[0])
    p.text = re.sub(r"^\s*[.\-:]{0,2}\s*", "", cola)


def _quita_etiqueta_bloque(p: ET.Element, etiqueta: str) -> None:
    """Same idea as `_quita_encabezado_articulo`, but for a fracción/inciso
    label ("**I.**"/"**a)**") instead of an article's "Artículo N" — it is
    already represented by the <paragraph>/<point>'s own <num>."""
    hijos = list(p)
    if not hijos or hijos[0].tag != "b":
        return
    if (hijos[0].text or "").strip().rstrip(".)-").strip().lower() != etiqueta.lower():
        return
    cola = hijos[0].tail or ""
    p.remove(hijos[0])
    p.text = re.sub(r"^\s*[.\-:)]{0,2}\s*", "", cola)


def _parrafos(texto_md: str) -> list[ET.Element]:
    return [_bloque_a_parrafo(b) for b in texto_md.split("\n\n") if b.strip()]


def _contenido(texto_md: str) -> ET.Element:
    contenido = ET.Element("content")
    for p in _parrafos(texto_md):
        contenido.append(p)
    return contenido


def _eid(numero: str) -> str:
    """`numero` ("5", "5 Bis") as an Akoma Ntoso `eId` token — ASCII,
    whitespace turned into "_"."""
    return "art_" + re.sub(r"\s+", "_", numero.strip())


def _eid_hijo(eid_padre: str, etiqueta: str) -> str:
    """A nested fracción/inciso's own `eId`, scoped under its parent's."""
    return eid_padre + "__" + re.sub(r"\s+", "_", etiqueta.strip())


def _eid_unico(propuesto: str, usados: set[str]) -> str:
    """`propuesto`, or `propuesto` with a numeric suffix appended until it is
    not already in `usados` (which gains whatever is returned) — eId must be
    unique across the whole <act>, but a DOF article can restate the same
    fracción/inciso label more than once (see `_cuerpo_articulo`)."""
    candidato = propuesto
    i = 2
    while candidato in usados:
        candidato = f"{propuesto}_{i}"
        i += 1
    usados.add(candidato)
    return candidato


def _agrupa_bloques(bloques_md: list[str]) -> tuple[list[ET.Element], list[dict], list[ET.Element]]:
    """`bloques_md` (an article's own blocks, header already excluded)
    grouped into (intro, fracciones, wrapUp): plain paragraphs before the
    first fracción are `intro`, plain paragraphs after the last one are
    `wrapUp` (Akoma Ntoso's own names for "leads into"/"wraps up" the
    nested elements in between — see `intro`/`wrapUp` in akomantoso30.xsd),
    and each fracción is `{"etiqueta", "p", "incisos": [(etiqueta, p), ...]}`.
    An inciso block with no fracción open yet (should not happen in real DOF
    text — incisos are always introduced by a fracción) falls back to a
    plain paragraph rather than being dropped.
    """
    intro: list[ET.Element] = []
    fracciones: list[dict] = []
    wrapup: list[ET.Element] = []
    for bloque in bloques_md:
        tipo, etiqueta, _ = _analiza_bloque(bloque)
        p = _bloque_a_parrafo(bloque)
        if tipo == "num":
            _quita_etiqueta_bloque(p, etiqueta)
            fracciones.append({"etiqueta": etiqueta, "p": p, "incisos": []})
        elif tipo == "letra" and fracciones:
            _quita_etiqueta_bloque(p, etiqueta)
            fracciones[-1]["incisos"].append((etiqueta, p))
        elif fracciones:
            wrapup.append(p)
        else:
            intro.append(p)
    return intro, fracciones, wrapup


def _cuerpo_articulo(article: ET.Element, texto_md: str) -> None:
    """Append this article's body to it: a flat <content> when its text has
    no fracciones, or <intro>/<paragraph>(fracción, itself possibly holding
    <point>s for its own incisos)/<wrapUp> when it does. Akoma Ntoso's
    `hierarchy` type is a strict choice between the two — nested hierarchy
    elements and a flat <content> never mix at the same level — so which
    branch to use is decided once, from whether any fracción was found at
    all, not block by block.
    """
    bloques = [b for b in texto_md.split("\n\n") if b.strip()]
    if not bloques:
        return

    primero = _bloque_a_parrafo(bloques[0])
    _quita_encabezado_articulo(primero)
    intro, fracciones, wrapup = _agrupa_bloques(bloques[1:])
    intro = [primero] + intro

    if not fracciones:
        contenido = ET.SubElement(article, "content")
        for p in intro:
            contenido.append(p)
        return

    intro_el = ET.SubElement(article, "intro")
    for p in intro:
        intro_el.append(p)

    eid_articulo = article.get("eId")
    # A DOF article can restate the same fracción label twice under two
    # separate "I. a X." lists (e.g. one for a body's own composition, a
    # later one for its members' eligibility) — eId must still be unique
    # across the whole <act>, so disambiguate within this one article.
    usados: set[str] = set()
    for fraccion in fracciones:
        eid_fraccion = _eid_unico(_eid_hijo(eid_articulo, fraccion["etiqueta"]), usados)
        paragraph = ET.SubElement(article, "paragraph", {"eId": eid_fraccion})
        ET.SubElement(paragraph, "num").text = f"{fraccion['etiqueta']}."
        if fraccion["incisos"]:
            paragraph_intro = ET.SubElement(paragraph, "intro")
            paragraph_intro.append(fraccion["p"])
            for etiqueta_inciso, p_inciso in fraccion["incisos"]:
                eid_inciso = _eid_unico(_eid_hijo(eid_fraccion, etiqueta_inciso), usados)
                point = ET.SubElement(paragraph, "point", {"eId": eid_inciso})
                ET.SubElement(point, "num").text = f"{etiqueta_inciso})"
                ET.SubElement(point, "content").append(p_inciso)
        else:
            ET.SubElement(paragraph, "content").append(fraccion["p"])

    if wrapup:
        wrapup_el = ET.SubElement(article, "wrapUp")
        for p in wrapup:
            wrapup_el.append(p)


def _coreProperties(elemento: ET.Element, this_iri: str, uri: str, fecha: str | None) -> None:
    """FRBRthis/FRBRuri/FRBRdate/FRBRauthor, in the exact order and
    mandatory-ness `coreProperties` requires at every FRBR level (Work,
    Expression and Manifestation alike) — see akomantoso30.xsd. FRBRdate is
    genuinely required there (and typed `xsd:date`, so no placeholder string
    can stand in for a missing one): a document built without `fecha` is
    NOT schema-valid, full stop, unlike FRBRnumber below (optional). FRBRauthor
    is required too, but does not depend on anything nota2md's Markdown
    itself carries — the DOF is always the issuing authority — so it is
    filled in unconditionally.
    """
    ET.SubElement(elemento, "FRBRthis", {"value": this_iri})
    ET.SubElement(elemento, "FRBRuri", {"value": uri})
    if fecha:
        ET.SubElement(elemento, "FRBRdate", {"date": fecha, "name": "publication"})
    ET.SubElement(elemento, "FRBRauthor", {"href": "#dof"})


def _identification(subtipo: str, fecha: str | None, numero: str | None) -> ET.Element:
    """A best-effort <identification> block. The IRI's "fecha" and "numero"
    segments fall back to a literal, non-resolvable placeholder when not
    given — issue #91 found no reliable way to derive a DOF decree's
    "número" from the note alone. Per the real schema this placeholder is
    harmless for `numero` (FRBRnumber is optional there) but not for
    `fecha` — see `_coreProperties` for why that one genuinely blocks
    schema validity, not just IRI resolvability.
    """
    fecha_iri = fecha or "sin-fecha"
    numero_iri = numero or "sin-numero"
    work_iri = f"/akn/mx/act/{subtipo}/{fecha_iri}/{numero_iri}"

    identification = ET.Element("identification", {"source": "#legalia"})
    work = ET.SubElement(identification, "FRBRWork")
    _coreProperties(work, f"{work_iri}/!main", work_iri, fecha)
    ET.SubElement(work, "FRBRcountry", {"value": "mx"})
    if numero:
        ET.SubElement(work, "FRBRnumber", {"value": numero})

    # "esp", not the ISO 639-2 code "spa" — matching the convention OASIS's
    # own official Uruguayan example uses for Spanish (issue #91 flagged the
    # two as an open question; this project settles on "esp").
    expression_iri = f"{work_iri}/esp@"
    expression = ET.SubElement(identification, "FRBRExpression")
    _coreProperties(expression, f"{expression_iri}/!main", expression_iri, fecha)
    ET.SubElement(expression, "FRBRlanguage", {"language": "esp"})

    manifestation_iri = f"{expression_iri}.xml"
    manifestation = ET.SubElement(identification, "FRBRManifestation")
    _coreProperties(manifestation, f"{manifestation_iri}/!main", manifestation_iri, fecha)
    return identification


def _references(conceptos: dict[str, str]) -> ET.Element:
    """<meta>'s <references> block: one <TLCConcept> per {eId: showAs} in
    `conceptos` — the schema-sanctioned way to declare what a `refersTo`
    target (e.g. "#transitorios") actually stands for, matching the
    convention OASIS's own official examples use (a local ontology-style
    href under "/akn/ontology/concepts/...", not a real, resolvable one —
    same placeholder spirit as FRBRauthor's "#dof").
    """
    references = ET.Element("references", {"source": "#legalia"})
    for eid, show_as in conceptos.items():
        ET.SubElement(
            references,
            "TLCConcept",
            {"eId": eid, "href": f"/akn/ontology/concepts/mx/{eid}", "showAs": show_as},
        )
    return references


def markdown_to_akoma_ntoso(
    md_path: str | Path,
    outdir: str | Path,
    *,
    subtipo: str = "decreto",
    fecha: str | None = None,
    numero: str | None = None,
    nombre_ley: str | None = None,
) -> Path:
    """Convert the Markdown at `md_path` (nota2md's own output shape — a
    `nota-{codNota}.md` from legal_provisions() or a `ley-{codNota}.md` from
    reconstruct_legal_provisions()) into an Akoma Ntoso <act>, written to
    ``outdir/{md_path.stem}.akn.xml``; returns that path — the same shape as
    legal_provisions()/reconstruct_legal_provisions().

    `subtipo` names the IRI's document subtype (e.g. "decreto", "ley"; see
    issue #91's Naming Convention findings) and the <act>'s own `name`
    attribute. `fecha` ("YYYY-MM-DD") and `numero` feed the FRBRWork IRI when
    known; left as None, the IRI is a structural placeholder, not a
    resolvable identifier (see `_identification`'s own docstring for why).
    `nombre_ley` picks which instrument's segment to convert when `md_path`
    holds a decree that touches more than one law, same as
    reconstruct_legal_provisions()'s own parameter.
    """
    md_path = Path(md_path)
    markdown = md_path.read_text(encoding="utf-8")
    preambulo, articulos, transitorios = _segmenta_original(markdown, nombre_ley)

    akoma_ntoso = ET.Element("akomaNtoso", {"xmlns": AKN_NS})
    act = ET.SubElement(akoma_ntoso, "act", {"name": subtipo})

    meta = ET.SubElement(act, "meta")
    meta.append(_identification(subtipo, fecha, numero))
    if transitorios:
        # <references> must declare the "#transitorios" refersTo target
        # used below, the way OASIS's own examples declare theirs — a bare
        # refersTo with nothing behind it is schema-valid too (referenceRef
        # is not integrity-checked at the XSD level), but incomplete.
        meta.append(_references({"transitorios": "Transitorios"}))

    # <preamble> is <act>'s own child, a sibling of <body> — NOT nested
    # inside it (hierarchicalStructure's sequence is meta, ..., preamble?,
    # body, ...); bodyType's content model only allows the hierarchical
    # elements (article, section, ...), nothing else.
    if preambulo:
        preamble = ET.SubElement(act, "preamble")
        for p in _parrafos(preambulo):
            preamble.append(p)

    body = ET.SubElement(act, "body")
    for numero_articulo, texto in articulos.items():
        article = ET.SubElement(body, "article", {"eId": _eid(numero_articulo)})
        ET.SubElement(article, "num").text = f"Artículo {numero_articulo}"
        _cuerpo_articulo(article, texto)

    if transitorios:
        # `section`'s schema type (`hierarchy`) has no `name` attribute —
        # only `refersTo` (an IRI into <meta>/<references>, not enforced at
        # the XSD level) is the schema-sanctioned way to say what a generic
        # container stands for, so that is what marks this as Transitorios.
        # eId must be unique across the whole <act> (the schema enforces
        # this), so this can't reuse "transitorios" too — that belongs to
        # the <TLCConcept> above, which is what refersTo points at.
        section = ET.SubElement(body, "section", {"eId": "sec_transitorios", "refersTo": "#transitorios"})
        ET.SubElement(section, "heading").text = "Transitorios"
        # transitorios' own first block is the "Transitorios" heading itself
        # (see _segmenta_original) — already covered by <heading> above.
        _, _, cuerpo = transitorios.partition("\n\n")
        if cuerpo:
            section.append(_contenido(cuerpo))

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"{md_path.stem}.akn.xml"
    ET.indent(akoma_ntoso, space="  ")
    ET.ElementTree(akoma_ntoso).write(dest, encoding="unicode", xml_declaration=True)
    return dest
