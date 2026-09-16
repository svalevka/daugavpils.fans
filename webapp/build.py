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

Set SITE_SKIP_LOCAL_VALIDATION on a checkout with no local media tree
(e.g. CI - see .github/workflows/pages.yml and ADR-0002): it skips
tools/validate.py's local checksum re-check, which would otherwise fail
on every file (they're all gitignored - see README). The archive.org
existence check below (require_media_published) still runs and is the
real gate, since publish_to_archive_org.py never lets a file reach
archive.org without having already passed validate.py for real, against
the maintainer's actual local files, at publish time.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from functools import partial
from itertools import zip_longest
from pathlib import Path

import markdown
import unidecode
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_org import archive_org_url, band_item_id, release_item_id  # noqa: E402
from models import MusicAlbum, MusicGroup  # noqa: E402
from i18n import (  # noqa: E402
    STRINGS,
    LANGS,
    DEFAULT_LANG,
    home_url,
    band_url,
    release_url,
    band_media_page_url as band_media_page_url_fn,
    release_media_page_url as release_media_page_url_fn,
    members_index_url as members_index_url_fn,
    member_url as member_url_fn,
    lang_prefix,
)

BANDS_DIR = REPO_ROOT / "bands"
WEBAPP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
DIST_DIR = WEBAPP_DIR / "dist"
MAINTENANCE_MD = {
    "ru": REPO_ROOT / "MAINTENANCE.md",
    "en": REPO_ROOT / "MAINTENANCE-EN.md",
}
BASE_PATH = os.environ.get("SITE_BASE_PATH", "").rstrip("/")
SITE_URL = os.environ.get("SITE_URL", "https://daugavpils.fans").rstrip("/")


def og_snippet(text: str | None, fallback: str, max_length: int = 200) -> str:
    """Generate a clean, single-line text snippet for Open Graph / meta descriptions."""
    if not text:
        return fallback
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    truncated = cleaned[: max_length - 3]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}..."

# How much media a band/release page shows inline before linking out to its
# own /media/ page (see issue #27) - kept as constants shared between the
# build script (which decides whether to build a /media/ page at all) and
# the band/release templates (which decide whether to show the "view all"
# link), so the two can't drift apart.
PHOTO_TEASER_LIMIT = 6
VIDEO_TEASER_LIMIT = 2

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


def localize_list(obj, field: str, lang: str) -> list[str]:
    """List-field counterpart to localize() - for MusicAlbum.creditText/
    creditText_en (GitHub issue #42), the one bilingual field that's a list
    of independent lines rather than one string, so there's no single
    obj.<field>/<field>_en pair to fall back between as a whole: each line
    needs its own fallback. tools/models.py's MusicAlbum enforces
    creditText_en is never longer than creditText (it translates a
    *prefix* of it, 1:1 by position - entries beyond that just aren't
    translated yet), so this must use zip_longest, not zip: a plain zip
    would silently drop every untranslated tail entry instead of falling
    back to its native-script text."""
    base = getattr(obj, field, None) or []
    if lang != "en":
        return base
    translated = getattr(obj, f"{field}_en", None) or []
    if not translated:
        return base
    return [en or ru for ru, en in zip_longest(base, translated, fillvalue=None)]


