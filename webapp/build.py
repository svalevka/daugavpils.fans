#!/usr/bin/env python3
"""
Static site builder for the Daugavpils Music Archive website (ADR-0001).

Renders bands/**/*.yaml into plain HTML under webapp/dist/, once per
language in i18n.LANGS (default "ru" at the site root, others under
/<lang>/). Media files are copied exactly once, mirroring bands/, and every
page - in every language - references them by absolute path (e.g.
/bands/m-spirit/media/x.webp), since they aren't duplicated per language.

Requires the archive to already pass tools/validate.py - this script
refuses to build over a broken or incomplete bands/ tree.

Usage:
    python webapp/build.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import MusicAlbum, MusicGroup  # noqa: E402
from i18n import STRINGS, LANGS, DEFAULT_LANG, home_url, band_url, release_url, lang_prefix  # noqa: E402

BANDS_DIR = REPO_ROOT / "bands"
WEBAPP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
DIST_DIR = WEBAPP_DIR / "dist"


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


def band_jsonld(band: MusicGroup) -> str:
    """Pretty-printed schema.org JSON-LD for a band, straight from the same
    model that backs band.yaml - exposed on the band page for transparency/
    education, not just for crawlers."""
    data = band.model_dump(by_alias=True, exclude_none=True, mode="json")
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_band(band_dir: Path) -> MusicGroup:
    raw = yaml.safe_load((band_dir / "band.yaml").read_text())
    return MusicGroup.model_validate(raw)


def load_release(release_dir: Path) -> MusicAlbum:
    raw = yaml.safe_load((release_dir / "release.yaml").read_text())
    return MusicAlbum.model_validate(raw)


def copy_media(src_dir: Path, dst_dir: Path, media_items) -> None:
    for item in media_items:
        src = src_dir / item.contentUrl
        dst = dst_dir / item.contentUrl
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


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

    band_dirs = sorted(p for p in BANDS_DIR.iterdir() if p.is_dir())
    bands = [load_band(d) for d in band_dirs]
    releases_by_band: dict[str, list[MusicAlbum]] = {}

    # Media is language-independent: copy it exactly once, mirroring bands/.
    for band_dir, band in zip(band_dirs, bands):
        band_dist_dir = DIST_DIR / "bands" / band.slug
        band_dist_dir.mkdir(parents=True)
        copy_media(band_dir, band_dist_dir, band.image)
        copy_media(band_dir, band_dist_dir, band.video)

        release_dirs = sorted(
            p for p in band_dir.iterdir() if p.is_dir() and (p / "release.yaml").exists()
        )
        releases = [load_release(d) for d in release_dirs]
        releases_by_band[band.slug] = releases

        for release_dir, release in zip(release_dirs, releases):
            release_dist_dir = band_dist_dir / release.slug
            release_dist_dir.mkdir(parents=True)
            copy_media(release_dir, release_dist_dir, [t.audio for t in release.track])
            copy_media(release_dir, release_dist_dir, release.image)
            copy_media(release_dir, release_dist_dir, release.video)

    index_tmpl = env.get_template("index.html")
    band_tmpl = env.get_template("band.html")
    release_tmpl = env.get_template("release.html")

    for lang in LANGS:
        lang_root = DIST_DIR if lang == DEFAULT_LANG else DIST_DIR / lang
        lang_root.mkdir(parents=True, exist_ok=True)
        base_ctx = dict(lang=lang, t=STRINGS[lang], lang_prefix=lang_prefix(lang))

        (lang_root / "index.html").write_text(
            index_tmpl.render(
                **base_ctx,
                ru_url=home_url("ru"),
                en_url=home_url("en"),
                home_url=home_url(lang),
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
                    ru_url=band_url("ru", band.slug),
                    en_url=band_url("en", band.slug),
                    home_url=home_url(lang),
                    band=band,
                    releases=releases,
                    jsonld=band_jsonld(band),
                )
            )

            for release in releases:
                release_out_dir = band_out_dir / release.slug
                release_out_dir.mkdir(parents=True, exist_ok=True)
                (release_out_dir / "index.html").write_text(
                    release_tmpl.render(
                        **base_ctx,
                        ru_url=release_url("ru", band.slug, release.slug),
                        en_url=release_url("en", band.slug, release.slug),
                        home_url=home_url(lang),
                        band=band,
                        release=release,
                    )
                )

        print(f"  built [{lang}]: {len(bands)} band(s)")

    print(f"\nBuilt site into {DIST_DIR}")


if __name__ == "__main__":
    build()
