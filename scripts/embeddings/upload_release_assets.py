"""Upload a part's assets to its GitHub release, resumably.

`PUBLICAR.md` used to say `xargs -a parte-<n>.txt gh release upload ... --clobber`,
which works for a few hundred assets and does not work for a thousand: publishing
`scjn-reglamentos-vectors` on 2026-09-13 stopped after 488 of 995 with
`HTTP 403: You have exceeded a secondary rate limit`. `xargs` then reported a bare
exit 123 with no record of which assets had landed, so the only recovery was to
re-upload all 995 — into the same rate limit.

So this uploads in small batches, pauses between them, waits out a secondary rate
limit and picks up where it left off. It is idempotent: an asset already on the
release at exactly its local size is skipped, so re-running after any failure
costs only what is actually missing. Size, not mere presence, is the test — a
connection dropped mid-asset leaves a short one behind.

Nothing here publishes on its own: a human runs it, same as every other step of
`PUBLICAR.md` (issue #115, Hallazgo C).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

BATCH = 15
"""Assets per `gh release upload` call. Small enough that a rate limit costs one
batch, large enough that the per-call overhead stays negligible."""

PAUSE = 10.0
"""Seconds between batches — the cheapest way to stay under the secondary limit."""

BACKOFF = 180.0
"""Seconds to wait after GitHub does complain. Its own advice is "a few minutes"."""


def published_sizes(tag: str, repo: str) -> dict[str, int]:
    """Asset name -> byte size, as the release currently holds them."""
    out = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {a["name"]: a["size"] for a in json.loads(out)["assets"]}


def pending(paths: list[Path], published: dict[str, int]) -> list[Path]:
    """The assets still missing, or on the release at the wrong size."""
    return [p for p in paths if published.get(p.name) != p.stat().st_size]


def _is_rate_limit(salida: str) -> bool:
    return "rate limit" in salida or "HTTP 403" in salida


def upload(tag: str, paths: list[Path], repo: str) -> int:
    """Upload `paths` to `tag`, skipping what is already there. Returns an exit code."""
    faltan = pending(paths, published_sizes(tag, repo))
    print(f"{tag}: {len(paths)} assets, {len(paths) - len(faltan)} ya completos, "
          f"{len(faltan)} por subir", flush=True)

    subidos = 0
    while subidos < len(faltan):
        lote = faltan[subidos:subidos + BATCH]
        proceso = subprocess.run(
            ["gh", "release", "upload", tag, "--repo", repo, "--clobber",
             *(str(p) for p in lote)],
            capture_output=True, text=True,
        )
        if proceso.returncode == 0:
            subidos += len(lote)
            print(f"  {subidos}/{len(faltan)}", flush=True)
            time.sleep(PAUSE)
            continue
        salida = (proceso.stderr or "") + (proceso.stdout or "")
        if not _is_rate_limit(salida):
            print(f"  fallo no recuperable:\n{salida}", flush=True)
            return 1
        print(f"  límite secundario de GitHub; espero {BACKOFF:.0f}s", flush=True)
        time.sleep(BACKOFF)
        # Part of the batch may have landed before the limit hit; ask the release
        # what it actually has rather than re-uploading the whole batch.
        faltan = faltan[:subidos] + pending(faltan[subidos:], published_sizes(tag, repo))

    restantes = pending(paths, published_sizes(tag, repo))
    print(f"{tag}: faltantes al terminar: "
          f"{[p.name for p in restantes] if restantes else 'ninguno'}", flush=True)
    return 1 if restantes else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, e.g. scjn-reglamentos-vectors-2.")
    parser.add_argument("lista", type=Path, help="parte-<n>.txt: one asset path per line.")
    parser.add_argument("--repo", default="INGEOTEC/LegalIA")
    args = parser.parse_args(argv)

    paths = [Path(l) for l in args.lista.read_text().split()]
    ausentes = [p for p in paths if not p.exists()]
    if ausentes:
        raise SystemExit(f"{args.lista}: no existen {len(ausentes)} rutas, la primera {ausentes[0]}")
    return upload(args.tag, paths, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