def license_label(url: str) -> str:
    """Best-effort human label for a Creative Commons license URL, else the raw URL."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    if "creativecommons.org" in url and "licenses" in parts:
        i = parts.index("licenses")
        variant = parts[i + 1].upper().replace("-", " ")
        version = parts[i + 2] if len(parts) > i + 2 else ""
        return f"CC {variant} {version}".strip()
    return url


ALIAS_MAP: dict[str, str] = {
    "вантуз": "aleksei-kras-ko",
    "гоблин": "ruslan-kondrus",
    "александр «рыб»": "aleksandr-rybakov",
    "вадим неменущий (вадер)": "vadim-neminushchii",
    "дерево": "evgenii-ershov",
    "андрон": "andrei-fatkhutdinov",
    "андрон фатхут": "andrei-fatkhutdinov",
    "алвин": "alvin-matisans",
    "алвин м.": "alvin-matisans",
    "янсон": "aivar-ianson",
    "стас": "stas-shimanskii",
}


def extract_clean_name_and_nicknames(name: str) -> tuple[str, list[str]]:
    """Extract clean canonical name and any embedded nicknames from quotes or parentheses."""
    nicks: list[str] = []
    for q in re.findall(r"[«\"“]([^»\"”]+)[»\"”]", name):
        n = q.strip()
        if n:
            nicks.append(n)
    for p in re.findall(r"\(([^)]+)\)", name):
        n = p.strip()
        if n:
            nicks.append(n)
    cleaned = re.sub(r"[«\"“][^»\"”]+[»\"”]", "", name)
    cleaned = re.sub(r"\([^)]+\)", "", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        cleaned = (
            name.replace("«", "")
            .replace("»", "")
            .replace('"', "")
            .replace("“", "")
            .replace("”", "")
            .strip(" ()")
        )
    return cleaned, nicks


def canonical_musician_slug(name: str) -> str:
    """Generate a stable, cross-referenced musician slug."""
    key = name.strip().lower()
    if key in ALIAS_MAP:
        return ALIAS_MAP[key]
    cleaned, _ = extract_clean_name_and_nicknames(name)
    trans = unidecode.unidecode(cleaned).lower()
    return re.sub(r"[^a-z0-9]+", "-", trans).strip("-")


def index_musicians(bands: list[MusicGroup]) -> dict[str, dict]:
    """Index all musician memberships across bands into a cross-referenced scene graph."""
    musicians: dict[str, dict] = {}
    for band in bands:
        for m in band.member:
            slug = canonical_musician_slug(m.name)
            if slug not in musicians:
                musicians[slug] = {
                    "slug": slug,
                    "names_ru": [],
                    "names_en": [],
                    "nicks_ru": [],
                    "nicks_en": [],
                    "memberships": [],
                }

            clean_ru, nicks_ru = extract_clean_name_and_nicknames(m.name)
            musicians[slug]["names_ru"].append(clean_ru)
            musicians[slug]["nicks_ru"].extend(nicks_ru)

            if m.name_en:
                clean_en, nicks_en = extract_clean_name_and_nicknames(m.name_en)
                musicians[slug]["names_en"].append(clean_en)
                musicians[slug]["nicks_en"].extend(nicks_en)

            musicians[slug]["memberships"].append({
                "band_slug": band.slug,
                "band_name": band.name,
                "band_name_en": getattr(band, "name_en", None) or band.name,
                "role": m.role,
                "role_en": m.role_en or m.role,
                "period": m.period,
            })

    return musicians


def prepare_musicians_view(musicians: dict[str, dict], lang: str) -> list[dict]:
    """Prepare localized musician directory and collaborator graph for rendering."""
    prepared: list[dict] = []

    intermediate: dict[str, dict] = {}
    for slug, m_data in musicians.items():
        c_ru = Counter(m_data["names_ru"])
        best_ru = max(c_ru.keys(), key=lambda s: (len(s.split()), c_ru[s], len(s)))

        c_en = Counter(m_data["names_en"])
        if c_en:
            best_en = max(c_en.keys(), key=lambda s: (len(s.split()), c_en[s], len(s)))
        else:
            best_en = unidecode.unidecode(best_ru)

        display_name = best_en if lang == "en" else best_ru

        alt_names: list[str] = []
        if lang == "en":
            candidates = m_data["nicks_en"] + m_data["names_en"]
            if not candidates:
                candidates = [unidecode.unidecode(n) for n in m_data["nicks_ru"] + m_data["names_ru"]]
        else:
            candidates = m_data["nicks_ru"] + m_data["names_ru"]

        for cand in candidates:
            if cand and cand != display_name and cand not in alt_names:
                alt_names.append(cand)

        bands_list: list[dict] = []
        seen_bands: set[tuple[str, str | None]] = set()
        for mem in m_data["memberships"]:
            band_name = mem["band_name_en"] if lang == "en" else mem["band_name"]
            display_role = mem["role_en"] if lang == "en" else mem["role"]
            period = mem["period"]
            band_key = (mem["band_slug"], period)
            if band_key not in seen_bands:
                seen_bands.add(band_key)
                bands_list.append({
                    "band_slug": mem["band_slug"],
                    "band_name": band_name,
                    "role": mem["role"],
                    "display_role": display_role,
                    "period": period,
                })

        intermediate[slug] = {
            "slug": slug,
            "display_name": display_name,
            "alternate_names": alt_names,
            "bands": bands_list,
            "band_slugs": {b["band_slug"] for b in bands_list},
        }

    for slug, item in intermediate.items():
        my_band_slugs = item["band_slugs"]
        collaborators: list[dict] = []
        for other_slug, other_item in intermediate.items():
            if other_slug == slug:
                continue
            shared_slugs = my_band_slugs & other_item["band_slugs"]
            if shared_slugs:
                shared_band_names: list[str] = []
                for b in item["bands"]:
                    if b["band_slug"] in shared_slugs and b["band_name"] not in shared_band_names:
                        shared_band_names.append(b["band_name"])
                collaborators.append({
                    "slug": other_slug,
                    "display_name": other_item["display_name"],
                    "shared_bands": shared_band_names,
                })

        collaborators.sort(key=lambda c: (-len(c["shared_bands"]), c["display_name"]))

        prepared.append({
            "slug": slug,
            "display_name": item["display_name"],
            "alternate_names": item["alternate_names"],
            "bands": item["bands"],
            "collaborators": collaborators,
        })

    prepared.sort(key=lambda m: m["display_name"].lower())
    return prepared



def render_maintenance_html(lang: str) -> str:
    """MAINTENANCE.md (or its MAINTENANCE-EN.md translation), rendered to HTML
    for the on-site /support/ page (see README's Support section) - same
    content, so it stays reachable even to people who'd never think to look
    on GitHub. Mermaid fences are pulled out before the markdown pass and
    reinserted as <pre class="mermaid"> blocks (HTML-escaped, so embedded
    diagram markup like `<br/>` survives as text for mermaid.js to parse -
    see templates/support.html) rather than being left for Python-Markdown's
    fenced-code handling, which would wrap them in <code> and defeat
    mermaid.js's `.mermaid` selector. Relative links to other repo files
    (e.g. `README.md`) are rewritten to GitHub blob URLs, since they'd
    otherwise resolve relative to /support/ on the Site."""
    text = MAINTENANCE_MD[lang].read_text()
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


def visible_same_as(model: MusicGroup | MusicAlbum) -> list[str]:
    """sameAs URLs worth showing a human in the "Ещё" list - torrent links
    stay in band.yaml/release.yaml metadata (and the JSON-LD) but aren't
    surfaced on the page itself."""
    return [url for url in model.sameAs if not url.endswith(".torrent")]


def to_jsonld(model: MusicGroup | MusicAlbum) -> str:
    """Pretty-printed schema.org JSON-LD for a band or release, straight from
    the same model that backs its band.yaml/release.yaml - embedded as a
    <script type="application/ld+json"> for crawlers."""
    data = model.model_dump(by_alias=True, exclude_none=True, mode="json")
    return json.dumps(data, ensure_ascii=False, indent=2).replace("<", "\\u003c")


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


def needs_media_page(images: list, videos: list) -> bool:
    return len(images) > PHOTO_TEASER_LIMIT or len(videos) > VIDEO_TEASER_LIMIT


def require_valid_archive() -> None:
    if os.environ.get("SITE_SKIP_LOCAL_VALIDATION"):
        print(
            "Skipping tools/validate.py (SITE_SKIP_LOCAL_VALIDATION set - no local "
            "media tree here). require_media_published() below is the integrity gate "
            "instead - see this file's module docstring."
        )
        return
    print("Validating archive (tools/validate.py)...")
    result = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "validate.py")])
    if result.returncode != 0:
        print(
            "\nAborted: the site can't be built over a broken archive. "
            "Fix the errors above, or run `python tools/validate.py --write` "
            "first if it's just missing checksums/duration/bitrate."
        )
        sys.exit(1)


class ArchiveOrgUnavailableError(Exception):
    pass


def _missing_from_item(item_id: str, content_urls: list[str]) -> list[str]:
    import internetarchive as ia

    if not content_urls:
        return []
    try:
        present = {f["name"] for f in ia.get_item(item_id).files}
    except Exception as exc:
        print(f"  WARNING: unable to verify files on archive.org for {item_id} ({exc}); skipping archive check", file=sys.stderr)
        raise ArchiveOrgUnavailableError(f"archive.org metadata error on {item_id}: {exc}") from exc
    return [f"{item_id}/{c}" for c in content_urls if c not in present]


def require_media_published(bands: list[MusicGroup], releases_by_band: dict[str, list[MusicAlbum]]) -> None:
    """The Site links directly to archive.org - it can never build a page
    linking to media that isn't there yet (see tools/publish_to_archive_org.py)."""
    import urllib.request

    print("Checking media is published to archive.org...", flush=True)
    try:
        urllib.request.urlopen("https://archive.org/metadata/daugavpils-fans-dvinsk", timeout=3)
    except Exception as exc:
        print(f"  WARNING: archive.org is unreachable ({exc}); skipping media publication check", file=sys.stderr, flush=True)
        return

    missing: list[str] = []
    try:
        for band in bands:
            missing.extend(
                _missing_from_item(band_item_id(band.slug), [m.contentUrl for m in band.image + band.video])
            )
            for release in releases_by_band[band.slug]:
                content_urls = [t.audio.contentUrl for t in release.track if t.audio is not None]
                content_urls += [m.contentUrl for m in release.image + release.video]
                missing.extend(_missing_from_item(release_item_id(band.slug, release.slug), content_urls))
    except ArchiveOrgUnavailableError:
        return

    if missing:
        print("\nAborted: the following media isn't published to archive.org yet:\n")
        for m in missing:
            print(f"- {m}")
        print("\nRun `python tools/publish_to_archive_org.py` first.")
        sys.exit(1)


def generate_search_index(
    lang: str,
    bands: list[MusicGroup],
    releases_by_band: dict[str, list[MusicAlbum]],
    base_path: str = "",
) -> list[dict]:
    """Compile a compact, client-side static search index for bands, releases, and tracks."""
    items: list[dict] = []
    for band in bands:
        years = ""
        if band.foundingDate and band.dissolutionDate:
            years = f"{band.foundingDate}-{band.dissolutionDate}"
        elif band.foundingDate:
            years = band.foundingDate

        if lang == "en":
            members = [m.name_en or m.name for m in band.member]
            roles = [m.role_en or m.role for m in band.member if (m.role or m.role_en)]
        else:
            members = [m.name for m in band.member]
            roles = [m.role for m in band.member if m.role]

        band_item: dict = {
            "type": "band",
            "name": band.name,
            "url": band_url(lang, band.slug, base_path),
            "genres": list(band.genre or []),
            "years": years,
            "members": members,
        }
        if roles:
            band_item["roles"] = roles
        if band.alternateName:
            band_item["alternate_names"] = list(band.alternateName)
        items.append(band_item)

        for release in releases_by_band.get(band.slug, []):
            release_item: dict = {
                "type": "release",
                "name": release.name,
                "band": band.name,
                "year": release.datePublished,
                "url": release_url(lang, band.slug, release.slug, base_path),
            }
            if release.genre:
                release_item["genres"] = list(release.genre)
            credits = localize_list(release, "creditText", lang)
            if credits:
                release_item["credits"] = credits
            items.append(release_item)

            for track in release.track:
                track_item: dict = {
                    "type": "track",
                    "name": track.name,
                    "band": band.name,
                    "release": release.name,
                    "url": f"{release_url(lang, band.slug, release.slug, base_path)}#track-{track.position}",
                }
                if track.alternateName:
                    track_item["alternate_name"] = track.alternateName
                items.append(track_item)

    return items


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
    env.globals["localize_list"] = localize_list
    env.globals["musician_slug"] = canonical_musician_slug
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

    musicians_by_slug = index_musicians(bands)

    index_tmpl = env.get_template("index.html")
    band_tmpl = env.get_template("band.html")
    release_tmpl = env.get_template("release.html")
    media_tmpl = env.get_template("media.html")
    members_tmpl = env.get_template("members.html")
    member_tmpl = env.get_template("member.html")

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
                og_title=f"{STRINGS[lang]['archive_title']} — daugavpils.fans",
                og_description=STRINGS[lang]["site_tagline"],
                og_image_url=None,
                og_type="website",
                canonical_url=f"{SITE_URL}{home_url(lang, BASE_PATH)}",
            )
        )

        for band in bands:
            band_out_dir = lang_root / "bands" / band.slug
            band_out_dir.mkdir(parents=True, exist_ok=True)
            releases = releases_by_band[band.slug]

            band_has_media_page = needs_media_page(band.image, band.video)
            band_media_page_url = (
                band_media_page_url_fn(lang, band.slug, BASE_PATH) if band_has_media_page else None
            )

            (band_out_dir / "index.html").write_text(
                band_tmpl.render(
                    **base_ctx,
                    ru_url=band_url("ru", band.slug, BASE_PATH),
                    en_url=band_url("en", band.slug, BASE_PATH),
                    home_url=home_url(lang, BASE_PATH),
                    band=band,
                    releases=releases,
                    jsonld=to_jsonld(band),
                    visible_same_as=visible_same_as(band),
                    photo_teaser_limit=PHOTO_TEASER_LIMIT,
                    video_teaser_limit=VIDEO_TEASER_LIMIT,
                    media_page_url=band_media_page_url,
                    og_title=f"{band.name} — daugavpils.fans",
                    og_description=og_snippet(
                        localize(band, "description", lang),
                        f"{band.name} — {band.location or 'Daugavpils, Latvia'}",
                    ),
                    og_image_url=band_media_url(band, band.image[0].contentUrl) if band.image else None,
                    og_type="music.band",
                    canonical_url=f"{SITE_URL}{band_url(lang, band.slug, BASE_PATH)}",
                )
            )

            if band_has_media_page:
                band_media_out_dir = band_out_dir / "media"
                band_media_out_dir.mkdir(parents=True, exist_ok=True)
                (band_media_out_dir / "index.html").write_text(
                    media_tmpl.render(
                        **base_ctx,
                        ru_url=band_media_page_url_fn("ru", band.slug, BASE_PATH),
                        en_url=band_media_page_url_fn("en", band.slug, BASE_PATH),
                        home_url=home_url(lang, BASE_PATH),
                        heading=band.name,
                        back_url=band_url(lang, band.slug, BASE_PATH),
                        back_label=band.name,
                        images=band.image,
                        videos=band.video,
                        media_url=partial(band_media_url, band),
                        og_title=f"{band.name} — {STRINGS[lang]['photos_and_video']} — daugavpils.fans",
                        og_description=f"{band.name} — {STRINGS[lang]['photos_and_video']}",
                        og_image_url=band_media_url(band, band.image[0].contentUrl) if band.image else None,
                        og_type="website",
                        canonical_url=f"{SITE_URL}{band_media_page_url}",
                    )
                )

            for release in releases:
                release_out_dir = band_out_dir / release.slug
                release_out_dir.mkdir(parents=True, exist_ok=True)

                release_has_media_page = needs_media_page(release.image, release.video)
                release_media_page_url = (
                    release_media_page_url_fn(lang, band.slug, release.slug, BASE_PATH)
                    if release_has_media_page
                    else None
                )

                rel_og_image = (
                    release_media_url(band, release, release.image[0].contentUrl)
                    if release.image
                    else (band_media_url(band, band.image[0].contentUrl) if band.image else None)
                )

                rel_item_id = release_item_id(band.slug, release.slug)
                rel_torrent_url = next((url for url in release.sameAs if url.endswith(".torrent")), None)
                if not rel_torrent_url and release.has_audio:
                    rel_torrent_url = f"https://archive.org/download/{rel_item_id}/{rel_item_id}_archive.torrent"
                rel_zip_url = (
                    f"https://archive.org/compress/{rel_item_id}/formats=VBR%20MP3,JPEG&file=/{rel_item_id}.zip"
                    if release.has_audio
                    else None
                )

                (release_out_dir / "index.html").write_text(
                    release_tmpl.render(
                        **base_ctx,
                        ru_url=release_url("ru", band.slug, release.slug, BASE_PATH),
                        en_url=release_url("en", band.slug, release.slug, BASE_PATH),
                        home_url=home_url(lang, BASE_PATH),
                        band=band,
                        release=release,
                        jsonld=to_jsonld(release),
                        visible_same_as=visible_same_as(release),
                        photo_teaser_limit=PHOTO_TEASER_LIMIT,
                        video_teaser_limit=VIDEO_TEASER_LIMIT,
                        media_page_url=release_media_page_url,
                        torrent_url=rel_torrent_url,
                        zip_url=rel_zip_url,
                        og_title=f"{release.name} — {band.name} — daugavpils.fans",
                        og_description=og_snippet(
                            localize(release, "description", lang),
                            f"{release.name} ({release.datePublished}) — {band.name}",
                        ),
                        og_image_url=rel_og_image,
                        og_type="music.album",
                        canonical_url=f"{SITE_URL}{release_url(lang, band.slug, release.slug, BASE_PATH)}",
                    )
                )

                if release_has_media_page:
                    release_media_out_dir = release_out_dir / "media"
                    release_media_out_dir.mkdir(parents=True, exist_ok=True)
                    (release_media_out_dir / "index.html").write_text(
                        media_tmpl.render(
                            **base_ctx,
                            ru_url=release_media_page_url_fn(lang, band.slug, release.slug, BASE_PATH),
                            en_url=release_media_page_url_fn(lang, band.slug, release.slug, BASE_PATH),
                            home_url=home_url(lang, BASE_PATH),
                            heading=release.name,
                            back_url=release_url(lang, band.slug, release.slug, BASE_PATH),
                            back_label=release.name,
                            images=release.image,
                            videos=release.video,
                            media_url=partial(release_media_url, band, release),
                            og_title=f"{release.name} — {STRINGS[lang]['photos_and_video']} — daugavpils.fans",
                            og_description=f"{release.name} — {STRINGS[lang]['photos_and_video']}",
                            og_image_url=rel_og_image,
                            og_type="website",
                            canonical_url=f"{SITE_URL}{release_media_page_url}",
                        )
                    )

        musicians_view = prepare_musicians_view(musicians_by_slug, lang)
        members_out_dir = lang_root / "members"
        members_out_dir.mkdir(parents=True, exist_ok=True)

        (members_out_dir / "index.html").write_text(
            members_tmpl.render(
                **base_ctx,
                ru_url=members_index_url_fn("ru", BASE_PATH),
                en_url=members_index_url_fn("en", BASE_PATH),
                home_url=home_url(lang, BASE_PATH),
                musicians=musicians_view,
                og_title=f"{STRINGS[lang]['musicians']} — daugavpils.fans",
                og_description=STRINGS[lang]["musicians_directory_desc"],
                og_image_url=None,
                og_type="website",
                canonical_url=f"{SITE_URL}{members_index_url_fn(lang, BASE_PATH)}",
            )
        )

        for m_view in musicians_view:
            musician_out_dir = members_out_dir / m_view["slug"]
            musician_out_dir.mkdir(parents=True, exist_ok=True)
            band_names_str = ", ".join(b["band_name"] for b in m_view["bands"])
            (musician_out_dir / "index.html").write_text(
                member_tmpl.render(
                    **base_ctx,
                    ru_url=member_url_fn("ru", m_view["slug"], BASE_PATH),
                    en_url=member_url_fn("en", m_view["slug"], BASE_PATH),
                    home_url=home_url(lang, BASE_PATH),
                    members_index_url=members_index_url_fn(lang, BASE_PATH),
                    musician=m_view,
                    collaborators=m_view["collaborators"],
                    og_title=f"{m_view['display_name']} — daugavpils.fans",
                    og_description=og_snippet(
                        f"{m_view['display_name']} — {band_names_str}",
                        f"{m_view['display_name']} — daugavpils.fans",
                    ),
                    og_image_url=None,
                    og_type="profile",
                    canonical_url=f"{SITE_URL}{member_url_fn(lang, m_view['slug'], BASE_PATH)}",
                )
            )

        search_index = generate_search_index(lang, bands, releases_by_band, BASE_PATH)
        (lang_root / "search-index.json").write_text(
            json.dumps(search_index, ensure_ascii=False, indent=2)
        )
        print(f"  built [{lang}]: {len(bands)} band(s), /members/ ({len(musicians_view)} musicians), search-index.json ({len(search_index)} items)")

    support_tmpl = env.get_template("support.html")
    for lang in LANGS:
        support_out_dir = (DIST_DIR if lang == DEFAULT_LANG else DIST_DIR / lang) / "support"
        support_out_dir.mkdir(parents=True, exist_ok=True)
        (support_out_dir / "index.html").write_text(
            support_tmpl.render(
                lang=lang,
                t=STRINGS[lang],
                lang_prefix=lang_prefix(lang),
                base_path=BASE_PATH,
                ru_url=f"{BASE_PATH}/{lang_prefix('ru')}support/",
                en_url=f"{BASE_PATH}/{lang_prefix('en')}support/",
                home_url=home_url(lang, BASE_PATH),
                content=render_maintenance_html(lang),
                og_title=f"{STRINGS[lang]['support_link']} — daugavpils.fans",
                og_description=STRINGS[lang]["site_tagline"],
                og_image_url=None,
                og_type="website",
                canonical_url=f"{SITE_URL}{BASE_PATH}/{lang_prefix(lang)}support/",
            )
        )
        print(f"  built [{lang}]: /support/ (from {MAINTENANCE_MD[lang].name})")

    print(f"\nBuilt site into {DIST_DIR}")


if __name__ == "__main__":
    build()
