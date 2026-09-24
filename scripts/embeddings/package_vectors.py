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

Because issue #256 replaces these releases **in place** (same tags, whole
articles instead of split ones), `PUBLICAR.md` also lists every asset
currently on the live release series that the new asset set does not
contain, with the `gh release delete-asset` command to remove each — read
via a read-only `gh release view <tag> --json assets` per part of the
series, probed the same way `legalvec`/`scjn.release` resolve one. Uploading
with `--clobber` (already in `upload_release_assets.py`) replaces the files
whose names are unchanged; this section is only for names that drop out
entirely. A first publish (no live release yet) reports no stale assets.

    python scripts/embeddings/package_vectors.py --work-dir emb-run-leyes \\
        --coleccion leyes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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
    computed rather than read back from a `partes.json`: it is recomputed on
    every run from this run's own asset list, not preserved as an arbitrary
    already-live split the way #223 records `scjn-reglamentos`' partition.
    When a repartition changes the tag series (issue #256's in-place
    replace), `stale_assets` below is what tells the caller so — this
    function itself does not compare against what is live.
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


def _live_assets(tag: str, repo: str, *, runner=None) -> list[str] | None:
    """`tag`'s current asset names, read-only, or `None` when the tag does
    not exist (a release not published yet, or the series' last part).

    `runner` defaults to `None` rather than binding `subprocess.run` in the
    signature, so a test can intercept it either by passing its own stub or
    by monkeypatching this module's `subprocess` name -- a default bound at
    def time would freeze in the real function before any monkeypatch ever
    runs (no network in a unit test, per issue #256's own test list).
    """
    run = runner or subprocess.run
    result = run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return [asset["name"] for asset in data.get("assets", [])]


def live_series_assets(base_tag: str, repo: str, *, runner=None) -> dict[str, list[str]]:
    """`{tag: [asset names]}` for every part of `base_tag`'s series
    currently live on GitHub, probing `-2`, `-3`, ... until one 404s — the
    same resolution `legalvec`/`scjn.release` use for a numbered series
    (issue #256). Empty when the release does not exist yet (a first
    publish)."""
    live: dict[str, list[str]] = {}
    n = 1
    while True:
        tag = _tag_de_parte(base_tag, n)
        assets = _live_assets(tag, repo, runner=runner)
        if assets is None:
            break
        live[tag] = assets
        n += 1
    return live


def stale_assets(
    coleccion: str, partes: list[dict], repo: str, *, runner=None
) -> tuple[dict[str, list[str]], bool]:
    """`({tag: [stale asset names]}, partition_changed)` for `coleccion`'s
    live release series against the new `partes` this run built (issue
    #256): a release replaced in place still has to say what to delete, not
    just what to upload.

    An asset counts as stale when its name does not appear anywhere in the
    new asset set, regardless of which new part carries it — `--clobber`
    already handles a same-named file moving between parts, it is only a
    name dropping out entirely that needs `gh release delete-asset`.
    `partition_changed` is true when there *is* a live series (a first
    publish has nothing to compare against) and its tag set differs from
    the new one -- said explicitly in `PUBLICAR.md` rather than left for a
    reader to notice a missing/extra numbered tag on their own.
    """
    live = live_series_assets(TAGS[coleccion], repo, runner=runner)
    nuevos: set[str] = set()
    for i, parte in enumerate(partes):
        nuevos |= {p.name for p in parte["assets"]}
        if i == 0:
            nuevos |= set(METADATOS)

    stale: dict[str, list[str]] = {}
    for tag, nombres in live.items():
        sobrantes = sorted(n for n in nombres if n not in nuevos)
        if sobrantes:
            stale[tag] = sobrantes

    partition_changed = bool(live) and set(live) != {parte["tag"] for parte in partes}
    return stale, partition_changed


def _cd_destino(out_dir: Path) -> str:
    """Where the generated block should `cd` to, anchored on `$REPO` so it does
    not depend on the reader's current directory. A `--out-dir` outside the repo
    keeps its own absolute path — there is nothing to anchor it to."""
    raiz = Path(__file__).resolve().parents[2]
    resuelto = out_dir.resolve()
    try:
        return f"$REPO/{resuelto.relative_to(raiz)}"
    except ValueError:
        return str(resuelto)


def genera_publicar(
    coleccion: str,
    partes: list[dict],
    out_dir: Path,
    repo: str,
    stale: dict[str, list[str]] | None = None,
    partition_changed: bool = False,
) -> str:
    """`PUBLICAR.md` — the exact, copy-pasteable `gh` sequence, one
    `release create` + `upload` pair per part, with each part's body read
    from its own checked-in `.github/<tag>.md`, plus the `gh release
    delete-asset` commands for `stale` (issue #256's in-place replace)."""
    stale = stale or {}
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
        "La subida va por `upload_release_assets.py`, no por `xargs`: GitHub corta con",
        "un límite secundario a la mitad de un millar de assets, y ese script reanuda",
        "sólo lo que falta. Es idempotente — si algo falla, vuelve a correr la misma",
        "línea.",
    ]
    if partition_changed:
        lineas += [
            "",
            "**La partición en partes cambió respecto a lo publicado.** El número de "
            "tags (o su conjunto) ya no es el mismo -- revisa la sección de assets "
            "obsoletos abajo con cuidado antes de borrar nada.",
        ]
    lineas += [
        "",
        "```bash",
        # The block has to be self-contained: it used to reference $REPO
        # without ever setting it, so a verbatim copy-paste resolved
        # --notes-file to /.github/<tag>.md and gh died on the first command.
        "REPO=$(git rev-parse --show-toplevel)",
        f'cd "{_cd_destino(out_dir)}"',
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
        lineas.append(
            f"python $REPO/scripts/embeddings/upload_release_assets.py {tag} "
            f"parte-{i}.txt --repo {repo}"
        )
    if stale:
        lineas.append("")
        lineas.append(
            "# Assets que la release en vivo tiene y este conjunto nuevo ya no -- "
            "issue #256: borrar sólo después de que las subidas de arriba terminen."
        )
        for tag in sorted(stale):
            for nombre in stale[tag]:
                lineas.append(f"gh release delete-asset {tag} {nombre} --repo {repo} --yes")
    lineas.append(f"gh release edit {partes[0]['tag']} --repo {repo} --latest")
    lineas.append("```")
    lineas.append("")
    lineas.append(
        "El bloque se pega tal cual desde cualquier directorio del repo: `$REPO` es la "
        "raíz, y la define la primera línea. `parte-<n>.txt` apunta a los archivos donde "
        "ya están (nada se copió): son cientos de MB por colección."
    )
    if stale:
        total = sum(len(v) for v in stale.values())
        lineas.append(
            f"\n{total} asset(s) obsoleto(s) en la release en vivo que este run ya no "
            "produce (nombres largos porque el artículo entero cambió de pieza a unidad "
            "-- ver `gh release delete-asset` arriba)."
        )
    else:
        lineas.append(
            "\nNingún asset obsoleto: la release en vivo (si existe) no tiene ningún "
            "nombre que este conjunto ya no produzca."
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
    stale, partition_changed = stale_assets(args.coleccion, partes, args.repo)
    (out_dir / "PUBLICAR.md").write_text(
        genera_publicar(args.coleccion, partes, out_dir, args.repo, stale, partition_changed),
        encoding="utf-8",
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
        "stale_assets": {tag: len(v) for tag, v in stale.items()},
        "partition_changed": partition_changed,
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
