"""Best-effort conversion of nota2md's own Markdown (as `legal_provisions()`
and `reconstruct_legal_provisions()` write it) into Akoma Ntoso XML — the
OASIS LegalDocML vocabulary for structured legal documents. This is the
"first attempt" mapping issue #91 (a continuation of #90's review of the
formal specification) asked for, not a complete implementation of the
standard: it covers the part of a DOF legal provision the spec review found
well-defined (preamble / article / Transitorios), not the full bibliographic
identification machinery (FRBRWork/Expression/Manifestation IRIs), which
issue #91 itself flags as unresolved — a DOF decree does not reliably carry
a clean "número" to build a real, resolvable IRI from.

Akoma Ntoso has no native element for "Transitorios" (nor "Considerandos");
this follows the convention issue #91 sketched for that gap: a plain
<section> carrying a `name` attribute that names its role, instead of
inventing a new element outside the standard's closed vocabulary.

Fracciones/incisos (markdown blocks like "**I.**"/"**a)**" inside an
article) are NOT nested into Akoma Ntoso's own <paragraph>/<point> hierarchy
yet — they are kept as sibling <p> paragraphs under the article's <content>,
same as any other block. Modeling that nesting is left for a later pass.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from nota2md.leyes import _segmenta_original

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


def _parrafos(texto_md: str) -> list[ET.Element]:
    return [_bloque_a_parrafo(b) for b in texto_md.split("\n\n") if b.strip()]


def _contenido(texto_md: str, *, es_articulo: bool = False) -> ET.Element:
    contenido = ET.Element("content")
    parrafos = _parrafos(texto_md)
    if es_articulo and parrafos:
        _quita_encabezado_articulo(parrafos[0])
    for p in parrafos:
        contenido.append(p)
    return contenido


def _eid(numero: str) -> str:
    """`numero` ("5", "5 Bis") as an Akoma Ntoso `eId` token — ASCII,
    whitespace turned into "_"."""
    return "art_" + re.sub(r"\s+", "_", numero.strip())


def _identification(subtipo: str, fecha: str | None, numero: str | None) -> ET.Element:
    """A best-effort <identification> block: FRBRcountry is always "mx"
    (this project only covers the federal DOF), but the IRI's "fecha" and
    "numero" segments fall back to a literal placeholder when not given —
    issue #91 found no reliable way to derive a DOF decree's "número" from
    the note alone, so this is NOT a resolvable IRI unless the caller
    supplies both.
    """
    fecha_iri = fecha or "sin-fecha"
    numero_iri = numero or "sin-numero"
    work_iri = f"/akn/mx/act/{subtipo}/{fecha_iri}/{numero_iri}"

    identification = ET.Element("identification", {"source": "#legalia"})
    work = ET.SubElement(identification, "FRBRWork")
    ET.SubElement(work, "FRBRthis", {"value": f"{work_iri}/main"})
    ET.SubElement(work, "FRBRuri", {"value": work_iri})
    if fecha:
        ET.SubElement(work, "FRBRdate", {"date": fecha, "name": "publication"})
    ET.SubElement(work, "FRBRcountry", {"value": "mx"})

    expression_iri = f"{work_iri}/spa@"
    expression = ET.SubElement(identification, "FRBRExpression")
    ET.SubElement(expression, "FRBRthis", {"value": f"{expression_iri}/main"})
    ET.SubElement(expression, "FRBRuri", {"value": expression_iri})
    ET.SubElement(expression, "FRBRlanguage", {"language": "spa"})

    manifestation_iri = f"{expression_iri}.xml"
    manifestation = ET.SubElement(identification, "FRBRManifestation")
    ET.SubElement(manifestation, "FRBRthis", {"value": f"{manifestation_iri}/main"})
    ET.SubElement(manifestation, "FRBRuri", {"value": manifestation_iri})
    return identification


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

    body = ET.SubElement(act, "body")
    if preambulo:
        preamble = ET.SubElement(body, "preamble")
        for p in _parrafos(preambulo):
            preamble.append(p)

    for numero_articulo, texto in articulos.items():
        article = ET.SubElement(body, "article", {"eId": _eid(numero_articulo)})
        ET.SubElement(article, "num").text = f"Artículo {numero_articulo}"
        article.append(_contenido(texto, es_articulo=True))

    if transitorios:
        section = ET.SubElement(body, "section", {"eId": "transitorios", "name": "transitorios"})
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
