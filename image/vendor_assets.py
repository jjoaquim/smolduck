"""Self-host the frontend's ESM modules + fonts for the offline (no-egress) VM.

Reads the CDN import map from a source `index.html`, crawls the esm.sh module
graph, mirrors every module into `<out>/vendor/esm/` as flat files with their
internal imports rewritten to local paths, downloads the web fonts, and writes a
localized `<out>/index.html` that loads everything from the same origin.

Pure stdlib so it can run inside the builder VM (python:3.12-slim). Needs network
at *build* time only; the produced frontend has zero external requests.

Usage:
  python vendor_assets.py --src-frontend <dir> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shutil
import time
import urllib.request
from pathlib import Path

ESM_BASE = "https://esm.sh"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# Vendor everything in the import map. The Python kernel renders
# inline Plotly figures, so plotly.js-dist-min must be self-hosted for the
# no-egress VM (its module graph is large, but the crawl handles it).
SKIP_SPECIFIERS: set[str] = set()

# Module references inside esm.sh files: absolute (/…) or relative (./… , ../…).
# esm.sh emits both forms, so both must be rewritten + crawled or they 404.
REF_RE = re.compile(r"""(["'])((?:/|\.{1,2}/)[^"'\n]+?)\1""")
IMPORTMAP_RE = re.compile(r'<script type="importmap">.*?</script>', re.DOTALL)
FONT_LINK_RE = re.compile(r'<link[^>]*fonts\.(?:googleapis|gstatic)\.com[^>]*>\s*', re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\((https://fonts\.gstatic\.com/[^)]+)\)")


def fetch(url: str, tries: int = 6) -> bytes:
    """Fetch with retries — the build host's network is flaky."""
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"failed to fetch {url} after {tries} tries: {last}")


def flatten(path: str) -> str:
    """Map an esm.sh path(+query) to a flat, JS-extensioned, filesystem-safe name."""
    name = re.sub(r"[^A-Za-z0-9._@-]", "_", path.lstrip("/"))
    if not name.endswith(".js"):
        name += ".js"
    return name


def is_module_ref(path: str) -> bool:
    return ".mjs" in path or ".js" in path or "?" in path or "@" in path


def vendor_esm(seeds: dict[str, str], esm_dir: Path) -> dict[str, str]:
    """Crawl + mirror the esm.sh graph. Returns specifier -> local URL for seeds.

    Every referenced module is tracked by its flat filename, so the crawl is
    complete by construction; a persistent fetch failure raises rather than
    leaving a dangling import.
    """
    esm_dir.mkdir(parents=True, exist_ok=True)
    flat_to_path: dict[str, str] = {}
    pending: list[str] = []
    spec_local: dict[str, str] = {}

    def enqueue(path: str) -> str:
        flat = flatten(path)
        if flat not in flat_to_path:
            flat_to_path[flat] = path
            pending.append(flat)
        return flat

    for spec, cdn_url in seeds.items():
        path = cdn_url[len(ESM_BASE):] if cdn_url.startswith(ESM_BASE) else cdn_url
        spec_local[spec] = f"/vendor/esm/{enqueue(path)}"

    def make_repl(cur_path: str):
        cur_dir = posixpath.dirname(cur_path)

        def repl(m: re.Match) -> str:
            ref = m.group(2)
            if ref.startswith("/vendor/") or not is_module_ref(ref):
                return m.group(0)
            # Resolve relative refs against the current module's esm.sh path so
            # they map to the same flat file as any absolute ref to that module.
            target = ref if ref.startswith("/") else posixpath.normpath(posixpath.join(cur_dir, ref))
            return f"{m.group(1)}/vendor/esm/{enqueue(target)}{m.group(1)}"

        return repl

    count = 0
    while pending:
        flat = pending.pop()
        cur_path = flat_to_path[flat]
        text = fetch(ESM_BASE + cur_path).decode("utf-8")
        (esm_dir / flat).write_text(REF_RE.sub(make_repl(cur_path), text), encoding="utf-8")
        count += 1

    print(f"  vendored {count} esm modules")
    return spec_local


def vendor_fonts(css_url: str, fonts_dir: Path) -> None:
    fonts_dir.mkdir(parents=True, exist_ok=True)
    css = fetch(css_url).decode("utf-8")
    files: dict[str, str] = {}
    for font_url in set(CSS_URL_RE.findall(css)):
        fname = re.sub(r"[^A-Za-z0-9._-]", "_", font_url.rsplit("/", 1)[-1].split("?")[0])
        if not Path(fname).suffix:
            fname += ".woff2"
        (fonts_dir / fname).write_bytes(fetch(font_url))
        files[font_url] = fname
    css = CSS_URL_RE.sub(lambda m: f"url(./{files[m.group(1)]})", css)
    (fonts_dir / "fonts.css").write_text(css, encoding="utf-8")
    print(f"  vendored {len(files)} font files")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-frontend", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = Path(args.src_frontend).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Copy our own (unchanged) source: app.js, components/, lib/, styles.css.
    for item in ["app.js", "styles.css", "components", "lib"]:
        s = src / item
        if not s.exists():
            continue
        d = out / item
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy(s, d)

    index_html = (src / "index.html").read_text()
    importmap = json.loads(IMPORTMAP_RE.search(index_html).group(0)
                           .split(">", 1)[1].rsplit("<", 1)[0])
    imports = importmap["imports"]

    seeds = {spec: url for spec, url in imports.items() if spec not in SKIP_SPECIFIERS}
    print("vendoring frontend assets for offline use…")
    spec_local = vendor_esm(seeds, out / "vendor" / "esm")

    # Fonts: pull the css2 url out of the source <link>.
    css_match = re.search(r'href="(https://fonts\.googleapis\.com/css2[^"]+)"', index_html)
    if css_match:
        vendor_fonts(css_match.group(1), out / "vendor" / "fonts")

    # Rewrite index.html: local import map + local fonts; drop preconnects.
    local_map = json.dumps({"imports": spec_local}, indent=2)
    index_html = IMPORTMAP_RE.sub(
        f'<script type="importmap">\n{local_map}\n    </script>', index_html
    )
    index_html = FONT_LINK_RE.sub("", index_html)
    index_html = index_html.replace(
        '<link rel="stylesheet" href="./styles.css" />',
        '<link rel="stylesheet" href="./vendor/fonts/fonts.css" />\n'
        '    <link rel="stylesheet" href="./styles.css" />',
    )
    (out / "index.html").write_text(index_html)
    print(f"wrote localized frontend to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
