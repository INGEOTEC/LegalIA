#!/usr/bin/env python3
"""Pack the legislative history into the tarballs published as release assets.

`packages/leyesmx/data/` holds 460-odd JSON files. Shipping them as four
tarballs — one per kind of instrument — makes them downloadable without cloning
the repository, and keeps each collection independently versioned.

The tarballs are **byte-reproducible**: gzip is stamped with mtime 0, members
are added in sorted order, and their timestamps and ownership are fixed. So
identical data always produces an identical file, which is what lets the monthly
workflow tell "nothing changed" from "something changed" by comparing bytes
instead of guessing.

    ./scripts/empaqueta_historial.py --outdir historial
    ./scripts/empaqueta_historial.py --verificar historial   # against a build

Writes a SHA256SUMS.txt alongside the tarballs.
"""

import argparse
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "packages" / "leyesmx" / "data"

#: asset name -> directory under data/, and whether to descend into it.
#: `reformas/` holds the laws at its top level and the regulations in a
#: subdirectory, so the two are split rather than shipped together.
COLECCIONES = {
    "leyes.tgz": ("reformas", False),
    "reglamentos.tgz": ("reformas/reglamentos", False),
    "normas.tgz": ("normas", True),
    "tratados.tgz": ("tratados", True),
}


def _archivos(directorio: Path, recursivo: bool) -> list[Path]:
    patron = "**/*.json" if recursivo else "*.json"
    return sorted(p for p in directorio.glob(patron) if p.is_file())


def empaqueta(nombre: str, origen: Path, recursivo: bool, destino: Path) -> Path:
    """Write one collection's tarball, reproducibly."""
    archivos = _archivos(origen, recursivo)
    if not archivos:
        raise SystemExit(f"{origen} no tiene JSON que empaquetar")

    salida = destino / nombre
    with open(salida, "wb") as bruto, \
            gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w") as tar:
        for archivo in archivos:
            datos = archivo.read_bytes()
            info = tarfile.TarInfo(archivo.relative_to(origen).as_posix())
            info.size = len(datos)
            # Fixed so a rebuild of unchanged data is the same file: these JSONs
            # are regenerated every run and carry a new mtime each time.
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(datos))
    return salida


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--outdir", type=Path, default=Path("historial"))
    p.add_argument("--verificar", type=Path, metavar="DIR",
                   help="compare the tarballs already in DIR against a fresh "
                        "build and report which differ, writing nothing")
    args = p.parse_args(argv)

    destino = args.outdir
    if args.verificar:
        destino = args.verificar.parent / (args.verificar.name + "-nuevo")
    destino.mkdir(parents=True, exist_ok=True)

    hechos = []
    for nombre, (sub, recursivo) in COLECCIONES.items():
        origen = DATOS / sub
        if not origen.is_dir():
            print(f"aviso: falta {origen}, se omite {nombre}", file=sys.stderr)
            continue
        ruta = empaqueta(nombre, origen, recursivo, destino)
        hechos.append(ruta)
        print(f"  {nombre:18} {len(_archivos(origen, recursivo)):4} archivos  "
              f"{ruta.stat().st_size:>9} bytes")

    sumas = destino / "SHA256SUMS.txt"
    sumas.write_text(
        "".join(f"{sha256(r)}  {r.name}\n" for r in sorted(hechos)), encoding="utf-8")

    if args.verificar:
        distintos = [
            r.name for r in hechos
            if not (args.verificar / r.name).exists()
            or sha256(args.verificar / r.name) != sha256(r)
        ]
        print(f"\n{len(distintos)} de {len(hechos)} asset(s) cambiaron"
              + (f": {', '.join(distintos)}" if distintos else ""))
        return 1 if distintos else 0

    print(f"\n{len(hechos)} asset(s) -> {destino}/  (sumas en {sumas})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
