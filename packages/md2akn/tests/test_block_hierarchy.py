"""Rule 33 (review fix-5 of #291): an API block is the root of a hierarchy.

The tratados 11293 (the SOLAS convention and its fifteen amending resolutions)
prints each resolution as one API block, with its recitals, an `ANEXO` and the
chapters and parts of that annex all in the block's own text. Everything a block
prints belongs to it and ends with it, and the headings keep their order among
themselves: the annex holds its chapters, a chapter holds its parts, the next
marker starts a new hierarchy at the same level as the last one.

The fragments are the real headings of three resolutions of 11293's snapshot
`27-04-2026` (MSC.404(96), MSC.409(97) and MSC.421(98)), body text cut down to a
line. The review of the Instruments page found: an `ANEXO` that was an empty node
with its chapters as siblings, a chapter's parts that were its siblings (so the
next chapter's `PARTE A` came out `part_A_2`), a resolution that was an
`hcontainer` in one block and a `section` in the next, and the annex of one of
them hanging from the body.
"""

import re

from md2akn import parse_markdown
from md2akn.units import coverage, text_units


def _marker(orden, label, kind="other"):
    return f'<!-- scjn:block orden={orden} numero=0 kind={kind} label="{label}" -->'


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _resolution(orden, name, date, chapters, with_appendix=False, bold_parts=True):
    """One `RESOLUCIÓN` block as the API serves it: heading, recitals, operative
    paragraphs, an `ANEXO` and the chapters (each `(heading, [parts])`) it prints."""
    blocks = [
        _marker(orden, name),
        f"**{name}**",
        f"(adoptada el {date})",
        "**ENMIENDAS AL CONVENIO INTERNACIONAL PARA LA SEGURIDAD DE LA VIDA HUMANA EN EL MAR, 1974, ENMENDADO**",
        "**EL COMITÉ DE SEGURIDAD MARÍTIMA,**",
        "RECORDANDO el artículo 28 b) del Convenio constitutivo de la Organización Marítima Internacional.",
        "1 ADOPTA, de conformidad con lo dispuesto en el artículo VIII b) iv) del Convenio, las enmiendas.",
        "**ANEXO**",
        "**ENMIENDAS AL CONVENIO INTERNACIONAL PARA LA SEGURIDAD DE LA VIDA HUMANA EN EL MAR, 1974, ENMENDADO**",
    ]
    for chapter, parts in chapters:
        blocks += [f"**CAPÍTULO {chapter}**", f"**DISPOSITIVOS DEL CAPÍTULO {chapter}**"]
        if not parts:
            blocks.append("4 Se añade la siguiente regla nueva 2-1 a continuación de la regla 2 actual:")
        for part in parts:
            blocks += [f"**PARTE {part}**" if bold_parts else f"Parte {part}",
                       "Generalidades", "Regla 1", f"Texto de la regla en la parte {part}."]
    if with_appendix:
        blocks += ["**APÉNDICE**", "**CERTIFICADOS**", "Texto del apéndice."]
    return blocks


def _doc():
    blocks = [
        _marker(1, "ENCABEZADO", "header"), "**ACUERDO DE PUBLICACIÓN**",
        _marker(2, "PARTE FINAL DEL ACUERDO", "other"), "Dado en la Ciudad de México.",
    ]
    blocks += _resolution(3, "RESOLUCIÓN MSC.404(96)", "19 de mayo de 2016",
                          [("II-2", ["A", "D", "G"]), ("III", ["A", "B"])])
    # no article-like line at all in this block's own text
    blocks += _resolution(4, "RESOLUCIÓN MSC.409(97)", "25 de noviembre de 2016",
                          [("II-1", ["A"]), ("II-2", ["A", "C"]), ("XI-1", [])], bold_parts=False)
    blocks += _resolution(5, "RESOLUCIÓN MSC.421(98)", "15 de junio de 2017",
                          [("II-1", ["A", "B", "B-1"]), ("II-2", ["A", "C"]), ("III", ["A", "B"])],
                          with_appendix=True)
    return "\n\n".join(blocks) + "\n"


def _resolutions(tree):
    body = next(c for c in tree.children if c.akn_type == "body")
    return [c for c in body.children if c.num and c.num.startswith("RESOLUCIÓN")]


def _kids(node, akn_type):
    return [c.num for c in node.children if c.akn_type == akn_type]


