Un vector por cada texto distinto del corpus de **lineamientos federales**:
el texto vigente de los 126 instrumentos con texto que publica
[`scjn-lineamientos`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-lineamientos),
segmentado en unidades de recuperación por
[`md2akn`](https://pypi.org/project/md2akn/) y embebido con los dos modelos
Qwen3-Embedding (issue #227).

Se lee con [`legalvec`](https://pypi.org/project/legalvec/):

```python
import legalvec

legalvec.download_vectors_assets("lineamientos")          # el corpus completo: 51 MB
conjunto = legalvec.load_vectors("lineamientos", "31834", "Qwen/Qwen3-Embedding-0.6B")
unidades = legalvec.load_units("lineamientos")
```

**La clave es el `id_ordenamiento`, nunca un slug** — `"31834"` arriba. La
SCJN reexpide un instrumento como un `idOrdenamiento` nuevo en vez de como
una reforma del anterior, así que cualquier clave derivada del título choca
(issue #220, heredado sin cambios).

## Qué contiene

| Asset | Contenido |
|---|---|
| `units.parquet` | Cada unidad de texto que se embebió: `clave`, `unit_type`, `eId`, `num`, `path`, el texto normalizado y su `text_sha1` |
| `leaves.parquet` | Cada hoja del árbol (`eId`) y la unidad que carga su texto |
| `corpus-manifest.json` | Cómo se construyeron las unidades |
| `vectors-manifest.json` | Cómo se construyeron los vectores |
| `SHA256SUMS.txt` | Suma de cada asset |
| `vectors-<id_ordenamiento>-<modelo>-<K>.parquet` | Los vectores propios de un instrumento — 126, por modelo |
| `vectors-shared-<modelo>-<K>.parquet` | Los vectores del texto que varios comparten — uno por modelo |

Los dos archivos de vectores se leen juntos: `legalvec.load_vectors` une el
propio con el compartido y deduplica por `text_sha1`.

## Los números

| | 0.6B | 4B |
|---|---|---|
| `K` | 1 024 | 2 560 |
| Vectores | 8 747 | 8 747 |
| De los cuales compartidos | 221 | 221 |
| Tamaño | 14 MB | 33 MB |

Corpus: 126 instrumentos, 9 314 unidades, **8 747 textos distintos**, `cap`
2 000, plantilla `bare`, `md2akn` 0.3.0.

Los otros 37 instrumentos del release `scjn-lineamientos` no aparecen aquí:
la SCJN los clasifica pero no sirve texto consolidado de ninguno
(`snapshots: 0`, issue #222, decisión 6). No hay nada que embeber.

## El corpus que obligó a leer los acuerdos

Un *lineamiento* casi siempre es un **acuerdo**, y un acuerdo no numera sus
disposiciones con `Artículo N`: escribe `**PRIMERO.-**`, o `1.` / `2.1`. 89
de los 126 instrumentos no tienen una sola línea de artículo. Antes de #227
todo el texto de esos documentos caía en la única unidad que se traga lo que
precede a la primera estructura reconocida — el `preamble`, de 51 246
caracteres en el peor caso — y un vector de eso no sirve para recuperar
nada.

La regla 8 de `md2akn` lee esas numeraciones como artículos, pero **sólo en
un documento que no numera nada con `Artículo N`**, decidido una vez por
documento. Medido sobre este corpus: 64 instrumentos se leen por ordinal, 19
por numeral, y los 43 restantes ya tenían artículos o no usan ninguna de las
dos formas. Las unidades pasaron de 5 273 a 9 314, la mediana del `preamble`
de 3 692 a 1 673 caracteres y su máximo de 51 246 a 2 000.

## Advertencias

- **Esto es dato derivado, no texto legal oficial.** La fuente del texto es
  la SCJN; la oficial sigue siendo el DOF. Un vector no es citable.
- **Texto vigente solamente**, sin historia y sin `codNota`: esta colección
  no está ligada al DOF (issue #220).
- **Nada de esto se publica solo** (issue #115, Hallazgo C).
