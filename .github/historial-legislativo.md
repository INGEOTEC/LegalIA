**Registro congelado.** Este release ya no se mantiene: nada en el
repositorio LegalIA puede regenerar sus assets, y ningún workflow los vuelve a
publicar. Se conserva descargable porque los datos siguen siendo ciertos del
día en que se construyeron; lo que dejó de ser cierto es que alguien los
actualice.

Contiene el historial de cambios de tres colecciones de la normatividad
federal mexicana, ligado a las legal provisions del Diario Oficial de la
Federación que las publicaron. Cada entrada es un `codNota`: el título, la
fecha y el organismo emisor se recuperan uniéndolo contra el
[archivo de legal provisions del DOF](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo),
que sí se mantiene.

| Asset | Contenido | Último build |
|---|---|---|
| `reglamentos.tgz` | Los reglamentos de leyes federales vigentes, más `reglamentos.json` | 2026-07-28 |
| `normas.tgz` | Las Normas Oficiales Mexicanas (`noms.json`), su catálogo y las citas que no identifican una norma | 2026-08-02 |
| `tratados.tgz` | Los tratados internacionales (`tratados.json`) y su catálogo | 2026-07-28 |

Los tres se construyeron a partir del índice de LeyesBiblio de la Cámara de
Diputados, unido contra el DOF. Reglamentos, normas oficiales y tratados
quedaron **fuera del alcance del proyecto** (issue #184), así que el código que
los construía se borró en lugar de migrarse: no existe ya un `python -m
leyesmx`, ni un `empaqueta_historial.py`, ni el workflow mensual
`reformas.yml` que republicaba este release sin intervención humana.

## Dónde está ahora el historial de las leyes

`leyes.tgz` **se retiró de este release** (issue #190). No fue reemplazado por
otro asset: el historial de reformas de una ley federal *es* el release
[`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes) —
el `indice.json` de cada ley, una entrada por reforma de la más antigua a la
más reciente con el `codNota` que la publicó, más `indice-global.json.gz`, que
invierte todo eso por `codNota`. Se lee desde Python con
`nota2md.download_scjn_leyes_corpus("<abrev>")` y
`nota2md.download_scjn_leyes_index()`.

Dos advertencias para quien venía usando `leyes.tgz`:

- **"Reforma N" cambió de definición.** En este release el número de una
  reforma era su posición en la columna de reformas de la Cámara de Diputados,
  que también numeraba fes de erratas, actualizaciones de cantidades en pesos,
  sentencias y declaratorias de entrada en vigor. En `scjn-leyes` es la
  posición en la tabla de reformas de la SCJN, en orden cronológico. Los dos
  cuentan cosas distintas, y donde difieren se desplazan todos los números
  posteriores: un "reforma 139 de la Constitución" guardado contra este
  release hay que volver a resolverlo **por fecha**, no por número. La
  numeración anterior no se reproduce ni se mide contra la nueva; su fuente ya
  no se consulta.
- **La fuente oficial del texto sigue siendo el DOF/SIDOF.** Los textos
  consolidados de `scjn-leyes` llevan su encabezado `fuente: scjn` y son un
  producto editorial de la Suprema Corte sobre el texto oficial, no el texto
  oficial.

## Verificar lo que este release sirve

Los `.tgz` se construyeron byte a byte reproducibles, y `SHA256SUMS.txt`
cubre exactamente los tres assets que quedan (se regeneró al retirar
`leyes.tgz`, para que la comprobación siga pasando tal cual):

```bash
gh release download historial-legislativo --repo INGEOTEC/LegalIA
sha256sum -c SHA256SUMS.txt
```
