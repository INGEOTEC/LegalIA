"""Point the citation links of some pages at the References page.

index.ipynb and pages/atlas.qmd cite publications inline but do not list them
(that list lives on references.qmd, at the same navbar level as Home/Archive/
Federal Laws, through its `nocite` field). Quarto's citeproc always resolves
citation links to local "#ref-KEY" anchors and appends a "References"
appendix (heading and bibliography div) to the page that produced them; there
is no document-level option to point those links at another page instead.
This project post-render hook does that rewrite by hand: for every page in
`PAGES` it retargets each "#ref-KEY" link in the rendered HTML to
"references.html#ref-KEY" and removes the local, now-orphaned appendix, since
the entries it would list are already on the References page.

The retargeted address is relative to each page's own directory, so it is
`pages/references.html` from `index.html` and `references.html` from
`pages/atlas.html`.

Quarto runs every script under `project: post-render:` with the working
directory set to the project directory (website/) and the rendered site in
`_site/` (project.output-dir in _quarto.yml), which is what this script
assumes. A page that has not been rendered is skipped.
"""

import os
import re
from pathlib import Path

SITE = Path("_site")
REFERENCES = SITE / "pages" / "references.html"

#: Rendered pages whose citations are listed on the References page.
PAGES = [SITE / "index.html", SITE / "pages" / "atlas.html"]


def retarget(html: str, page: Path) -> str:
    """`html` of `page` with its citation links on the References page and no
    local appendix."""
    target = Path(os.path.relpath(REFERENCES, page.parent)).as_posix()
    html = re.sub(r'href="#(ref-[^"]+)"', rf'href="{target}#\1"', html)
    return re.sub(
        r'\n?<div id="quarto-appendix"[^>]*>.*?</div>\s*(?=</main>)',
        "\n",
        html,
        flags=re.S,
    )


def main() -> None:
    for page in PAGES:
        if page.exists():
            page.write_text(retarget(page.read_text(encoding="utf-8"), page),
                            encoding="utf-8")


if __name__ == "__main__":
    main()
