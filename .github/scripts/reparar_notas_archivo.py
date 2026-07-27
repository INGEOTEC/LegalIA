"""Repair notas-archivo assets: refill the days SIDOF lost, from the DOF site.

SIDOF reports a day it is missing as 200 OK with no notes, which is also how
it reports a Sunday, so days it lost were archived as empty. This walks the
published assets, finds every weekday stored with no notes, asks
www.dof.gob.mx whether the gazette actually came out, and rewrites only those
days. Everything else in the tarball is copied through untouched: same member
names, order, mode, ownership and mtime.

Run with --dry-run first: it reports what it would change and writes nothing.

    python .github/scripts/reparar_notas_archivo.py --anios 1999,2006 --dry-run
    python .github/scripts/reparar_notas_archivo.py --anios auto

Uploading is left to `gh release upload --clobber` in the workflow, so this
script never needs a token.
"""

import argparse
import datetime as dt
import gzip
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import requests

from dofjson import dofweb, titulos

TAG = "notas-archivo"
LISTAS = ("NotasMatutinas", "NotasVespertinas", "NotasExtraordinarias")
# The site echoes the date it is actually serving. Under concurrency it has
# been seen to answer with a different day's page, so a recovered day is only
# accepted when this matches what was asked for.
_ENCABEZADO = re.compile(r"Fecha:\s*([0-9/]+)")


def dias_del_asset(contenido: bytes) -> dict[str, int]:
    """Map every day file in the tarball to how many titled notes it holds."""
    dias = {}
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for m in tar:
            if not m.isfile() or not m.name.endswith(".json"):
                continue
            base = m.name.rsplit("/", 1)[-1][:8]          # DDMMYYYY
            fecha = f"{base[4:8]}-{base[2:4]}-{base[0:2]}"
            dia = json.load(tar.extractfile(m))
            dias[fecha] = sum(
                1 for k in LISTAS for n in dia.get(k, []) if n.get("titulo")
            )
    return dias


def recupera(fecha: dt.date, timeout: int = 60):
    """The day's index from the DOF website, or None if it has no such day.

    Rejects a response whose printed date is not the one requested.
    """
    datos = dofweb.get_notas(fecha, timeout=timeout)
    if not dofweb.hay_publicacion(datos):
        return None
    url = (
        f"{dofweb.BASE_URL}/index.php?year={fecha:%Y}&month={fecha:%m}"
        f"&day={fecha:%d}&edicion=MAT"
    )
    pagina = dofweb._get(url, timeout)
    enc = _ENCABEZADO.search(pagina)
    if not enc or enc.group(1) != f"{fecha:%d/%m/%Y}":
        print(f"    {fecha}: la página dice {enc.group(1) if enc else 'nada'}, "
              f"no {fecha:%d/%m/%Y} — descartada", flush=True)
        return None
    return datos


def reescribe(contenido: bytes, nuevos: dict[str, bytes], destino: Path) -> tuple[int, int]:
    """Copy the tarball to `destino`, swapping in `nuevos` (member -> bytes).

    The gzip header is stamped with mtime 0 so the same inputs always produce
    the same bytes; otherwise every run would differ only by its timestamp and
    checksums could not be compared across runs.
    """
    cambiados = copiados = 0
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as ent, \
            open(destino, "wb") as bruto, \
            gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w") as sal:
        for m in ent.getmembers():
            if m.name in nuevos:
                datos = nuevos[m.name]
                info = tarfile.TarInfo(m.name)
                info.mode, info.uid, info.gid = m.mode, m.uid, m.gid
                info.uname, info.gname, info.mtime = m.uname, m.gname, m.mtime
                info.size = len(datos)
                sal.addfile(info, io.BytesIO(datos))
                cambiados += 1
            elif m.isfile():
                sal.addfile(m, ent.extractfile(m))
                copiados += 1
            else:
                sal.addfile(m)
    return cambiados, copiados


def repara_asset(asset: dict, outdir: Path, dry_run: bool) -> dict:
    nombre = asset["name"]
    print(f"\n=== {nombre}", flush=True)
    contenido = requests.get(asset["url"], headers=titulos._HEADERS, timeout=300).content
    dias = dias_del_asset(contenido)
    vacios = [
        f for f, n in sorted(dias.items())
        if n == 0 and dt.date.fromisoformat(f).weekday() < 5
    ]
    print(f"  {len(dias)} días, {len(vacios)} hábiles sin notas", flush=True)

    nuevos, detalle = {}, []
    for f in vacios:
        d = dt.date.fromisoformat(f)
        datos = recupera(d)
        if datos is None or not dofweb.cuenta_notas(datos):
            continue
        miembro = f"{d:%Y}/{d:%d%m%Y}-notas.json"
        nuevos[miembro] = json.dumps(datos, ensure_ascii=False, indent=2).encode("utf-8")
        detalle.append({"fecha": f, "notas": dofweb.cuenta_notas(datos)})
        print(f"    {f}: {dofweb.cuenta_notas(datos)} notas recuperadas", flush=True)

    if not nuevos:
        print("  sin cambios", flush=True)
        return {"asset": nombre, "dias": []}
    if dry_run:
        print(f"  [dry-run] reescribiría {len(nuevos)} día(s)", flush=True)
        return {"asset": nombre, "dias": detalle, "dry_run": True}

    outdir.mkdir(parents=True, exist_ok=True)
    destino = outdir / nombre
    cambiados, copiados = reescribe(contenido, nuevos, destino)
    # re-read what was written, rather than trusting the write
    verif = dias_del_asset(destino.read_bytes())
    for x in detalle:
        if verif.get(x["fecha"]) != x["notas"]:
            raise SystemExit(f"{nombre}: {x['fecha']} quedó con {verif.get(x['fecha'])} "
                             f"notas, se esperaban {x['notas']}")
    print(f"  escrito {destino} ({cambiados} reescritos, {copiados} copiados, "
          f"{destino.stat().st_size} bytes)", flush=True)
    return {"asset": nombre, "dias": detalle, "archivo": str(destino)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--anios", default="auto",
                   help="comma-separated years, or 'auto' for every asset")
    p.add_argument("--outdir", default="reparados")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change; write nothing")
    args = p.parse_args(argv)

    assets = titulos.listar_assets()
    if args.anios != "auto":
        quiere = {a.strip() for a in args.anios.split(",") if a.strip()}
        assets = [a for a in assets
                  if a["name"].removeprefix("notas-").removesuffix(".tgz")[:4] in quiere]
        if not assets:
            sys.exit(f"ningún asset coincide con --anios {args.anios}")

    outdir = Path(args.outdir)
    resultados = [repara_asset(a, outdir, args.dry_run) for a in assets]
    con_cambios = [r for r in resultados if r["dias"]]

    print("\n=== RESUMEN")
    total = 0
    for r in con_cambios:
        n = sum(d["notas"] for d in r["dias"])
        total += n
        print(f"  {r['asset']}: {len(r['dias'])} día(s), {n} notas "
              f"({', '.join(d['fecha'] for d in r['dias'])})")
    print(f"  {len(con_cambios)} asset(s), {total} notas recuperadas")
    # Alongside the rebuilt assets, not in whatever directory this was run
    # from -- in CI that is the checkout, which should stay clean.
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "reparacion.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # Finding nothing to repair is a good outcome, not a failure: the caller
    # decides what to upload from what was actually written.
    return 0


if __name__ == "__main__":
    sys.exit(main())
