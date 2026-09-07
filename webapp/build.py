#!/usr/bin/env python3
"""
Static site builder for the Daugavpils Music Archive website (ADR-0001).

Renders bands/**/*.yaml into plain HTML under webapp/dist/, once per
language in i18n.LANGS (default "ru" at the site root, others under
/<lang>/). No media (audio/image/video) is copied anywhere - the Site
links directly to archive.org, where each band/release's media lives as
its own item (see tools/archive_org.py, tools/publish_to_archive_org.py).

Requires the archive to already pass tools/validate.py, and every
referenced media file to already be published to archive.org - this
script refuses to build over a broken/incomplete bands/ tree, or over
media the Site would otherwise link to a 404.

Usage:
    python webapp/build.py

Set SITE_BASE_PATH to build for a host that doesn't serve the Site from
its domain root (e.g. a GitHub Pages project site at
https://<user>.github.io/<repo>/) - every site-internal link/asset path
gets this prefix. Leave unset (root-relative "/...") for daugavpils.fans
itself and for a GitHub Pages custom domain, both served at root.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_org import archive_org_url, band_item_id, release_item_id  # noqa: E402
from models import MusicAlbum, MusicGroup  # noqa: E402
from i18n import STRINGS, LANGS, DEFAULT_LANG, home_url, band_url, release_url, lang_prefix  # noqa: E402

BANDS_DIR = REPO_ROOT / "bands"
WEBAPP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
DIST_DIR = WEBAPP_DIR / "dist"
MAINTENANCE_MD = REPO_ROOT / "MAINTENANCE.md"
BASE_PATH = os.environ.get("SITE_BASE_PATH", "").rstrip("/")

MERMAID_FENCE_RE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
RELATIVE_MD_LINK_RE = re.compile(r"\]\((?!https?://)([^)]+\.md)\)")
GITHUB_BLOB_BASE = "https://github.com/svalevka/daugavpils.fans/blob/main/"


def format_duration(iso: str | None) -> str:
    """'PT1M13S' -> '1:13', 'PT50S' -> '0:50'."""
    if not iso:
        return ""
    body = iso[2:]
    minutes = 0
    if "M" in body:
        minutes_str, body = body.split("M", 1)
        minutes = int(minutes_str)
    seconds = int(body.rstrip("S")) if body else 0
    return f"{minutes}:{seconds:02d}"


def localize(obj, field: str, lang: str) -> str | None:
    """Pick obj.<field>_en for lang="en" (falling back to obj.<field> if no
    translation exists yet), else obj.<field>. Used for bilingual text fields
    (description, member name/role, image caption) that carry a Russian
    canonical value and an optional English translation."""
    base = getattr(obj, field, None)
    if lang == "en":
        return getattr(obj, f"{field}_en", None) or base
    return base


def license_label(url: str) -> str:
    """Best-effort human label for a Creative Commons license URL, else the raw URL."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    if "creativecommons.org" in url and "licenses" in parts:
        i = parts.index("licenses")
        variant = parts[i + 1].upper().replace("-", " ")
        version = parts[i + 2] if len(parts) > i + 2 else ""
        return f"CC {variant} {version}".strip()
    return url


def render_maintenance_html() -> str:
    """MAINTENANCE.md, rendered to HTML for the on-site /support/ page (see
    README's Support section) - same content, so it stays reachable even to
    people who'd never think to look on GitHub. Mermaid fences are pulled out
    before the markdown pass and reinserted as <pre class="mermaid"> blocks
    (HTML-escaped, so embedded diagram markup like `<br/>` survives as text
    for mermaid.js to parse - see templates/support.html) rather than being
    left for Python-Markdown's fenced-code handling, which would wrap them in
    <code> and defeat mermaid.js's `.mermaid` selector. Relative links to
    other repo files (e.g. `README.md`) are rewritten to GitHub blob URLs,
    since they'd otherwise resolve relative to /support/ on the Site."""
    text = MAINTENANCE_MD.read_text()
    text = RELATIVE_MD_LINK_RE.sub(lambda m: f"]({GITHUB_BLOB_BASE}{m.group(1)})", text)

    diagrams: list[str] = []

    def stash_mermaid(match: re.Match[str]) -> str:
        diagrams.append(match.group(1))
        return f'\n<div class="mermaid-placeholder" data-index="{len(diagrams) - 1}"></div>\n'

    text = MERMAID_FENCE_RE.sub(stash_mermaid, text)
    body = markdown.markdown(text, extensions=["tables"])

    for index, diagram in enumerate(diagrams):
        placeholder = f'<div class="mermaid-placeholder" data-index="{index}"></div>'
        block = f'<pre class="mermaid">\n{html.escape(diagram)}</pre>'
        body = body.replace(f"<p>{placeholder}</p>", block).replace(placeholder, block)

    return body


def to_jsonld(model: MusicGroup | MusicAlbum) -> str:
    """Pretty-printed schema.org JSON-LD for a band or release, straight from
    the same model that backs its band.yaml/release.yaml - embedded as a
    <script type="application/ld+json"> for crawlers."""
    data = model.model_dump(by_alias=True, exclude_none=True, mode="json")
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_band(band_dir: Path) -> MusicGroup:
    raw = yaml.safe_load((band_dir / "band.yaml").read_text())
    return MusicGroup.model_validate(raw)


def load_release(release_dir: Path) -> MusicAlbum:
    raw = yaml.safe_load((release_dir / "release.yaml").read_text())
    return MusicAlbum.model_validate(raw)


