"""A vector release's assets, ready for a human to publish (issue #227's
Fase 4).

Turns one collection's finished work directory — `units.parquet`,
`leaves.parquet`, `corpus-manifest.json` and every model's merged
`runs/<model>/vectors/` — into the exact upload plan for its own GitHub
release, and stops there. **Nothing here publishes anything**: no
`gh release create`, no upload, no tag. That is issue #115's Hallazgo C,
which this issue does not get to override — a human reads `PUBLICAR.md` and
runs it.

Nothing is copied, either. A vector release is ~570 MB (0.6B) to ~1.4 GB
(4B) per corpus, so `parte-<n>.txt` lists the files where they already are
and `gh release upload` reads them from there; the only files written here
are the small ones a release needs and does not already have
(`vectors-manifest.json`, `SHA256SUMS.txt`, the part lists, `PUBLICAR.md`).

A release holds at most 1,000 assets (issue #223, hit publishing
`scjn-reglamentos`), so a collection over that is published as a numbered
series — `scjn-reglamentos-vectors`, `-2`, `-3` — and `legalvec` resolves the
series by probing GitHub until a part 404s. The release **body** is a file
checked into `.github/<tag>.md`, the pattern `.github/historial-legislativo.md`
set: the file *is* the body, so it is reviewed in a pull request rather than
typed into a web form.

    python scripts/embeddings/package_vectors.py --work-dir emb-run-leyes \\
        --coleccion leyes
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

#: GitHub's own cap on a release's asset count (issue #223).
MAX_ASSETS_POR_RELEASE = 1000

#: The tag series each collection's vectors are published under — the same
#: names `legalvec.cache.RELEASE_TAGS` reads them back by.
TAGS = {
    "leyes": "scjn-leyes-vectors",
    "reglamentos": "scjn-reglamentos-vectors",
    "lineamientos": "scjn-lineamientos-vectors",
}

#: GitHub's release-body cap (issue #223): `scjn-reglamentos`' 1,082-row
#: table was 179,749 bytes and did not fit, which is why a vectors body
#: summarises rather than enumerates.
LIMITE_CUERPO_NOTAS = 125_000

#: The assets that are not vectors. Part 1 carries all of them, so its own
#: vector budget is `MAX_ASSETS_POR_RELEASE` minus this many.
METADATOS = ("units.parquet", "leaves.parquet", "corpus-manifest.json",
             "vectors-manifest.json", "SHA256SUMS.txt")


def model_dirs(work_dir: Path) -> list[Path]:
    """Every model whose shards were merged in `work_dir`, by its own
    `runs/<model-slug>/vectors/` directory."""
    runs = work_dir / "runs"
    return sorted(p for p in runs.glob("*/vectors") if p.is_dir()) if runs.is_dir() else []


def vector_files(work_dir: Path) -> list[Path]:
    """Every per-instrument and shared vector file, across every model —
    what the release actually publishes, in a deterministic order."""
    archivos: list[Path] = []
    for directorio in model_dirs(work_dir):
        archivos += sorted(directorio.glob("vectors-*.parquet"))
    return archivos


def vectors_manifest(work_dir: Path) -> dict:
    """Both models' merge manifests in one file — what was run, at which `K`,
    with which pooling and dtype, and how many rows came out. One asset
    rather than one per model: it is a few hundred bytes and a reader who
    wants to know what produced a vector wants both halves."""
    modelos = {}
    for directorio in model_dirs(work_dir):
        manifiesto = directorio.parent / "manifest.json"
        if manifiesto.exists():
            modelos[directorio.parent.name] = json.loads(manifiesto.read_text(encoding="utf-8"))
    return {"models": modelos}


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _tag_de_parte(base: str, n: int) -> str:
    return base if n == 1 else f"{base}-{n}"


def reparte(base: str, vectores: list[Path], metadatos: int = len(METADATOS)) -> list[dict]:
    """The assets split into parts of at most `MAX_ASSETS_POR_RELEASE`, part
    1 reserving room for the metadata assets it alone carries.

    Unlike `scripts/empaqueta_scjn_coleccion.py`'s own partition, this one is
    computed rather than read back from a `partes.json`: nothing of these
    three releases is published yet, so there is no arbitrary already-live
    split to preserve. Once they are published, a repartition would have to
    be recorded the way #223 records that one.
    """
    partes: list[dict] = []
    restantes = list(vectores)
    presupuesto = MAX_ASSETS_POR_RELEASE - metadatos
    n = 1
    while restantes or n == 1:
        lote, restantes = restantes[:presupuesto], restantes[presupuesto:]
        partes.append({"tag": _tag_de_parte(base, n), "assets": lote})
        presupuesto = MAX_ASSETS_POR_RELEASE
        n += 1
    return partes


def genera_publicar(coleccion: str, partes: list[dict], out_dir: Path, repo: str) -> str:
    """`PUBLICAR.md` — the exact, copy-pasteable `gh` sequence, one
    `release create` + `upload` pair per part, with each part's body read
    from its own checked-in `.github/<tag>.md`."""
    lineas = [
        f"# Publicar `{TAGS[coleccion]}` — comandos generados, correr a mano",
        "",
        "Issue #115, Hallazgo C: ningún GitHub Action publica datos derivados de la",
        "SCJN, y esto tampoco. Lee primero `vectors-manifest.json` y",
        "`corpus-manifest.json`: dicen con qué modelo, con qué `K` y con qué reglas",
        "de unidad se construyó cada vector.",
        "",
        f"Cuerpo de cada release: `.github/<tag>.md` en el repo (≤ {LIMITE_CUERPO_NOTAS:,}",
        "caracteres, límite de GitHub). El archivo *es* el cuerpo.",
        "",
        "```bash",
        f"cd {out_dir}",
    ]
    for i, parte in enumerate(partes, 1):
        tag = parte["tag"]
        titulo = f"LegalIA — vectores de {coleccion}"
        if len(partes) > 1:
            titulo += f" (parte {i} de {len(partes)})"
        crear = f'gh release create {tag} --repo {repo} --title "{titulo}" ' \
                f"--notes-file $REPO/.github/{tag}.md"
        if i == 1:
            crear += " " + " ".join(METADATOS)
        lineas.append(crear)
        lineas.append(f"xargs -a parte-{i}.txt gh release upload {tag} --repo {repo} --clobber")
    lineas.append(f"gh release edit {partes[0]['tag']} --repo {repo} --latest")
    lineas.append("```")
    lineas.append("")
    lineas.append(
        "`$REPO` es la raíz del repo. `parte-<n>.txt` apunta a los archivos donde ya "
        "están (nada se copió): son cientos de MB por colección."
    )
    lineas.append("")
    return "\n".join(lineas) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--coleccion", choices=tuple(TAGS), required=True)
    parser.add_argument("--out-dir", type=Path, default=None,
                         help="Defaults to <work-dir>/publish.")
    parser.add_argument("--repo", default="INGEOTEC/LegalIA")
    args = parser.parse_args(argv)

    work_dir = args.work_dir
    out_dir = args.out_dir or (work_dir / "publish")
    out_dir.mkdir(parents=True, exist_ok=True)

    vectores = vector_files(work_dir)
    if not vectores:
        raise SystemExit(
            f"{work_dir}: no hay runs/<model>/vectors/ -- corre merge_shards.py primero"
        )

    (out_dir / "vectors-manifest.json").write_text(
        json.dumps(vectors_manifest(work_dir), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for nombre in ("units.parquet", "leaves.parquet", "corpus-manifest.json"):
        destino = out_dir / nombre
        if not destino.exists():
            destino.symlink_to((work_dir / nombre).resolve())

    sumas = [f"{sha256(p)}  {p.name}" for p in vectores]
    sumas += [
        f"{sha256(out_dir / n)}  {n}" for n in METADATOS if (out_dir / n).exists()
    ]
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sorted(sumas)) + "\n", encoding="utf-8")

    partes = reparte(TAGS[args.coleccion], vectores)
    for i, parte in enumerate(partes, 1):
        (out_dir / f"parte-{i}.txt").write_text(
            "\n".join(str(p.resolve()) for p in parte["assets"]) + "\n", encoding="utf-8",
        )
    (out_dir / "PUBLICAR.md").write_text(
        genera_publicar(args.coleccion, partes, out_dir, args.repo), encoding="utf-8",
    )

    raiz = Path(__file__).resolve().parents[2]
    faltan = [
        f".github/{parte['tag']}.md" for parte in partes
        if not (raiz / ".github" / f"{parte['tag']}.md").exists()
    ]
    total = sum(len(p["assets"]) for p in partes) + len(METADATOS)
    print(json.dumps({
        "coleccion": args.coleccion,
        "vector_files": len(vectores),
        "assets_total": total,
        "partes": [{"tag": p["tag"], "assets": len(p["assets"])} for p in partes],
        "out_dir": str(out_dir),
        "release_bodies_missing": faltan,
    }, indent=2, ensure_ascii=False))
    if faltan:
        print(
            "\nFaltan los cuerpos de release listados arriba: escríbelos en .github/ "
            "y vuelve a correr esto antes de publicar.",
        )
    print("\nLee PUBLICAR.md completo. Nada de esto se publica solo (issue #115, Hallazgo C).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
