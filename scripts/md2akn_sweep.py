"""Run `md2akn` over the whole SCJN corpus and report where it still falls short.

This is not a test. It does not pass or fail: it is the instrument that says
which laws are the work to do next, by sorting them worst-metric-first. Each
new pattern that gets supported as a result becomes a curated fixture under
`packages/md2akn/tests/fixtures/`, and *those* run in CI. The sweep does not,
because it needs data that is deliberately not in this repository.

It lives in `scripts/` rather than in the package for the same reason. The
file-selection rule below — *per law directory, the most recent `DD-MM-YYYY.md`
with no `-2`/`-3` suffix* — is a fact about how the SCJN corpus is laid out on
disk, not about Markdown, and `md2akn` is a package that reads a file.

    python scripts/md2akn_sweep.py                    # the default corpus
    python scripts/md2akn_sweep.py --corpus DIR --out DIR

The report goes to `scripts/md2akn-sweep/` (git-ignored, like every other
data directory here): `por-ley.csv`, `resumen.json`, and `peores.txt`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "md2akn"))

from md2akn import parse_markdown, validate  # noqa: E402

CORPUS = Path("scripts/scjn/leyes")
SALIDA = Path("scripts/md2akn-sweep")

#: `DD-MM-YYYY.md` and nothing else. The `-2`/`-3` suffixes are the SCJN's own
#: marker for a second text published the same day; the unsuffixed file is the
#: law, so the pattern simply refuses to match them.
RE_INSTANTANEA = re.compile(r"^(\d{2})-(\d{2})-(\d{4})\.md$")

#: Article numbers that are not numbers. A law's articles are numbered `1`,
#: `1o.`, `1 Bis`, `27-A`; the sweep only reports *gaps*, so anything it
#: cannot read as an integer is skipped rather than guessed at.
RE_ENTERO = re.compile(r"^\s*(\d+)")


def latest_snapshots(root: Path = CORPUS):
    """`(slug, path)` per law directory: its most recent unsuffixed snapshot."""
    for directorio in sorted(root.iterdir()):
        if not directorio.is_dir():
            continue
        candidatos = []
        for archivo in directorio.glob("*.md"):
            m = RE_INSTANTANEA.match(archivo.name)
            if m:
                d, mo, y = (int(x) for x in m.groups())
                candidatos.append((dt.date(y, mo, d), archivo))
        if candidatos:
            yield directorio.name, max(candidatos)[1]


def _saltos(nums: list[str]) -> int:
    """How many integers are missing from an article sequence.

    A gap is the sign of a heading the segmenter did not recognize. It is a
    weak signal on its own — laws legitimately repeal articles and leave the
    numbering with holes — which is why it is reported next to the other
    metrics rather than on its own.
    """
    enteros = []
    for num in nums:
        m = RE_ENTERO.match(num or "")
        if m:
            enteros.append(int(m.group(1)))
    if len(enteros) < 2:
        return 0
    faltan = 0
    for antes, despues in zip(enteros, enteros[1:]):
        if despues > antes + 1:
            faltan += despues - antes - 1
    return faltan


def mide(slug: str, path: Path) -> dict:
    """Every per-law metric of issue #162, for one law."""
    texto = path.read_text(encoding="utf-8")
    inicio = time.perf_counter()
    act = parse_markdown(texto)
    segundos = time.perf_counter() - inicio

    rep = validate(act, texto=texto)
    tipos = Counter()
    articulos, anotaciones, sin_accion = [], 0, 0
    # A duplicate is only a duplicate among *siblings*. A law legitimately has
    # an "ARTICULO PRIMERO" in its transitorios and another in every later
    # reform's transitorios; counting those document-wide reported 7,631
    # duplicates for a corpus that has an order of magnitude fewer real ones.
    por_padre: Counter[tuple[int, str]] = Counter()
    for nodo in act.walk():
        tipos[nodo.akn_type] += 1
        if nodo.akn_type == "article":
            articulos.append(nodo.num)
            por_padre[(id(nodo.parent), (nodo.num or "").strip().upper())] += 1
        for a in nodo.notes:
            anotaciones += 1
            sin_accion += a.action is None
    for a in act.notes:
        anotaciones += 1
        sin_accion += a.action is None

    duplicados = sum(c - 1 for c in por_padre.values() if c > 1)
    transitorios = sum(
        1 for n in act.walk() if (n.refers_to or "").endswith("transitorios")
    )
    return {
        "slug": slug,
        "archivo": path.name,
        "bytes": len(texto.encode("utf-8")),
        "cobertura": round(rep.cobertura, 3),
        "violaciones": len(rep.violations),
        "reglas_rotas": ",".join(sorted({v.rule for v in rep.violations})),
        "articulos": len(articulos),
        "articulos_duplicados": duplicados,
        "saltos_de_numeracion": _saltos(articulos),
        "fracciones": tipos["paragraph"],
        "incisos": tipos["point"],
        "contenedores": sum(
            tipos[t] for t in ("book", "title", "chapter", "section", "level")
        ),
        "transitorios": transitorios,
        "anotaciones": anotaciones,
        "anotaciones_sin_accion": sin_accion,
        "segundos": round(segundos, 3),
        "bytes_por_segundo": int(len(texto.encode("utf-8")) / segundos) if segundos else 0,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corpus", type=Path, default=CORPUS,
                   help=f"the SCJN corpus directory (default: {CORPUS})")
    p.add_argument("--out", type=Path, default=SALIDA,
                   help=f"where the report goes (default: {SALIDA})")
    p.add_argument("--limit", type=int, default=None,
                   help="only the first N laws, for a quick look")
    args = p.parse_args(argv)

    if not args.corpus.is_dir():
        p.error(
            f"{args.corpus} does not exist. The corpus is not in the repository; "
            "fetch it with `nota2md download federal-laws` and extract it there."
        )

    leyes = list(latest_snapshots(args.corpus))[: args.limit]
    filas, fallos = [], []
    for i, (slug, path) in enumerate(leyes, 1):
        try:
            filas.append(mide(slug, path))
        except Exception as exc:                          # noqa: BLE001
            # A law that cannot be parsed at all is the loudest possible
            # finding, so it is recorded rather than allowed to stop the run.
            fallos.append({"slug": slug, "archivo": path.name, "error": repr(exc)})
        print(f"\r{i}/{len(leyes)} {slug:<24}", end="", file=sys.stderr)
    print(file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    if filas:
        with (args.out / "por-ley.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(filas[0]))
            w.writeheader()
            w.writerows(filas)

    n = len(filas)
    coberturas = sorted(f["cobertura"] for f in filas)
    resumen = {
        "leyes": len(leyes),
        "medidas": n,
        "fallidas": fallos,
        "cobertura_minima": coberturas[0] if n else None,
        "cobertura_mediana": coberturas[n // 2] if n else None,
        "cobertura_media": round(sum(coberturas) / n, 3) if n else None,
        "leyes_con_cobertura_>=99": sum(c >= 99 for c in coberturas),
        "leyes_con_violaciones": sum(f["violaciones"] > 0 for f in filas),
        "leyes_sin_articulos": sum(f["articulos"] == 0 for f in filas),
        "leyes_sin_transitorios": sum(f["transitorios"] == 0 for f in filas),
        "articulos": sum(f["articulos"] for f in filas),
        "articulos_duplicados": sum(f["articulos_duplicados"] for f in filas),
        "fracciones": sum(f["fracciones"] for f in filas),
        "incisos": sum(f["incisos"] for f in filas),
        "anotaciones": sum(f["anotaciones"] for f in filas),
        "anotaciones_sin_accion": sum(f["anotaciones_sin_accion"] for f in filas),
        "bytes": sum(f["bytes"] for f in filas),
        "segundos": round(sum(f["segundos"] for f in filas), 2),
        "bytes_por_segundo": int(
            sum(f["bytes"] for f in filas) / sum(f["segundos"] for f in filas)
        ) if n else None,
        "peor_caso": max(filas, key=lambda f: f["bytes"], default=None),
    }
    (args.out / "resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Worst-metric-first: this ordering is the point of the whole script.
    peores = sorted(filas, key=lambda f: (-f["violaciones"], f["cobertura"]))
    (args.out / "peores.txt").write_text(
        "\n".join(
            f"{f['slug']:<28} cov={f['cobertura']:7.3f}%  viol={f['violaciones']:<4} "
            f"art={f['articulos']:<5} dup={f['articulos_duplicados']:<4} "
            f"{f['reglas_rotas']}"
            for f in peores
        ),
        encoding="utf-8",
    )

    print(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(f"\nreport written to {args.out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
