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
| `units.parquet` | Cada unidad de texto que se embebió: `clave`, `unit_type`, `eId`, `num`, `path`, el texto normalizado, su `text_sha1` y `envelope` (issue #301) |
| `leaves.parquet` | Cada hoja del árbol (`eId`) y la unidad que carga su texto |
| `corpus-manifest.json` | Cómo se construyeron las unidades |
| `vectors-manifest.json` | Cómo se construyeron los vectores |
| `SHA256SUMS.txt` | Suma de cada asset |
| `vectors-<id_ordenamiento>-<modelo>-<K>.parquet` | Los vectores propios de un instrumento — 126, por modelo |
| `vectors-shared-<modelo>-<K>.parquet` | Los vectores del texto que varios comparten — uno por modelo |

Los dos archivos de vectores se leen juntos: `legalvec.load_vectors` une el
propio con el compartido y deduplica por `text_sha1`.

**Desde issue #256, un artículo es siempre una unidad entera**, sin importar
su longitud (`split_articles=False`): ya no se parte por el `cap` de 2 000
caracteres, que sigue rigiendo sólo para el epígrafe de un capítulo, el
preámbulo, los transitorios y el contenido suelto. Un artículo más largo que
la ventana del modelo se **trunca** a esa ventana (nunca se parte ni se
promedia); cada texto truncado queda contado en `vectors-manifest.json`
(`texts_truncated`, `max_tokens_embedded`).

## Los números

| | 0.6B | 4B |
|---|---|---|
| `K` | 1 024 | 2 560 |
| Vectores | 5 924 | 5 924 |
| De los cuales compartidos | 145 | 145 |
| Tamaño | 10 MB | 23 MB |

Corpus: 126 instrumentos, 6 298 unidades, **5 924 textos distintos**, `cap`
2 000, plantilla `bare`, `md2akn` 0.5.1 (issue #291: los artículos decimales,
las introducciones de contenedor y las tablas, y la revisión de los números de artículo repetidos:
reglas 14 a 18, y la lectura de la estructura que declara la API de la SCJN (revisión fix-2 de #291: marcadores de esquema en el Markdown, reglas 19 a 24, y revisión fix-4 de #291: los bloques `other` de la API, las divisiones donde la numeración reinicia y la numeración del Reglamento de Radiocomunicaciones, reglas 29 a 32, revisión fix-5 de #291: un bloque de la API es la raíz de su jerarquía, regla 33, y revisión fix-6 de #291: cada bloque de la API acota su texto, un encabezado sin contenido es un encabezado, las leyendas y notas de una tabla son de la tabla, un artículo-encabezado abre una sección y `N (SIC)` es un artículo, reglas 34 a 39)). Issue #300 añadió los bloques que la API de la SCJN omitía y la regla 40. Issue #301 añade a `units.parquet` la columna `envelope`, verdadera para la cabecera de la SCJN, el decreto de promulgación y las fórmulas de cierre: es parte del corpus y se vectoriza como cualquier otra unidad, pero el Atlas no la compara. La revisión fix-1 de #301 corrige cómo se lee esa envoltura y el preámbulo: el preámbulo es sólo el bloque de cabecera, las conclusiones nunca cruzan un bloque de la API y el decreto de promulgación se reconoce por su posición (reglas 42 y 43).

Los otros 37 instrumentos del release `scjn-lineamientos` no aparecen aquí:
la SCJN los clasifica pero no sirve texto consolidado de ninguno
(`snapshots: 0`, issue #222, decisión 6). No hay nada que embeber.

## El corpus que obligó a leer los acuerdos

Un *lineamiento* casi siempre es un **acuerdo**, y un acuerdo no numera sus
disposiciones con `Artículo N`: escribe `**PRIMERO.-**`, o `1.` / `2.1`. 77
de los 126 instrumentos no numeran sus disposiciones con `Artículo N`. Antes de #227
todo el texto de esos documentos caía en la única unidad que se traga lo que
precede a la primera estructura reconocida — el `preamble`, de 51 246
caracteres en el peor caso — y un vector de eso no sirve para recuperar
nada.

La regla 8 de `md2akn` lee esas numeraciones como artículos, pero **sólo en
un documento que no numera nada con `Artículo N`**, decidido una vez por
documento. Medido sobre este corpus: 58 instrumentos se leen por ordinal, 19
por numeral, y los 49 restantes ya tenían artículos. Las unidades pasaron de 5 273 a 9 314, la mediana del `preamble`
de 3 692 a 1 673 caracteres y su máximo de 51 246 a 2 000.

## Advertencias

- **Esto es dato derivado, no texto legal oficial.** La fuente del texto es
  la SCJN; la oficial sigue siendo el DOF. Un vector no es citable.
- **Texto vigente solamente**, sin historia y sin `codNota`: esta colección
  no está ligada al DOF (issue #220).
- **Nada de esto se publica solo** (issue #115, Hallazgo C).
