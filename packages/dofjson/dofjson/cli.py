import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from dofjson import client

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
    parser.add_argument("--outdir", default="output", help="Output directory (default: output/)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.pdf_diario is not None:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.pdf_diario}.pdf"
        client.download_pdf(args.pdf_diario, dest)
        print(f"Saved to: {dest}")
        return

    if args.imagen is not None:
        if not args.edicion:
            sys.exit("--imagen requires --edicion")
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / f"{args.imagen}.jpg"
        client.download_imagen(args.imagen, args.edicion, dest)
        print(f"Saved to: {dest}")
        return

    if args.nota is not None:
        outdir = Path(args.outdir)
        for dest in client.download_nota(args.nota, outdir):
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
        filename = f"{date:%d%m%Y}-{args.endpoint}.json"

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / filename
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