def band_media_url(band: MusicGroup, content_url: str) -> str:
    return archive_org_url(band_item_id(band.slug), content_url)


def release_media_url(band: MusicGroup, release: MusicAlbum, content_url: str) -> str:
    return archive_org_url(release_item_id(band.slug, release.slug), content_url)


def require_valid_archive() -> None:
    print("Validating archive (tools/validate.py)...")
    result = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "validate.py")])
    if result.returncode != 0:
        print(
            "\nAborted: the site can't be built over a broken archive. "
            "Fix the errors above, or run `python tools/validate.py --write` "
            "first if it's just missing checksums/duration/bitrate."
        )
        sys.exit(1)


def _missing_from_item(item_id: str, content_urls: list[str]) -> list[str]:
    import internetarchive as ia

    if not content_urls:
        return []
    present = {f["name"] for f in ia.get_item(item_id).files}
    return [f"{item_id}/{c}" for c in content_urls if c not in present]


def require_media_published(bands: list[MusicGroup], releases_by_band: dict[str, list[MusicAlbum]]) -> None:
    """The Site links directly to archive.org - it can never build a page
    linking to media that isn't there yet (see tools/publish_to_archive_org.py)."""
    print("Checking media is published to archive.org...")
    missing: list[str] = []
    for band in bands:
        missing.extend(
            _missing_from_item(band_item_id(band.slug), [m.contentUrl for m in band.image + band.video])
        )
        for release in releases_by_band[band.slug]:
            content_urls = [t.audio.contentUrl for t in release.track]
            content_urls += [m.contentUrl for m in release.image + release.video]
            missing.extend(_missing_from_item(release_item_id(band.slug, release.slug), content_urls))

    if missing:
        print("\nAborted: the following media isn't published to archive.org yet:\n")
        for m in missing:
            print(f"- {m}")
        print("\nRun `python tools/publish_to_archive_org.py` first.")
        sys.exit(1)


def build() -> None:
    require_valid_archive()

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)
    shutil.copytree(STATIC_DIR, DIST_DIR / "static")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["duration"] = format_duration
    env.filters["license_label"] = license_label
    env.globals["localize"] = localize
    env.globals["band_media_url"] = band_media_url
    env.globals["release_media_url"] = release_media_url
    env.globals["partial"] = partial

    band_dirs = sorted(p for p in BANDS_DIR.iterdir() if p.is_dir())
    bands = [load_band(d) for d in band_dirs]
    releases_by_band: dict[str, list[MusicAlbum]] = {}

    for band_dir, band in zip(band_dirs, bands):
        release_dirs = sorted(
            p for p in band_dir.iterdir() if p.is_dir() and (p / "release.yaml").exists()
        )
        releases_by_band[band.slug] = [load_release(d) for d in release_dirs]

    require_media_published(bands, releases_by_band)

    index_tmpl = env.get_template("index.html")
    band_tmpl = env.get_template("band.html")
    release_tmpl = env.get_template("release.html")

    for lang in LANGS:
        lang_root = DIST_DIR if lang == DEFAULT_LANG else DIST_DIR / lang
        lang_root.mkdir(parents=True, exist_ok=True)
        base_ctx = dict(lang=lang, t=STRINGS[lang], lang_prefix=lang_prefix(lang), base_path=BASE_PATH)

        (lang_root / "index.html").write_text(
            index_tmpl.render(
                **base_ctx,
                ru_url=home_url("ru", BASE_PATH),
                en_url=home_url("en", BASE_PATH),
                home_url=home_url(lang, BASE_PATH),
                bands=bands,
            )
        )

        for band in bands:
            band_out_dir = lang_root / "bands" / band.slug
            band_out_dir.mkdir(parents=True, exist_ok=True)
            releases = releases_by_band[band.slug]

            (band_out_dir / "index.html").write_text(
                band_tmpl.render(
                    **base_ctx,
                    ru_url=band_url("ru", band.slug, BASE_PATH),
                    en_url=band_url("en", band.slug, BASE_PATH),
                    home_url=home_url(lang, BASE_PATH),
                    band=band,
                    releases=releases,
                    jsonld=to_jsonld(band),
                )
            )

            for release in releases:
                release_out_dir = band_out_dir / release.slug
                release_out_dir.mkdir(parents=True, exist_ok=True)
                (release_out_dir / "index.html").write_text(
                    release_tmpl.render(
                        **base_ctx,
                        ru_url=release_url("ru", band.slug, release.slug, BASE_PATH),
                        en_url=release_url("en", band.slug, release.slug, BASE_PATH),
                        home_url=home_url(lang, BASE_PATH),
                        band=band,
                        release=release,
                        jsonld=to_jsonld(release),
                    )
                )

        print(f"  built [{lang}]: {len(bands)} band(s)")

    support_tmpl = env.get_template("support.html")
    support_out_dir = DIST_DIR / "support"
    support_out_dir.mkdir(parents=True, exist_ok=True)
    (support_out_dir / "index.html").write_text(
        support_tmpl.render(
            lang=DEFAULT_LANG,
            t=STRINGS[DEFAULT_LANG],
            lang_prefix=lang_prefix(DEFAULT_LANG),
            base_path=BASE_PATH,
            ru_url=f"{BASE_PATH}/support/",
            en_url=f"{BASE_PATH}/support/",
            home_url=home_url(DEFAULT_LANG, BASE_PATH),
            content=render_maintenance_html(),
        )
    )
    print("  built: /support/ (from MAINTENANCE.md)")

    print(f"\nBuilt site into {DIST_DIR}")


if __name__ == "__main__":
    build()
