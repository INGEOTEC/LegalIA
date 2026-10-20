Un vector por cada texto distinto del corpus de **reglamentos federales**: el
texto vigente de los 1 083 instrumentos con texto que publica
[`scjn-reglamentos`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-reglamentos),
segmentado en unidades de recuperación por
[`md2akn`](https://pypi.org/project/md2akn/) y embebido con los dos modelos
Qwen3-Embedding (issue #227).

Se lee con [`legalvec`](https://pypi.org/project/legalvec/):

```python
import legalvec

legalvec.download_vectors_assets("reglamentos", ["104906"])
conjunto = legalvec.load_vectors("reglamentos", "104906", "Qwen/Qwen3-Embedding-0.6B")
unidades = legalvec.load_units("reglamentos")
```

**La clave es el `id_ordenamiento`, nunca un slug.** La SCJN reexpide un
reglamento como un `idOrdenamiento` nuevo en vez de como una reforma del
anterior — 137 de 1 080 títulos se repiten — así que cualquier clave derivada
del título choca (issue #220).

## Publicado en tres partes

Un release de GitHub admite a lo más 1 000 assets, y aquí hay 2 173. Así que
esta colección se publica como una serie numerada, igual que el corpus del
que sale (issue #223):

| Release | Assets |
|---|---|
| `scjn-reglamentos-vectors` | 995 vectores + `units.parquet`, `leaves.parquet`, `corpus-manifest.json`, `vectors-manifest.json`, `SHA256SUMS.txt` |
| [`scjn-reglamentos-vectors-2`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-reglamentos-vectors-2) | 1 000 vectores |
| [`scjn-reglamentos-vectors-3`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-reglamentos-vectors-3) | 171 vectores |

`legalvec` resuelve la serie completa preguntándole a GitHub — prueba
`-2`, `-3`, … hasta que una da 404 — así que un lector nunca necesita saber
cuántas partes hay, y nada publicado registra ese número.

## Qué contiene

| Asset | Contenido |
|---|---|
| `units.parquet` | Cada unidad de texto que se embebió: `clave`, `unit_type`, `eId`, `num`, `path`, el texto normalizado y su `text_sha1` |
| `leaves.parquet` | Cada hoja del árbol (`eId`) y la unidad que carga su texto |
| `corpus-manifest.json` | Cómo se construyeron las unidades |
| `vectors-manifest.json` | Cómo se construyeron los vectores |
| `SHA256SUMS.txt` | Suma de cada asset de la serie completa |
| `vectors-<id_ordenamiento>-<modelo>-<K>.parquet` | Los vectores propios de un reglamento — 1 083, por modelo |
| `vectors-shared-<modelo>-<K>.parquet` | Los vectores del texto que varios comparten — uno por modelo |

Los dos archivos de vectores se leen juntos, siempre: un texto que varios
reglamentos comparten se embebe y se publica una sola vez, en el compartido.
`legalvec.load_vectors` une los dos y deduplica por `text_sha1`.

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
| Vectores | 103 457 | 103 457 |
| De los cuales compartidos | 4 469 | 4 469 |
| Tamaño | 162 MB | 394 MB |

Corpus: 1 083 reglamentos, 113 595 unidades, **103 480 textos distintos**,
`cap` 2 000, plantilla `bare`, `md2akn` 0.5.1 (issue #291: los artículos
decimales, las introducciones de contenedor y las tablas, y la revisión de los números
de artículo repetidos: reglas 14 a 18, y la lectura de la estructura que declara la API de la SCJN (revisión fix-2 de #291: marcadores de esquema en el Markdown, reglas 19 a 24, y revisión fix-4 de #291: los bloques `other` de la API, las divisiones donde la numeración reinicia y la numeración del Reglamento de Radiocomunicaciones, reglas 29 a 32, y revisión fix-5 de #291: un bloque de la API es la raíz de su jerarquía, regla 33); los 30 artículos romanos del
reglamento 182464 ya son unidades de artículo, issue #279). Los otros 4 instrumentos del
release `scjn-reglamentos` no aparecen: la SCJN los clasifica pero no sirve
texto consolidado de ninguno (`snapshots: 0`, issue #222, decisión 6).

La deduplicación es **dentro** de la colección, no entre colecciones:
deduplicar contra leyes y lineamientos ahorraba menos del 1 % de los vectores
a cambio de un archivo compartido que tres corpus que se rastrean, empaquetan
y republican por separado tendrían que republicar juntos (issue #227,
decisión 8).

## Advertencias

- **Esto es dato derivado, no texto legal oficial.** La fuente del texto es
  la SCJN; la oficial sigue siendo el DOF. Un vector no es citable.
- **Texto vigente solamente**, sin historia y sin `codNota`: esta colección
  no está ligada al DOF (issue #220).
- **Nada de esto se publica solo** (issue #115, Hallazgo C).
