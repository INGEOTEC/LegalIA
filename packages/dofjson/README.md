# dofjson

Prototype client for the JSON open data service exposed by
[sidof.segob.gob.mx](https://sidof.segob.gob.mx/datos_abiertos), the
Secretaría de Gobernación's system for Mexico's official gazette (DOF,
Diario Oficial de la Federación).

The service's public docs only show sample responses, but its real,
unauthenticated endpoints were found under `https://sidof.segob.gob.mx/dof/sidof/`:

| Endpoint | Description |
|---|---|
| `GET /diarios/porFecha/DD-MM-YYYY` | Edition metadata for a date (Matutina/Vespertina/Extraordinaria) |
| `GET /notas/DD-MM-YYYY` | Notes/documents published on a date |
| `GET /notas/nota/{codNota}` | Full detail of a single note, including its HTML content |
| `GET /indicadores/DD-MM-YYYY` | Economic indicators (exchange rate, TIIE, UDIS) |

This is an experimental package for evaluating whether this service is a
viable alternative (or complement) to `dof2md`'s PDF download + Markdown
conversion pipeline — notes already come with structured HTML content,
which may be easier to work with than OCR'd PDFs.

## Usage

```bash
pip install -e "packages/dofjson[test]"
dofjson 2026-07-16 --endpoint notas --outdir output
```

## Development

```bash
pytest packages/dofjson
```
