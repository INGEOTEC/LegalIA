"""The line patterns that recognize a law's structure.

Empty of structural rules at this layer (issue #158, whose whole point is to
fix the shape of the API before any rule exists). What lands here:

- #159 — preamble, containers (LIBRO/TÍTULO/CAPÍTULO/SECCIÓN/APARTADO),
  articles, transitorios and the closing signatures.
- #160 — fracciones, incisos and subincisos inside an article, with the
  consecutiveness rule that tells a Roman numeral from a capital letter.
- #161 — the `(REFORMADO, D.O.F. ...)` annotations.

Only the frontmatter delimiter lives here for now: it is the one piece of
line-level syntax #158 does have to recognize, and putting it anywhere else
would mean patterns.py were an empty file.
"""

import re

#: The `---` fence around the YAML frontmatter the SCJN corpus files open
#: with. Matched at the very start of the document only — a `---` in the body
#: is a horizontal rule, not a frontmatter fence.
FRONTMATTER_FENCE = re.compile(r"^---[ \t]*\r?\n")

#: One `key: value` line of the frontmatter. The value is everything after
#: the first colon, so a value that itself contains ": " (a title with a
#: subtitle, say) survives intact.
FRONTMATTER_ENTRY = re.compile(r"^([A-Za-z_][\w.-]*)[ \t]*:[ \t]*(.*)$")
