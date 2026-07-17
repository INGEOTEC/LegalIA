"""Point index.html's citation links at the References page.

index.ipynb cites publications inline but does not list them (that list lives
on references.qmd, at the same navbar level as Home/Explorations/Tools).
Quarto's citeproc always resolves citation links to local "#ref-KEY" anchors
and appends a "References" appendix (heading and bibliography div) to the
page that produced them; there is no document-level option to point those
links at another page instead. This project post-render hook does that
rewrite by hand: it retargets every "#ref-KEY" link in the rendered
index.html to "references.html#ref-KEY" and removes the local, now-orphaned
appendix, since the entries it would list are already on the References
page.

Quarto runs every script under `project: post-render:` with the working
directory set to the project directory (website/) and the rendered site in
`_site/` (project.output-dir in _quarto.yml), which is what this script
assumes.
"""

import re
from pathlib import Path

INDEX_HTML = Path("_site/index.html")


def main() -> None:
    if not INDEX_HTML.exists():
        return
    html = INDEX_HTML.read_text(encoding="utf-8")

    html = re.sub(r'href="#(ref-[^"]+)"', r'href="references.html#\1"', html)

    html = re.sub(
        r'\n?<div id="quarto-appendix"[^>]*>.*?</div>\s*(?=</main>)',
        "\n",
        html,
        flags=re.S,
    )

    INDEX_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
