"""The curated fixtures (issue #162).

Each fixture is a pair — `NAME.md` and `NAME.json` — and the comparison is
over the **whole** serialized tree, not over selected fields. That is the
point: a regression that quietly reorders the hierarchy, renumbers an eId or
moves a paragraph one level up has to fail here, and it only does if nothing
is exempt from the comparison.

The `.md` files are *trimmed* excerpts of real laws, never whole ones. Whole
files reach 1.89 MB and this repository does not version data; a few dozen
lines is a test case.

To regenerate the expected trees after an intentional change:

    python -m tests.test_fixtures --write     # from packages/md2akn
"""

import json
from pathlib import Path

import pytest

from md2akn import parse_markdown, validate

FIXTURES = Path(__file__).parent / "fixtures"
NOMBRES = sorted(p.stem for p in FIXTURES.glob("*.md"))


def serializa(nodo) -> dict:
    """The whole tree as plain data — every field a regression could move."""
    return {
        "akn_type": nodo.akn_type,
        "eId": nodo.eId,
        "num": nodo.num,
        "heading": nodo.heading,
        "refers_to": nodo.refers_to,
        "is_chapeau": nodo.is_chapeau,
        "is_tail": nodo.is_tail,
        "start": nodo.start_char,
        "end": nodo.end_char,
        "notes": [
            {
                "action": a.action,
                "scope": a.scope,
                "date": a.date.isoformat() if a.date else None,
                "source": a.source,
                "raw": a.raw,
            }
            for a in nodo.notes
        ],
        "children": [serializa(h) for h in nodo.children],
    }


def arbol(nombre: str) -> dict:
    """The fixture's expected file, whole: the tree plus its coverage.

    Coverage is pinned here rather than asserted flat at 100% because one
    fixture is legitimately below it — `annotations.md` ends with an
    annotation that has no node after it to describe, so the `act` holds it
    and those characters are, correctly, unplaced. Writing the number into
    the fixture makes that a recorded fact instead of a loosened assertion.
    """
    texto = (FIXTURES / f"{nombre}.md").read_text(encoding="utf-8")
    act = parse_markdown(texto)
    rep = validate(act, texto=texto)
    return {"coverage": round(rep.cobertura, 4), "tree": serializa(act)}


@pytest.mark.parametrize("nombre", NOMBRES)
def test_el_arbol_completo(nombre):
    esperado = json.loads((FIXTURES / f"{nombre}.json").read_text(encoding="utf-8"))
    assert arbol(nombre) == esperado


@pytest.mark.parametrize("nombre", NOMBRES)
def test_las_invariantes_se_cumplen(nombre):
    texto = (FIXTURES / f"{nombre}.md").read_text(encoding="utf-8")
    rep = validate(parse_markdown(texto), texto=texto)
    assert rep.violations == []


def test_hay_fixtures():
    # A glob that silently matches nothing would make every test above vanish
    # and the file still pass.
    assert len(NOMBRES) >= 4


if __name__ == "__main__":  # pragma: no cover
    import sys

    if "--write" not in sys.argv:
        raise SystemExit("usage: python -m tests.test_fixtures --write")
    for nombre in NOMBRES:
        destino = FIXTURES / f"{nombre}.json"
        destino.write_text(
            json.dumps(arbol(nombre), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("wrote", destino)
