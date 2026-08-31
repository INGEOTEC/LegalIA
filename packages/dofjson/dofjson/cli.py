import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from dofjson import api, archivo, sidof, titulos

ENDPOINT_NAMES = ["diario", "notas", "indicadores"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch DOF (Mexico's official gazette) data from the SIDOF "
        "open data JSON service. To work with every legal provision ever "
        "published, populate the notas-archivo cache instead (`nota2md download "
        "gazette-metadata`, or dofjson.download_dof_assets) and stream it with "
        "dofjson.legal_provisions_titles / dofjson.iterador_de_assets."
    )
    parser.add_argument(
        "date", nargs="?",
        help="Query date, format YYYY-MM-DD (e.g. 2026-07-16). Ignored with --nota.",
    )
    parser.add_argument(
        "--endpoint", choices=ENDPOINT_NAMES, default="notas",
        help="Which date-based service to query (default: notas)",
    )
    parser.add_argument(
        "--nota", type=int,
        help="Download a single note by its codNota alone, instead of querying by date: "
        "saves its JSON if cadenaContenido exists, otherwise falls back to its page image",
    )
    parser.add_argument(
        "--nota-imagenes", type=int,
        help="Download the scanned page image(s) of a single note by its codNota, "
        "regardless of whether the note also has HTML content (existeHtml 'S')",
    )
    parser.add_argument(
        "--nota-pdf", type=int,
        help="Download a single note as its own PDF by its codNota: the whole "
        "edition PDF sliced down to just the note's page(s)",
    )
    parser.add_argument(
        "--pdf-diario", type=int,
        help="Download the PDF of a whole edition by its codDiario (there is no per-note PDF; "
        "get codDiario from get_nota's response, along with pagina/paginaHasta to locate the note)",
    )
    parser.add_argument(
        "--pdf-edicion", metavar="DD-MM-YYYY",
        help="Download the PDF of a whole edition by date (format DD-MM-YYYY, e.g. "
        "16-07-2026) and --edicion, resolving codDiario from get_diario() first "
        "(dofjson.download_edicion_pdf). Requires --edicion.",
    )
    parser.add_argument(
        "--imagenes-diario", type=int,
        help="Fetch the per-page scanned image listing for a whole edition by its codDiario",
    )
    parser.add_argument(
        "--imagen", metavar="NOMBRE_ARCHIVO",
        help="Download a single scanned page as JPEG, by its nombreArchivo "
        "(from --imagenes-diario). Requires --edicion.",
    )
    parser.add_argument(
        "--edicion", choices=["MAT", "VES", "EXT"],
        help="Edition (MAT/VES/EXT) a note or page belongs to, required with --imagen",
    )
    parser.add_argument(
        "--archivo", action="store_true",
        help="Incrementally download the daily notes index for a whole date range "
        "into a resumable local archive: one JSON per day under <outdir>/YYYY/, "
        "with a registry of completed days in <outdir>/.completados so re-runs "
        "only fetch the missing days. Days SIDOF reports as empty are checked "
        "against the DOF website (see --respaldo), and every saved day records "
        'which source it came from in its "fuente" key. '
        "Positional date is ignored; use --desde/--hasta.",
    )
    parser.add_argument(
        "--respaldo", choices=api.RESPALDO_OPCIONES, default="habiles",
        help="What to do when SIDOF reports a day as empty (which is how it "
        "reports the days it has lost, not just the days with no edition): "
        "'habiles' (default) re-checks Mon-Fri against dof.gob.mx, where every "
        "confirmed loss is; 'todos' also re-checks weekends, ~10,000 more "
        "requests over the full range; 'nunca' trusts SIDOF alone. "
        "Applies to --archivo and to a single --endpoint notas query.",
    )
    parser.add_argument(
        "--desde", default=archivo.FECHA_INICIO_DEFAULT.isoformat(),
        help="First date YYYY-MM-DD of the archive range "
        f"(only with --archivo; default: {archivo.FECHA_INICIO_DEFAULT.isoformat()})",
    )
    parser.add_argument(
        "--hasta", default=None,
        help="Last date YYYY-MM-DD of the archive range (only with --archivo; default: today)",
    )
    parser.add_argument(
        "--pausa", type=float, default=0.5,
        help="Seconds to wait between requests to the server "
        "(only with --archivo; default: 0.5)",
    )
    parser.add_argument(
        "--outdir", default=None,
        help="Output directory (default: output/, notas-archivo/ with --archivo)",
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory already holding notas-archivo .tgz assets (see "
        "dofjson.titulos.download_dof_assets). A date already published there "
        "is read off disk instead of querying SIDOF/dofweb. Applies to "
        "--archivo and to a single --endpoint notas query. Not given: uses "
        "dofjson.titulos.CACHE_DIR (the OS-appropriate default). "
        "'none': always fetch live, skipping the cache entirely.",
    )
    return parser.parse_args(argv)


def _resolver_cache_dir(valor: str | None):
    """--cache-dir's value, as a get_notas()/download_archivo()-ready
    argument: not given at all -> titulos.SIN_CACHE_DIR (their own default,
    titulos.CACHE_DIR); 'none' -> None (skip the cache entirely); anything
    else -> that path."""
    if valor is None:
        return titulos.SIN_CACHE_DIR
    if valor.lower() == "none":
        return None
    return Path(valor)


def main(argv=None):
    args = parse_args(argv)
    if args.archivo:
        outdir_default = "notas-archivo"
    else:
        outdir_default = "output"
    outdir = Path(args.outdir or outdir_default)

    if args.archivo:
        try:
            desde = dt.date.fromisoformat(args.desde)
            hasta = dt.date.fromisoformat(args.hasta) if args.hasta else dt.date.today()
        except ValueError as exc:
            sys.exit(f"Invalid date: {exc}. Use YYYY-MM-DD format.")
        if desde > hasta:
            sys.exit(f"--desde ({desde}) cannot be later than --hasta ({hasta}).")
        archivo.download_archivo(
            desde, hasta, outdir, pausa=args.pausa, respaldo=args.respaldo,
            cache_dir=_resolver_cache_dir(args.cache_dir),
        )
        return

    if args.pdf_diario is not None:
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.pdf_diario}.pdf"
        sidof.download_pdf(args.pdf_diario, dest)
        print(f"Saved to: {dest}")
        return

    if args.pdf_edicion is not None:
        if not args.edicion:
            sys.exit("--pdf-edicion requires --edicion")
        try:
            date = dt.datetime.strptime(args.pdf_edicion, "%d-%m-%Y").date()
        except ValueError:
            sys.exit(f"Invalid date: {args.pdf_edicion}. Use DD-MM-YYYY format.")
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            dest = api.download_edicion_pdf(date, args.edicion, outdir)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Saved to: {dest}")
        return

    if args.imagen is not None:
        if not args.edicion:
            sys.exit("--imagen requires --edicion")
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.imagen}.jpg"
        sidof.download_imagen(args.imagen, args.edicion, dest)
        print(f"Saved to: {dest}")
        return

    if args.nota is not None:
        for dest in api.download_nota(args.nota, outdir):
            print(f"Saved to: {dest}")
        return

    if args.nota_imagenes is not None:
        for dest in api.download_nota_imagenes(args.nota_imagenes, outdir):
            print(f"Saved to: {dest}")
        return

    if args.nota_pdf is not None:
        dest = api.download_nota_pdf(args.nota_pdf, outdir)
        print(f"Saved to: {dest}")
        return

    if args.imagenes_diario is not None:
        data = sidof.get_imagenes(args.imagenes_diario)
        filename = f"{args.imagenes_diario}-imagenes.json"
    else:
        if not args.date:
            sys.exit("Provide a date or --nota")
        try:
            date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            sys.exit(f"Invalid date: {args.date}. Use YYYY-MM-DD format.")
        if args.endpoint == "notas":
            # api.get_notas() makes the SIDOF-then-dofweb decision itself
            # (see its docstring) -- the same one procesar_dia() delegates
            # to for a full --archivo run.
            data = api.get_notas(
                date, respaldo=args.respaldo,
                cache_dir=_resolver_cache_dir(args.cache_dir),
            )
            if data.get("fuente") == api.FUENTE_WEB:
                print(f"SIDOF no tiene {date}; recuperada de {api.FUENTE_WEB}")
        else:
            data = getattr(sidof, f"get_{args.endpoint}")(date)
        filename = f"{date:%d%m%Y}-{args.endpoint}.json"

    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / filename
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
