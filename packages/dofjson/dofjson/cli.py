import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from dofjson import archivo, client, titulos

ENDPOINT_NAMES = ["diario", "notas", "indicadores"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch DOF (Mexico's official gazette) data from the SIDOF open data JSON service."
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
        "only fetch the missing days. Positional date is ignored; use --desde/--hasta.",
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
        "--titulos", action="store_true",
        help="Build a compact codNota+titulo dataset (gzipped JSONL) from every "
        "note in the published notas-archivo GitHub release: downloads each "
        "year/month asset straight into memory (nothing touches disk) and "
        "keeps only codNota and titulo. Small output, meant for Colab GPU "
        "experiments.",
    )
    parser.add_argument(
        "--outdir", default=None,
        help="Output directory (default: output/, notas-archivo/ with --archivo, "
        "or titulos/ with --titulos)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.archivo:
        outdir_default = "notas-archivo"
    elif args.titulos:
        outdir_default = "titulos"
    else:
        outdir_default = "output"
    outdir = Path(args.outdir or outdir_default)

    if args.titulos:
        dest = outdir / "titulos.jsonl.gz"
        titulos.download_titulos(dest)
        return

    if args.archivo:
        try:
            desde = dt.date.fromisoformat(args.desde)
            hasta = dt.date.fromisoformat(args.hasta) if args.hasta else dt.date.today()
        except ValueError as exc:
            sys.exit(f"Invalid date: {exc}. Use YYYY-MM-DD format.")
        if desde > hasta:
            sys.exit(f"--desde ({desde}) cannot be later than --hasta ({hasta}).")
        archivo.download_archivo(desde, hasta, outdir, pausa=args.pausa)
        return

    if args.pdf_diario is not None:
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.pdf_diario}.pdf"
        client.download_pdf(args.pdf_diario, dest)
        print(f"Saved to: {dest}")
        return

    if args.imagen is not None:
        if not args.edicion:
            sys.exit("--imagen requires --edicion")
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.imagen}.jpg"
        client.download_imagen(args.imagen, args.edicion, dest)
        print(f"Saved to: {dest}")
        return

    if args.nota is not None:
        for dest in client.download_nota(args.nota, outdir):
            print(f"Saved to: {dest}")
        return

    if args.nota_imagenes is not None:
        for dest in client.download_nota_imagenes(args.nota_imagenes, outdir):
            print(f"Saved to: {dest}")
        return

    if args.nota_pdf is not None:
        dest = client.download_nota_pdf(args.nota_pdf, outdir)
        print(f"Saved to: {dest}")
        return

    if args.imagenes_diario is not None:
        data = client.get_imagenes(args.imagenes_diario)
        filename = f"{args.imagenes_diario}-imagenes.json"
    else:
        if not args.date:
            sys.exit("Provide a date or --nota")
        try:
            date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            sys.exit(f"Invalid date: {args.date}. Use YYYY-MM-DD format.")
        fetch = getattr(client, f"get_{args.endpoint}")
        data = fetch(date)
        if args.endpoint == "notas":
            data = client.quita_notas_sin_titulo(data)
        filename = f"{date:%d%m%Y}-{args.endpoint}.json"

    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / filename
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