def test_every_resolution_is_a_sibling_container_of_one_type():
    resolutions = _resolutions(parse_markdown(_doc()))
    assert [r.num for r in resolutions] == [
        "RESOLUCIÓN MSC.404(96)", "RESOLUCIÓN MSC.409(97)", "RESOLUCIÓN MSC.421(98)"]
    assert {r.akn_type for r in resolutions} == {"section"}
    assert [r.eId for r in resolutions] == [
        "sec_RESOLUCIÓN_MSC.404(96)", "sec_RESOLUCIÓN_MSC.409(97)", "sec_RESOLUCIÓN_MSC.421(98)"]


def test_nothing_of_a_block_is_at_the_body_level():
    tree = parse_markdown(_doc())
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [c.akn_type for c in body.children] == ["hcontainer", "section", "section", "section"]


def test_the_annex_holds_the_chapters_and_the_chapter_its_parts():
    first, second, third = _resolutions(parse_markdown(_doc()))
    for resolution, chapters in (
        (first, ["II-2", "III"]), (second, ["II-1", "II-2", "XI-1"]), (third, ["II-1", "II-2", "III"]),
    ):
        # the introduction, then one annex: nothing else hangs from the resolution
        assert [c.akn_type for c in resolution.children] == ["intro", "attachment"]
        annex = resolution.children[1]
        assert annex.refers_to == "#anexo"
        assert _kids(annex, "chapter") == chapters
        assert not _kids(annex, "part")
    annex = third.children[1]
    by_chapter = {c.num: _kids(c, "part") for c in annex.children if c.akn_type == "chapter"}
    assert by_chapter == {"II-1": ["A", "B", "B-1"], "II-2": ["A", "C"], "III": ["A", "B"]}
    second_annex = second.children[1]
    parts = {c.num: _kids(c, "part") for c in second_annex.children if c.akn_type == "chapter"}
    assert parts == {"II-1": ["A"], "II-2": ["A", "C"], "XI-1": []}


def test_an_appendix_of_the_block_is_inside_its_annex_after_the_chapters():
    third = _resolutions(parse_markdown(_doc()))[2]
    annex = third.children[1]
    assert [c.akn_type for c in annex.children] == ["chapter", "chapter", "chapter", "attachment"]
    appendix = annex.children[-1]
    assert appendix.eId.endswith("__app_attachment")


def test_no_part_or_annex_needs_a_numeric_suffix():
    eids = [n.eId for n in _walk(parse_markdown(_doc()))]
    assert len(eids) == len(set(eids))
    assert not [e for e in eids if re.search(r"__(?:part|att|app)_[^_]*_\d+$", e)]


def test_an_annex_heading_is_not_loose_text_and_has_its_title():
    tree = parse_markdown(_doc())
    units = text_units(tree, split_articles=False)
    assert "loose" not in {u.unit_type for u in units}
    annex = _resolutions(tree)[0].children[1]
    assert annex.heading is not None and annex.heading.startswith("ENMIENDAS AL CONVENIO")
    assert coverage(tree, units).uncovered_chars == 0


def test_an_untitled_annex_is_still_a_container_of_what_follows():
    doc = "\n\n".join([
        _marker(1, "RESOLUCIÓN MSC.1(1)"), "**RESOLUCIÓN MSC.1(1)**", "RECORDANDO algo.",
        "**ANEXO**", "**CAPÍTULO I**", "**DISPOSITIVOS**", "**PARTE A**", "Regla 1", "Texto.",
        "**CAPÍTULO II**", "**OTRO**", "**PARTE A**", "Regla 2", "Texto.",
    ]) + "\n"
    resolution = _resolutions(parse_markdown(doc))[0]
    annex = resolution.children[-1]
    assert annex.akn_type == "attachment"
    assert _kids(annex, "chapter") == ["I", "II"]
    assert all(_kids(c, "part") == ["A"] for c in annex.children)


