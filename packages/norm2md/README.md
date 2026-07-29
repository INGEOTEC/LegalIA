# norm2md

Read back a Mexican legislative-history collection — laws, regulations,
Normas Oficiales Mexicanas, international treaties — from the
[`historial-legislativo`](https://github.com/INGEOTEC/LegalIA/releases/tag/historial-legislativo)
release that [`leyesmx`](../leyesmx) publishes.

## Why a separate package

`leyesmx` *builds* a collection (scraping LeyesBiblio and the DOF, then
packing the result into the release's tarballs); `norm2md` only *reads one
back*. A consumer that just wants a law's reform history — to reconstruct its
current text, say — needs none of `leyesmx`'s scraping dependencies.

## Use

```bash
pip install -e packages/norm2md
```

```python
from norm2md.historial import download_normative_history

leyes = download_normative_history("leyes")   # or "reglamentos", "normas", "tratados"
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")
print(cpeum["nombre"], cpeum["reformas"], len(cpeum["historial"]))
```

Downloads that collection's tarball straight into memory — nothing touches
disk — and returns one dict per instrument, merging its catalogue entry
(name, reform count, dates...) with its own `historial`: the `codNota` of its
reforms or decrees, oldest first, index 0 the original publication. That is
what [`nota2md.leyes.normative_reconstruction`](../nota2md) expects.

See `leyesmx`'s own README for what each collection's `historial` means and
how it was built.
