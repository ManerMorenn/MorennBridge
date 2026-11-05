# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the documentation website (GitHub Pages) from the Markdown files in the repository.

    python dev/build_site.py --out site

Pages are generated from README.md, docs/*.md, CHANGELOG.md and the footer pages in dev/site; images are copied from
docs/images.
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
SITE_URL = "https://morennbridge.manermorenn.com"
# Drop this file into dev/site/static to use the studio typeface instead of IBM Plex Mono.
STUDIO_FONT = "fliegemono-thin.otf"

# (slug, source file, navigation label, eyebrow label); slug "" is the home page
PAGES = [
    ("", "README.md", "Overview", "Blender to Unreal Engine 5"),
    ("guide", "docs/USER_GUIDE.md", "User guide", "Documentation"),
    ("tutorials", "docs/TUTORIALS.md", "Tutorials", "Documentation"),
    ("conventions", "docs/CONVENTIONS.md", "Conventions", "Reference"),
    ("issues", "docs/ISSUES.md", "Issue codes", "Reference"),
    ("unreal-setup", "docs/UE_SETUP.md", "Unreal setup", "Reference"),
    ("support", "docs/SUPPORT.md", "Support", "Help"),
    ("changelog", "CHANGELOG.md", "Changelog", "Releases"),
]
# Linked from the footer only, not from the navigation
FOOTER_PAGES = [
    ("legal", "dev/site/legal.md", "Legal notice", "Legal"),
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
    "docs/TUTORIALS.md": "tutorials/",
    "TUTORIALS.md": "tutorials/",
    "docs/ISSUES.md": "issues/",
    "ISSUES.md": "issues/",
    "docs/SUPPORT.md": "support/",
    "SUPPORT.md": "support/",
    "CHANGELOG.md": "changelog/",
}
EXTERNAL_TARGETS = {
    "LICENSE": f"{REPO_URL}/blob/main/LICENSE",
    "CONTRIBUTING.md": f"{REPO_URL}/blob/main/CONTRIBUTING.md",
    "NOTICE.md": f"{REPO_URL}/blob/main/NOTICE.md",
    "morennbridge/": f"{REPO_URL}/tree/main",
    "morennbridge/docs/": f"{REPO_URL}/tree/main/docs",
}


def rewrite_links(text, prefix):
    """Point repository-relative links and images at the generated site."""

    def link(match):
        target = match.group(1)
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
            anchor = "#" + anchor
        if target in LINK_TARGETS:
            return f"]({prefix}{LINK_TARGETS[target] or './'}{anchor})"
        if target in EXTERNAL_TARGETS:
            return f"]({EXTERNAL_TARGETS[target]})"
        for key in ("docs/images/", "images/"):
            if target.startswith(key):
                return f"]({prefix}images/{target[len(key) :]})"
        return match.group(0)

    text = re.sub(r"\]\(([^)\s]+)\)", link, text)
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
    badge = r'(?:<a [^>]*>)?<img [^>]*src="[^"]*(?:shields\.io|badge\.svg)[^"]*"[^>]*>(?:</a>)?'
    body = re.sub(rf"<p>((?:\s*{badge})+\s*)</p>", r'<p class="badges">\1</p>', body)
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


def first_sentence(md_text):
    """The opening sentence of a page, without Markdown, for search engines and link previews."""
    for block in md_text.split("\n\n"):
        block = block.strip()
        if block and block[0].isalpha():
            plain = re.sub(r"[*`]|\[|\]\([^)]*\)", "", " ".join(block.split()))
            return plain.split(". ")[0].rstrip(".") + "."
    return ""


def render(template, fonts, prefix, url, **values):
    page = template
    for key, value in {
        "{{fonts}}": fonts.replace("{{prefix}}", prefix),
        "{{prefix}}": prefix,
        "{{home}}": prefix or "./",
        "{{repo}}": REPO_URL,
        "{{repo_slug}}": REPO_URL.removeprefix("https://github.com/"),
        "{{site}}": SITE_URL,
        "{{canonical}}": url,
        **values,
    }.items():
        page = page.replace(key, value)
    return page


def write_site_files(out_dir, urls):
    entries = "\n".join(f"  <url><loc>{url}</loc></url>" for url in urls)
    (out_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n',
        encoding="utf-8",
    )
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")


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
    shutil.copytree(SITE_SRC / "static", out_dir, dirs_exist_ok=True)
    social_image = ROOT / "docs" / "listing" / "social-preview.png"
    if social_image.exists():
        shutil.copy(social_image, out_dir / "social.png")
    images_src = ROOT / "docs" / "images"
    if images_src.exists():
        shutil.copytree(images_src, out_dir / "images", ignore=shutil.ignore_patterns("_*"))

    urls = []
    for slug, source, label, eyebrow in PAGES + FOOTER_PAGES:
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
        doc_title = f"{page_title} | MorennBridge" if slug else "MorennBridge"
        url = f"{SITE_URL}/{slug}/" if slug else f"{SITE_URL}/"
        urls.append(url)
        page = render(
            template,
            fonts,
            prefix,
            url,
            **{
                "{{title}}": html.escape(page_title),
                "{{subtitle_block}}": f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else "",
                "{{eyebrow}}": html.escape(eyebrow),
                "{{nav}}": nav_html(slug, prefix),
                "{{toc}}": toc_html(toc_tokens),
                "{{content}}": body,
                "{{doc_title}}": html.escape(doc_title),
                "{{description}}": html.escape(first_sentence(rest) or page_title, quote=True),
            },
        )
        target = out_dir / slug / "index.html" if slug else out_dir / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        print("wrote", target.relative_to(out_dir.parent))
    missing = render(
        template,
        fonts,
        "/",
        f"{SITE_URL}/404.html",
        **{
            "{{title}}": "Not found",
            "{{subtitle_block}}": "",
            "{{eyebrow}}": "404",
            "{{nav}}": nav_html(None, "/"),
            "{{toc}}": "",
            "{{content}}": '<p>This page does not exist. Start from the <a href="/guide/">user guide</a>.</p>',
            "{{doc_title}}": "Not found | MorennBridge",
            "{{description}}": "This page does not exist.",
        },
    )
    (out_dir / "404.html").write_text(missing, encoding="utf-8")
    write_site_files(out_dir, urls)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site")
    build(parser.parse_args().out)