def test_the_next_marker_closes_everything_the_block_opened():
    doc = "\n\n".join([
        _marker(1, "RESOLUCIÓN MSC.1(1)"), "**RESOLUCIÓN MSC.1(1)**", "RECORDANDO algo.",
        "**ANEXO**", "**CAPÍTULO I**", "**DISPOSITIVOS**", "**PARTE A**", "Regla 1", "Texto.",
        _marker(2, "RESOLUCIÓN MSC.2(2)"), "**RESOLUCIÓN MSC.2(2)**", "RECORDANDO algo.",
        "**ANEXO**", "**CAPÍTULO I**", "**DISPOSITIVOS**", "**PARTE A**", "Regla 1", "Texto.",
    ]) + "\n"
    tree = parse_markdown(doc)
    one, two = _resolutions(tree)
    for r in (one, two):
        annex = r.children[-1]
        assert _kids(annex, "chapter") == ["I"]
        assert _kids(annex.children[0], "part") == ["A"] if annex.children[0].akn_type == "chapter" else True
    assert [n.eId for n in _walk(tree) if n.akn_type == "chapter"] == [
        "sec_RESOLUCIÓN_MSC.1(1)__att_attachment__cap_I", "sec_RESOLUCIÓN_MSC.2(2)__att_attachment__cap_I"]


def test_without_markers_nothing_is_a_labelled_block():
    stripped = "\n\n".join(b for b in _doc().split("\n\n") if not b.startswith("<!-- scjn:block"))
    tree = parse_markdown(stripped)
    assert not any(n.akn_type == "hcontainer" for n in _walk(tree))


def test_an_annex_that_follows_an_empty_annex_is_inside_it():
    """MSC.497(105) .. MSC.534(107) of 11293 print `ANEXO` / its title / `ANEXO` / the
    title of the annex to the Convention / `APÉNDICE`: the second is the first's own text,
    and the first is not left an empty node."""
    doc = "\n\n".join([
        _marker(1, "RESOLUCIÓN MSC.1(1)"), "**RESOLUCIÓN MSC.1(1)**", "RECORDANDO algo.",
        "**ANEXO**", "**ENMIENDAS AL PROTOCOLO DE 1988**",
        "**ANEXO**", "**MODIFICACIONES Y ADICIONES AL ANEXO DEL CONVENIO**",
        "**APÉNDICE**", "**MODIFICACIONES AL APÉNDICE**", "Texto del apéndice.",
    ]) + "\n"
    tree = parse_markdown(doc)
    resolution = _resolutions(tree)[0]
    first = resolution.children[-1]
    assert first.heading == "ENMIENDAS AL PROTOCOLO DE 1988"
    second = first.children[0]
    assert (second.akn_type, second.heading) == ("attachment", "MODIFICACIONES Y ADICIONES AL ANEXO DEL CONVENIO")
    assert [c.akn_type for c in second.children] == ["attachment"]
    assert second.children[0].refers_to == "#apendice"
    assert "loose" not in {u.unit_type for u in text_units(tree, split_articles=False)}


def test_an_annex_with_a_text_between_two_annexes_closes_the_first():
    doc = "\n\n".join([
        _marker(1, "RESOLUCIÓN MSC.1(1)"), "**RESOLUCIÓN MSC.1(1)**", "RECORDANDO algo.",
        "**ANEXO**", "**PRIMER ANEXO**", "Texto del primer anexo.",
        "**ANEXO**", "**SEGUNDO ANEXO**", "Texto del segundo anexo.",
    ]) + "\n"
    resolution = _resolutions(parse_markdown(doc))[0]
    assert [c.akn_type for c in resolution.children] == ["intro", "attachment", "attachment"]


def test_an_annex_with_nothing_structural_after_it_in_its_block_takes_the_blocks_that_follow():
    """The shape of the reglamento 83225 and of the gas carrier code (96707): the annex is
    the last thing the block prints, and the instrument it approves, or the code it amends, is
    what the API serves in the blocks after it -- so they belong to the annex, which leaves
    the block (rule 29, narrowed by rule 33)."""
    doc = "\n\n".join([
        _marker(1, "ACUERDO"), "**ACUERDO**", "Se aprueba el Reglamento.", "**ANEXO ÚNICO**",
        _marker(2, "REGLAMENTO DE LA MATERIA"), "Reglamento de la materia",
        _marker(3, "CAPÍTULO I", "container").replace("kind=container", "kind=container type=chapter num=\"I\""),
        "**CAPÍTULO I**", "**Artículo 1.-** Uno.",
    ]) + "\n"
    tree = parse_markdown(doc)
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [c.akn_type for c in body.children] == ["hcontainer", "attachment"]
    annex = body.children[1]
    assert annex.num == "ÚNICO"
    assert [c.akn_type for c in annex.children][-1] == "chapter"
