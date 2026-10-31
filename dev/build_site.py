# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the documentation website (GitHub Pages) from the Markdown files in the repository.

    python dev/build_site.py --out site

Pages are generated from README.md, docs/*.md and CHANGELOG.md; images are copied from docs/images.
Requires the ``markdown``, ``pymdown-extensions`` and ``pygments`` packages.
"""

import argparse
import html
import pathlib
import re
import shutil

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_SRC = ROOT / "dev" / "site"
REPO_URL = "https://github.com/ManerMorenn/MorennBridge"
# Drop this file into dev/site/static to use the studio typeface instead of the Google Fonts fallback.
STUDIO_FONT = "fliegemono-thin.otf"

# (slug, source file, navigation label, eyebrow label); slug "" is the home page
PAGES = [
    ("", "README.md", "Overview", "Blender to Unreal Engine 5"),
    ("guide", "docs/USER_GUIDE.md", "User guide", "Documentation"),
    ("conventions", "docs/CONVENTIONS.md", "Conventions", "Reference"),
    ("unreal-setup", "docs/UE_SETUP.md", "Unreal setup", "Reference"),
    ("changelog", "CHANGELOG.md", "Changelog", "Releases"),
]

# Markdown links between repository files, rewritten to site URLs (relative to the site root).
LINK_TARGETS = {
    "README.md": "",
    "morennbridge/README.md": "",
    "docs/USER_GUIDE.md": "guide/",
    "USER_GUIDE.md": "guide/",
    "docs/CONVENTIONS.md": "conventions/",
    "CONVENTIONS.md": "conventions/",
    "docs/UE_SETUP.md": "unreal-setup/",
    "UE_SETUP.md": "unreal-setup/",
    "CHANGELOG.md": "changelog/",
}
EXTERNAL_TARGETS = {
    "LICENSE": f"{REPO_URL}/blob/main/LICENSE",
    "morennbridge/": f"{REPO_URL}/tree/main",
    "morennbridge/docs/": f"{REPO_URL}/tree/main/docs",
}


def rewrite_links(text, prefix):
    """Point repository-relative links and images at the generated site."""

    def link(match):
        label, target = match.group(1), match.group(2)
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
            anchor = "#" + anchor
        if target in LINK_TARGETS:
            return f"[{label}]({prefix}{LINK_TARGETS[target]}{anchor})"
        if target in EXTERNAL_TARGETS:
            return f"[{label}]({EXTERNAL_TARGETS[target]})"
        for key in ("docs/images/", "images/"):
            if target.startswith(key):
                return f"[{label}]({prefix}images/{target[len(key) :]})"
        return match.group(0)

    text = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r'src="(?:docs/)?images/', f'src="{prefix}images/', text)
    return text


def convert(md_text):
    converter = markdown.Markdown(
        extensions=["pymdownx.superfences", "tables", "toc", "attr_list", "md_in_html", "codehilite", "sane_lists"],
        extension_configs={
            "toc": {"toc_depth": "2-3", "permalink": False},
            "codehilite": {"css_class": "highlight", "guess_lang": False},
        },
    )
    body = converter.convert(md_text)
    return body, converter.toc_tokens


def strip_title(md_text):
    """Return (title, subtitle, rest): the first H1, an optional H3 subtitle right below it, the remainder."""
    lines = md_text.splitlines()
    title, subtitle = None, None
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        title = lines[i][2:].strip()
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].startswith("### "):
            subtitle = lines[i][4:].strip()
            i += 1
    return title, subtitle, "\n".join(lines[i:])


def toc_html(tokens):
    items = [t for t in tokens if t["level"] == 2]
    if len(items) < 3:
        return ""
    out = ['<nav class="toc" aria-label="On this page"><p class="toc-title">On this page</p><ul>']
    for t in items:
        out.append(f'<li><a href="#{t["id"]}">{html.escape(t["name"])}</a></li>')
    out.append("</ul></nav>")
    return "".join(out)


def nav_html(current_slug, prefix):
    parts = []
    for slug, _src, label, _eyebrow in PAGES:
        href = f"{prefix}{slug}/" if slug else (prefix or "./")
        cls = ' class="active"' if slug == current_slug else ""
        parts.append(f'<a href="{href}"{cls}>{html.escape(label)}</a>')
    return "".join(parts)


def build(out_dir):
    out_dir = pathlib.Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    template = (SITE_SRC / "template.html").read_text(encoding="utf-8")
    fonts = ""
    if (SITE_SRC / "static" / STUDIO_FONT).exists():
        fonts = (
            '  <style>@font-face { font-family: "Fliege Mono"; src: url("{{prefix}}' + STUDIO_FONT + '")'
            ' format("opentype"); font-weight: 100 300; font-style: normal; font-display: swap; }</style>\n'
        )
    shutil.copy(SITE_SRC / "style.css", out_dir / "style.css")
    for static in (SITE_SRC / "static").iterdir():
        shutil.copy(static, out_dir / static.name)
    images_src = ROOT / "docs" / "images"
    if images_src.exists():
        shutil.copytree(images_src, out_dir / "images", ignore=shutil.ignore_patterns("_*"))

    for slug, source, label, eyebrow in PAGES:
        depth = 1 if slug else 0
        prefix = "../" * depth
        md_text = (ROOT / source).read_text(encoding="utf-8")
        title, subtitle, rest = strip_title(md_text)
        rest = rewrite_links(rest, prefix)
        body, toc_tokens = convert(rest)
        page_title = title or label
        if slug:  # the wordmark already says MorennBridge; keep page titles short
            page_title = re.sub(r"^MorennBridge\s+|\s+for MorennBridge$", "", page_title)
            page_title = page_title[:1].upper() + page_title[1:]
        page = template
        for key, value in {
            "{{title}}": html.escape(page_title),
            "{{subtitle}}": html.escape(subtitle) if subtitle else "",
            "{{subtitle_block}}": f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else "",
            "{{eyebrow}}": html.escape(eyebrow),
            "{{nav}}": nav_html(slug, prefix),
            "{{toc}}": toc_html(toc_tokens),
            "{{content}}": body,
            "{{fonts}}": fonts.replace("{{prefix}}", prefix),
            "{{prefix}}": prefix,
            "{{home}}": prefix or "./",
            "{{repo}}": REPO_URL,
            "{{repo_slug}}": REPO_URL.removeprefix("https://github.com/"),
            "{{doc_title}}": html.escape(f"{page_title} | MorennBridge" if slug else "MorennBridge"),
        }.items():
            page = page.replace(key, value)
        target = out_dir / slug / "index.html" if slug else out_dir / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        print("wrote", target.relative_to(out_dir.parent))
    (out_dir / ".nojekyll").write_text("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site")
    build(parser.parse_args().out)
