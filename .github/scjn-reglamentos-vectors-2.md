Parte 2 de la serie **`scjn-reglamentos-vectors`** — un vector por cada texto
distinto del corpus de reglamentos federales (issue #227).

Este release sólo carga archivos de vectores. Todo lo demás — qué contiene
la colección, cómo se construyó, cómo se lee con
[`legalvec`](https://pypi.org/project/legalvec/), los manifiestos, las sumas
SHA-256 y las advertencias — está en la parte 1:
[`scjn-reglamentos-vectors`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-reglamentos-vectors).

Un release de GitHub admite a lo más 1 000 assets y aquí hay 2 171, de ahí la
serie numerada (issue #223). `legalvec` la resuelve completa preguntándole a
GitHub: prueba `-2`, `-3`, … hasta que una da 404, así que nada publicado
registra cuántas partes hay y un lector nunca tiene que saberlo.
