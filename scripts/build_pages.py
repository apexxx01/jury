"""Build the public demo site: the console with the recorded floor baked in.

    uv run python scripts/build_pages.py [out_dir]      # default: site/

The page is the same ``jury/static/index.html`` that ``jury up`` serves. With
``window.JURY_DEMO`` set it reads the floor from the page instead of
``/api/state``, so it runs on any static host (GitHub Pages here).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/apexxx01/jury"
SITE = "https://apexxx01.github.io/jury/"


def build(out: Path) -> Path:
    page = (ROOT / "jury" / "static" / "index.html").read_text(encoding="utf-8")
    demo = json.loads((ROOT / "jury" / "static" / "demo.json").read_text(encoding="utf-8"))
    data = json.dumps(demo, separators=(",", ":")).replace("</", "<\\/")

    meta = (
        '<meta name="description" content="Jury: a hackathon judging floor that runs in your team chat, on Switch. '
        'AI judges gather the evidence, a human gives every verdict.">\n'
        '<meta property="og:title" content="Jury Console">\n'
        '<meta property="og:description" content="Every submission gets a room. AI judges argue it out. A human gives the verdict.">\n'
        f'<meta property="og:image" content="{SITE}social-preview.png">\n'
        f'<meta property="og:url" content="{SITE}">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
    )
    page = page.replace("<title>Jury Console</title>", "<title>Jury Console</title>\n" + meta, 1)
    page = page.replace('<script>\n"use strict";', f'<script>window.JURY_DEMO = {data};</script>\n<script>\n"use strict";', 1)
    page = page.replace(
        "<span>Recorded data: sample teams plus Jury's own dry run (Case #001). <code>jury up</code> shows your live floor.</span>",
        "<span>Recorded data: sample teams plus Jury's own dry run (Case #001). "
        f'<a href="{REPO}">Get Jury on GitHub</a> and <code>jury up</code> shows your live floor.</span>',
        1,
    )
    if "window.JURY_DEMO" not in page:
        raise SystemExit("couldn't inject the demo floor: the console's script tag changed")

    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(ROOT / "docs" / "img" / "social-preview.png", out / "social-preview.png")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"


if __name__ == "__main__":
    print(f"wrote {build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'site')}")
