#!/usr/bin/env python3
"""Rebuild `reconstruct_legal_provisions()`'s ground-truth fixtures from the
`scjn-leyes` release — `packages/nota2md/tests/fixtures/leyes/<abrev>.md` plus
`historial_44.json`, what `tests/test_leyes_44.py` reads.

    ./scripts/regenera_fixtures_leyes.py                     # report only
    ./scripts/regenera_fixtures_leyes.py --escribe

Issue #188 (Fase 3 of #184) replaced the previous fixtures, which were the
Cámara de Diputados' consolidated "texto vigente" PDF cleaned up by a module
(`nota2md.texto_vigente`) deleted along with them. The replacement is the
SCJN's own consolidated text of each law at its most recent reform, which the
release already publishes — so nothing is crawled or packaged here, only read
back.

**These fixtures are the one exception to "data is never committed to git"**
(see `CLAUDE.md`). They are ~3 MB of test fixtures, not a dataset: nothing
reads them but the test, and the point of freezing them is that a regression
test of a *replay algorithm* should not change its answer because a law was
reformed. This script exists so that freezing is reproducible instead of a
blob nobody can regenerate.

What it writes, per law:

`<abrev>.md`
    The newest snapshot of the law in the release — the consolidated text as
    it read right after its most recent reform — with the provenance header
    stripped and the SCJN's own editorial insertions removed paragraph by
    paragraph (`quita_notas_editoriales`, the "N. DE E." markers the SCJN
    adds and the DOF never published).

`historial_44.json`
    Per law, its `nombre` and the `codNota` of every reform up to and
    including that snapshot, oldest first — read off the law's own
    `indice.json`, which since issue #187 *is* the reform history. A law
    whose history the corpus cannot fully resolve (any snapshot left without
    a `codNota` even after the content-diff link) is **excluded from the
    fixture set and reported**: replaying a history with a hole in it would
    measure the hole, not the algorithm.

The `44` in the file names is historical — it was the number of files in the
directory when they were Diputados-derived — and is kept so the test module's
own name keeps matching it.
"""

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "packages" / "nota2md"))
sys.path.insert(0, str(RAIZ / "packages" / "scjn"))

from nota2md.scjn import download_scjn_leyes_corpus  # noqa: E402
from scjn.catalog import slug_instrumento  # noqa: E402
from scjn.text import quita_notas_editoriales  # noqa: E402

FIXTURES = RAIZ / "packages" / "nota2md" / "tests" / "fixtures" / "leyes"
ARCHIVO_HISTORIAL = "historial_44.json"


def _iso(fecha: str) -> str:
    """`DD-MM-YYYY` as ISO, so snapshots sort chronologically as strings."""
    return f"{fecha[6:10]}-{fecha[3:5]}-{fecha[0:2]}"


def cuerpo_de_snapshot(markdown: str) -> str:
    """The snapshot's own text, past the `---`-delimited provenance header
    `scjn.api.cabecera` writes at its top."""
    _, delimitador, resto = markdown.partition("\n---\n\n")
    return resto if delimitador else markdown.partition("\n\n")[2]


def limpia(markdown: str) -> str:
    """One snapshot as a fixture: header gone, every paragraph stripped of
    the SCJN's editorial insertions, blank paragraphs dropped."""
    parrafos = (quita_notas_editoriales(p.strip()) for p in cuerpo_de_snapshot(markdown).split("\n\n"))
    return "\n\n".join(p for p in parrafos if p) + "\n"


def historial_de(snapshots: list[dict]) -> list[int] | None:
    """Every snapshot's own `codNota`, oldest first, or None when any one of
    them has none — see the module docstring for why a partial history is
    not usable as a fixture.

    A snapshot linked by content diff rather than by title (issue #187's
    `title_link_status: "content_diff"`) already carries its `codNota` here;
    the `content_diff_confirmed_codNota` fallback below is for a release
    packaged before that change, where the confirmation was recorded but not
    promoted."""
    historial, usados = [], set()
    for snapshot in snapshots:
        cod = snapshot.get("codNota")
        if cod is None:
            confirmado = snapshot.get("content_diff_confirmed_codNota")
            if confirmado is not None and confirmado not in usados:
                cod = confirmado
        if cod is None:
            return None
        usados.add(cod)
        historial.append(cod)
    return historial


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--escribe", action="store_true",
        help="write the fixtures; without it nothing is touched and only the "
             "report is printed",
    )
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    previo = json.loads((FIXTURES / ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))

    historial: dict[str, dict] = {}
    excluidas: dict[str, list[str]] = {}
    for abrev in sorted(previo):
        corpus = download_scjn_leyes_corpus(
            slug_instrumento({"abrev": abrev}), timeout=args.timeout
        )
        snapshots = sorted(
            corpus["snapshots"],
            key=lambda s: (_iso(s.get("fecha_publicacion") or s["archivo"][:10]), s["archivo"]),
        )
        if not snapshots:
            excluidas[abrev] = ["sin snapshots en el release"]
            continue
        codigos = historial_de(snapshots)
        if codigos is None:
            excluidas[abrev] = [s.get("title_link_status") or "?" for s in snapshots]
            continue

        ultimo = snapshots[-1]
        historial[abrev] = {"nombre": previo[abrev]["nombre"], "historial": codigos}
        print(
            f"{abrev:<16} {len(snapshots)} reforma(s), ultima {ultimo['archivo']}",
            file=sys.stderr,
        )
        if args.escribe:
            (FIXTURES / f"{abrev}.md").write_text(limpia(ultimo["markdown"]), encoding="utf-8")

    for abrev, motivo in excluidas.items():
        print(f"EXCLUIDA {abrev}: {motivo}", file=sys.stderr)
        if args.escribe:
            (FIXTURES / f"{abrev}.md").unlink(missing_ok=True)

    if args.escribe:
        (FIXTURES / ARCHIVO_HISTORIAL).write_text(
            json.dumps(historial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"{len(historial)} ley(es) en el fixture, {len(excluidas)} excluida(s)"
        + ("" if args.escribe else "  (nada escrito: falta --escribe)"),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
