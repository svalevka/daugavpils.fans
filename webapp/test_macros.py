from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


class LightboxCaptionTest(unittest.TestCase):
    def setUp(self) -> None:
        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.globals["localize"] = lambda obj, field, lang: (
            obj.get(f"{field}_en") if lang == "en" and obj.get(f"{field}_en") else obj.get(field)
        )

    def test_gallery_lightbox_includes_caption_when_present(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.gallery(images, "band-photo", "fallback", url_fn) }}
""")
        images = [
            {"contentUrl": "media/photo1.webp", "caption": "Описание на русском", "caption_en": "English description"},
        ]
        # Test Russian rendering
        html_ru = tmpl.render(lang="ru", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', html_ru)
        self.assertIn('<figcaption class="lightbox-caption">Описание на русском</figcaption>', html_ru)

        # Test English rendering
        html_en = tmpl.render(lang="en", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figcaption class="lightbox-caption">English description</figcaption>', html_en)

    def test_gallery_lightbox_omits_caption_when_none(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.gallery(images, "band-photo", "fallback", url_fn) }}
""")
        images = [
            {"contentUrl": "media/photo-no-caption.webp"},
        ]
        rendered = tmpl.render(lang="ru", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', rendered)
        self.assertNotIn("lightbox-caption", rendered)

    def test_video_gallery_lightbox_includes_caption_when_present(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Концерт 1995"},
            {"contentUrl": "media/vid2.mp4", "encodingFormat": "video/mp4", "name": "Репетиция 1996"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', rendered)
        self.assertIn('<figcaption class="lightbox-caption">Концерт 1995</figcaption>', rendered)
        self.assertIn('<figcaption class="lightbox-caption">Репетиция 1996</figcaption>', rendered)

    def test_video_gallery_renders_clickable_thumbnails_with_play_badge(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Концерт 1995"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        # Thumbnail is an <a> link to the lightbox target
        self.assertIn('<a href="#band-video-1" class="gallery-item video-item"', rendered)
        # First frame preview via preload="metadata" and #t=0.001
        self.assertIn('<div class="video-thumb-wrap">', rendered)
        self.assertIn('<video preload="metadata" muted playsinline tabindex="-1" aria-hidden="true">', rendered)
        self.assertIn('src="https://archive.org/media/vid1.mp4#t=0.001"', rendered)
        # Play badge overlay is present
        self.assertIn('<span class="video-play-badge" aria-hidden="true"></span>', rendered)
        # Thumbnail caption
        self.assertIn('<figcaption>Концерт 1995</figcaption>', rendered)
        # No yellow box expand-link
        self.assertNotIn("expand-link", rendered)
        # Lightbox is generated even for a single video
        self.assertIn('<div class="lightbox video-lightbox" id="band-video-1">', rendered)
        self.assertIn('data-video="Концерт 1995"', rendered)
        self.assertNotIn("lightbox-prev", rendered)
        self.assertNotIn("lightbox-next", rendered)

    def test_video_gallery_multi_video_includes_navigation(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Video 1"},
            {"contentUrl": "media/vid2.mp4", "encodingFormat": "video/mp4", "name": "Video 2"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<a href="#band-video-2" class="lightbox-prev" aria-label="Previous video">&#10094;</a>', rendered)
        self.assertIn('<a href="#band-video-2" class="lightbox-next" aria-label="Next video">&#10095;</a>', rendered)


class StaticFilesTest(unittest.TestCase):
    def test_javascript_files_syntax(self) -> None:
        import subprocess

        static_dir = Path(__file__).resolve().parent / "static"
        js_files = list(static_dir.glob("*.js"))
        self.assertGreater(len(js_files), 0)
        for js_file in js_files:
            with self.subTest(file=js_file.name):
                res = subprocess.run(["node", "-c", str(js_file)], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"{js_file.name} syntax error: {res.stderr}")

    def test_favicon_files_exist(self) -> None:
        static_dir = Path(__file__).resolve().parent / "static"
        self.assertTrue((static_dir / "favicon.svg").is_file())
        self.assertTrue((static_dir / "favicon.ico").is_file())
        self.assertGreater((static_dir / "favicon.svg").stat().st_size, 0)
        self.assertGreater((static_dir / "favicon.ico").stat().st_size, 0)

    def test_tracklist_audio_css_scrubber_rules(self) -> None:
        static_dir = Path(__file__).resolve().parent / "static"
        css = (static_dir / "style.css").read_text(encoding="utf-8")
        self.assertIn("table.tracklist td.track-audio", css)
        self.assertIn("table.tracklist td.track-title", css)
        self.assertIn("max-width: 22rem", css)
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("table.tracklist tr.track-row", css)


class SearchIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

    def test_generate_search_index_ru(self) -> None:
        from build import generate_search_index
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({
            "name": "M. Spirit",
            "slug": "m-spirit",
            "alternateName": ["Metal Spirit"],
            "foundingDate": "1994",
            "dissolutionDate": "1996",
            "genre": ["Punk Rock"],
            "member": [
                {"name": "Владислав Петкун", "name_en": "Vladislav Petkun", "role": "гитара, вокал", "role_en": "guitar, vocals"},
                {"name": "Сергей Валевко", "role": "вокал"},
            ],
        })
        release = MusicAlbum.model_validate({
            "name": "Задушевные песенки",
            "slug": "1995-zadushevnie-pesenki",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "genre": ["Punk Rock"],
            "creditText": ["Кузя - запись"],
            "creditText_en": ["Kuzya - recording"],
            "track": [
                {
                    "position": 1,
                    "name": "Ящер",
                    "alternateName": "Ящерка",
                    "audio": {
                        "contentUrl": "01.mp3",
                        "encodingFormat": "audio/mpeg",
                        "identifier": [{"propertyID": "sha256", "value": "abc"}],
                    },
                }
            ],
        })

        index = generate_search_index("ru", [band], {"m-spirit": [release]}, base_path="")
        self.assertEqual(len(index), 3)

        band_item = index[0]
        self.assertEqual(band_item["type"], "band")
        self.assertEqual(band_item["name"], "M. Spirit")
        self.assertEqual(band_item["url"], "/bands/m-spirit/")
        self.assertEqual(band_item["years"], "1994-1996")
        self.assertEqual(band_item["genres"], ["Punk Rock"])
        self.assertEqual(band_item["members"], ["Владислав Петкун", "Сергей Валевко"])
        self.assertEqual(band_item["roles"], ["гитара, вокал", "вокал"])
        self.assertEqual(band_item["alternate_names"], ["Metal Spirit"])

        release_item = index[1]
        self.assertEqual(release_item["type"], "release")
        self.assertEqual(release_item["name"], "Задушевные песенки")
        self.assertEqual(release_item["band"], "M. Spirit")
        self.assertEqual(release_item["year"], "1995")
        self.assertEqual(release_item["url"], "/bands/m-spirit/1995-zadushevnie-pesenki/")
        self.assertEqual(release_item["genres"], ["Punk Rock"])
        self.assertEqual(release_item["credits"], ["Кузя - запись"])

        track_item = index[2]
        self.assertEqual(track_item["type"], "track")
        self.assertEqual(track_item["name"], "Ящер")
        self.assertEqual(track_item["band"], "M. Spirit")
        self.assertEqual(track_item["release"], "Задушевные песенки")
        self.assertEqual(track_item["url"], "/bands/m-spirit/1995-zadushevnie-pesenki/#track-1")
        self.assertEqual(track_item["alternate_name"], "Ящерка")

    def test_generate_search_index_en(self) -> None:
        from build import generate_search_index
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({
            "name": "M. Spirit",
            "slug": "m-spirit",
            "foundingDate": "1994",
            "member": [
                {"name": "Владислав Петкун", "name_en": "Vladislav Petkun", "role": "гитара, вокал", "role_en": "guitar, vocals"},
                {"name": "Сергей Валевко", "role": "вокал"},
            ],
        })
        release = MusicAlbum.model_validate({
            "name": "Задушевные песенки",
            "slug": "1995-zadushevnie-pesenki",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "creditText": ["Кузя - запись"],
            "creditText_en": ["Kuzya - recording"],
            "track": [
                {
                    "position": 2,
                    "name": "Track Two",
                    "audio": {
                        "contentUrl": "02.mp3",
                        "encodingFormat": "audio/mpeg",
                        "identifier": [{"propertyID": "sha256", "value": "abc"}],
                    },
                }
            ],
        })

        index = generate_search_index("en", [band], {"m-spirit": [release]}, base_path="/site")
        self.assertEqual(len(index), 3)

        band_item = index[0]
        self.assertEqual(band_item["url"], "/site/en/bands/m-spirit/")
        self.assertEqual(band_item["years"], "1994")
        self.assertEqual(band_item["members"], ["Vladislav Petkun", "Сергей Валевко"])
        self.assertEqual(band_item["roles"], ["guitar, vocals", "вокал"])

        release_item = index[1]
        self.assertEqual(release_item["url"], "/site/en/bands/m-spirit/1995-zadushevnie-pesenki/")
        self.assertEqual(release_item["credits"], ["Kuzya - recording"])

        track_item = index[2]
        self.assertEqual(track_item["url"], "/site/en/bands/m-spirit/1995-zadushevnie-pesenki/#track-2")


class DeepLinkAndOpenGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.filters["duration"] = lambda d: d or ""
        self.env.globals["localize"] = lambda obj, field, lang: getattr(obj, field, None)
        self.env.globals["localize_list"] = lambda obj, field, lang: getattr(obj, field, []) or []
        self.env.globals["partial"] = lambda fn, *args: (lambda *a, **k: fn(*args, *a, **k))

    def test_og_snippet_truncation_and_fallback(self) -> None:
        from build import og_snippet

        self.assertEqual(og_snippet(None, "Fallback text"), "Fallback text")
        self.assertEqual(og_snippet("", "Fallback text"), "Fallback text")

        text = "First paragraph with words.\n\nSecond paragraph has more text."
        self.assertEqual(
            og_snippet(text, "Fallback"),
            "First paragraph with words. Second paragraph has more text.",
        )

        long_text = "Word " * 60
        snippet = og_snippet(long_text, "Fallback", max_length=50)
        self.assertLessEqual(len(snippet), 50)
        self.assertTrue(snippet.endswith("..."))

    def test_release_template_renders_track_anchors(self) -> None:
        from i18n import STRINGS
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({
            "name": "M. Spirit",
            "slug": "m-spirit",
        })
        release = MusicAlbum.model_validate({
            "name": "Задушевные песенки",
            "slug": "1995-zadushevnie-pesenki",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "track": [
                {
                    "position": 1,
                    "name": "Track 1",
                    "audio": {
                        "contentUrl": "01.mp3",
                        "encodingFormat": "audio/mpeg",
                        "identifier": [{"propertyID": "sha256", "value": "abc"}],
                    },
                },
                {
                    "position": 2,
                    "name": "Track 2",
                    "audio": None,
                },
            ],
        })

        tmpl = self.env.get_template("release.html")
        rendered = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            band=band,
            release=release,
            home_url="/",
            ru_url="/",
            en_url="/en/",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            release_media_url=lambda b, r, u: f"https://archive.org/{u}",
            canonical_url="https://daugavpils.fans/bands/m-spirit/1995-zadushevnie-pesenki/",
            jsonld="{}",
            og_title="Задушевные песенки — M. Spirit — daugavpils.fans",
            og_description="Test description",
            og_image_url="https://archive.org/cover.jpg",
            og_type="music.album",
        )

        # Track 1 row and anchor
        self.assertIn('<tr id="track-1" class="track-row">', rendered)
        self.assertIn('<a href="#track-1" class="track-anchor"', rendered)
        self.assertIn('>1</a>', rendered)
        self.assertIn('<td class="track-title">', rendered)
        self.assertIn('<td class="track-audio">', rendered)
        self.assertIn('<audio controls controlsList="nodownload noplaybackrate"', rendered)

        # Track 2 unpreserved row and anchor
        self.assertIn('<tr id="track-2" class="track-row track-unpreserved">', rendered)
        self.assertIn('<a href="#track-2" class="track-anchor"', rendered)
        self.assertIn('>2</a>', rendered)

    def test_base_template_renders_open_graph_and_twitter_tags(self) -> None:
        from i18n import STRINGS

        tmpl = self.env.get_template("base.html")
        rendered = tmpl.render(
            lang="en",
            t=STRINGS["en"],
            lang_prefix="en/",
            base_path="",
            home_url="/en/",
            ru_url="/",
            en_url="/en/",
            og_title="M. Spirit — daugavpils.fans",
            og_description="Biography of M. Spirit band from Daugavpils.",
            og_image_url="https://archive.org/photo.jpg",
            og_type="music.band",
            canonical_url="https://daugavpils.fans/en/bands/m-spirit/",
        )

        self.assertIn('<meta property="og:site_name" content="daugavpils.fans">', rendered)
        self.assertIn('<meta property="og:title" content="M. Spirit — daugavpils.fans">', rendered)
        self.assertIn('<meta property="og:description" content="Biography of M. Spirit band from Daugavpils.">', rendered)
        self.assertIn('<meta property="og:image" content="https://archive.org/photo.jpg">', rendered)
        self.assertIn('<meta property="og:type" content="music.band">', rendered)
        self.assertIn('<meta property="og:url" content="https://daugavpils.fans/en/bands/m-spirit/">', rendered)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', rendered)


class ReleaseDownloadsTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.filters["duration"] = lambda d: d or ""
        self.env.globals["localize"] = lambda obj, field, lang: getattr(obj, field, None)
        self.env.globals["localize_list"] = lambda obj, field, lang: getattr(obj, field, []) or []
        self.env.globals["partial"] = lambda fn, *args: (lambda *a, **k: fn(*args, *a, **k))

    def test_release_template_renders_direct_downloads(self) -> None:
        from i18n import STRINGS
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({"name": "M. Spirit", "slug": "m-spirit"})
        release = MusicAlbum.model_validate({
            "name": "Задушевные песенки",
            "slug": "1995-zadushevnie-pesenki",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "track": [
                {
                    "position": 1,
                    "name": "Ящер",
                    "audio": {
                        "contentUrl": "01-iashcher.mp3",
                        "encodingFormat": "audio/mpeg",
                        "identifier": [{"propertyID": "sha256", "value": "abc"}],
                    },
                }
            ],
        })

        tmpl = self.env.get_template("release.html")
        rendered_ru = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            band=band,
            release=release,
            home_url="/",
            ru_url="/",
            en_url="/en/",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            release_media_url=lambda b, r, u: f"https://archive.org/download/item/{u}",
            canonical_url="https://daugavpils.fans/bands/m-spirit/1995-zadushevnie-pesenki/",
            jsonld="{}",
        )

        # No bulk downloads or individual track download links (archive is for streaming listening)
        self.assertNotIn('<div class="release-downloads">', rendered_ru)
        self.assertNotIn("<h3>Скачать релиз</h3>", rendered_ru)
        self.assertNotIn("Скачать .torrent", rendered_ru)
        self.assertNotIn("Скачать архив (.zip)", rendered_ru)
        self.assertNotIn('class="track-download"', rendered_ru)

        # Audio player is present
        self.assertIn('<audio controls controlsList="nodownload noplaybackrate"', rendered_ru)
        self.assertIn('data-track="Ящер"', rendered_ru)

        # English rendering
        rendered_en = tmpl.render(
            lang="en",
            t=STRINGS["en"],
            lang_prefix="en/",
            base_path="",
            band=band,
            release=release,
            home_url="/en/",
            ru_url="/",
            en_url="/en/",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            release_media_url=lambda b, r, u: f"https://archive.org/download/item/{u}",
            canonical_url="https://daugavpils.fans/en/bands/m-spirit/1995-zadushevnie-pesenki/",
            jsonld="{}",
        )
        self.assertNotIn("<h3>Download release</h3>", rendered_en)
        self.assertNotIn("Download .torrent", rendered_en)
        self.assertNotIn("Download archive (.zip)", rendered_en)
        self.assertNotIn('title="Download track"', rendered_en)
        self.assertNotIn('class="track-download"', rendered_en)

    def test_release_template_omits_downloads_when_none(self) -> None:
        from i18n import STRINGS
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({"name": "M. Spirit", "slug": "m-spirit"})
        release = MusicAlbum.model_validate({
            "name": "Unpreserved",
            "slug": "1995-unpreserved",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "track": [
                {
                    "position": 1,
                    "name": "Lost Track",
                    "audio": None,
                }
            ],
        })

        tmpl = self.env.get_template("release.html")
        rendered = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            band=band,
            release=release,
            home_url="/",
            ru_url="/",
            en_url="/en/",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            release_media_url=lambda b, r, u: f"https://archive.org/download/item/{u}",
            canonical_url="https://daugavpils.fans/bands/m-spirit/1995-unpreserved/",
            jsonld="{}",
        )
        self.assertNotIn("release-downloads", rendered)
        self.assertNotIn("track-download", rendered)


class MusicianDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.filters["duration"] = lambda d: d or ""
        self.env.filters["license_label"] = lambda u: u
        self.env.globals["localize"] = lambda obj, field, lang: (
            getattr(obj, f"{field}_en", None) if lang == "en" and getattr(obj, f"{field}_en", None) else getattr(obj, field, None)
        )
        self.env.globals["localize_list"] = lambda obj, field, lang: getattr(obj, field, []) or []
        self.env.globals["partial"] = lambda fn, *args: (lambda *a, **k: fn(*args, *a, **k))

    def test_canonical_musician_slug(self) -> None:
        from build import canonical_musician_slug

        # Quotes / nicknames in quotes
        self.assertEqual(canonical_musician_slug("Руслан «Гоблин» Кондрусь"), "ruslan-kondrus")
        self.assertEqual(canonical_musician_slug("Алексей «Вантуз» Красько"), "aleksei-kras-ko")

        # Parenthesized nicknames
        self.assertEqual(canonical_musician_slug("Алексей Красько (Вантуз)"), "aleksei-kras-ko")
        self.assertEqual(canonical_musician_slug("Руслан Кондрусь (Годблин)"), "ruslan-kondrus")

        # Nickname-only alias map
        self.assertEqual(canonical_musician_slug("Вантуз"), "aleksei-kras-ko")
        self.assertEqual(canonical_musician_slug("Гоблин"), "ruslan-kondrus")
        self.assertEqual(canonical_musician_slug("Александр «Рыб»"), "aleksandr-rybakov")

        # Single word without alias
        self.assertEqual(canonical_musician_slug("Слеер"), "sleer")

    def test_index_musicians_and_collaborators(self) -> None:
        from build import canonical_musician_slug, index_musicians, prepare_musicians_view
        from models import MusicGroup

        band1 = MusicGroup.model_validate({
            "name": "Фобия",
            "slug": "fobiia",
            "member": [
                {"name": "Руслан «Гоблин» Кондрусь", "name_en": "Ruslan Kondrus", "role": "гитара", "role_en": "guitar"},
                {"name": "Геннадий «Кузя» Кузьмин", "name_en": "Gennady Kuzmin", "role": "вокал", "role_en": "vocals"},
            ],
        })
        band2 = MusicGroup.model_validate({
            "name": "Дети Гранта",
            "slug": "deti-granta",
            "member": [
                {"name": "Гоблин", "name_en": "Goblin", "role": "гитара", "role_en": "guitar"},
                {"name": "Вантуз", "name_en": "Vantuz", "role": "вокал", "role_en": "vocals"},
            ],
        })

        raw_index = index_musicians([band1, band2])
        self.assertIn("ruslan-kondrus", raw_index)
        self.assertIn("gennadii-kuz-min", raw_index)
        self.assertIn("aleksei-kras-ko", raw_index)

        # Russian view
        view_ru = prepare_musicians_view(raw_index, "ru")
        kondrus_ru = next(m for m in view_ru if m["slug"] == "ruslan-kondrus")
        self.assertEqual(kondrus_ru["display_name"], "Руслан Кондрусь")
        self.assertIn("Гоблин", kondrus_ru["alternate_names"])
        self.assertEqual(len(kondrus_ru["bands"]), 2)
        # Collaborators of Ruslan Kondrus: Gennady Kuzmin and Aleksei Kras'ko
        collab_slugs = [c["slug"] for c in kondrus_ru["collaborators"]]
        self.assertIn("gennadii-kuz-min", collab_slugs)
        self.assertIn("aleksei-kras-ko", collab_slugs)

        # English view
        view_en = prepare_musicians_view(raw_index, "en")
        kondrus_en = next(m for m in view_en if m["slug"] == "ruslan-kondrus")
        self.assertEqual(kondrus_en["display_name"], "Ruslan Kondrus")
        self.assertIn("Goblin", kondrus_en["alternate_names"])

    def test_members_directory_template_rendering(self) -> None:
        from i18n import STRINGS

        tmpl = self.env.get_template("members.html")
        musicians = [
            {
                "slug": "ruslan-kondrus",
                "display_name": "Руслан Кондрусь",
                "alternate_names": ["Гоблин"],
                "bands": [
                    {"band_slug": "fobiia", "band_name": "Фобия", "display_role": "гитара", "period": "1994"},
                    {"band_slug": "deti-granta", "band_name": "Дети Гранта", "display_role": "гитара", "period": None},
                ],
            },
            {
                "slug": "aleksei-kras-ko",
                "display_name": "Алексей Красько",
                "alternate_names": ["Вантуз"],
                "bands": [
                    {"band_slug": "deti-granta", "band_name": "Дети Гранта", "display_role": "вокал", "period": None},
                ],
            },
        ]

        rendered = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/members/",
            en_url="/en/members/",
            musicians=musicians,
        )

        self.assertIn("Руслан Кондрусь", rendered)
        self.assertIn("/members/ruslan-kondrus/", rendered)
        self.assertIn("2 группы", rendered)
        self.assertIn("Алексей Красько", rendered)
        self.assertIn("/members/aleksei-kras-ko/", rendered)

    def test_member_profile_template_rendering(self) -> None:
        from i18n import STRINGS

        tmpl = self.env.get_template("member.html")
        musician = {
            "slug": "ruslan-kondrus",
            "display_name": "Руслан Кондрусь",
            "alternate_names": ["Гоблин", "Годблин"],
            "bands": [
                {"band_slug": "fobiia", "band_name": "Фобия", "display_role": "гитара", "period": "1994-1995"},
                {"band_slug": "deti-granta", "band_name": "Дети Гранта", "display_role": "гитара", "period": None},
            ],
        }
        collaborators = [
            {"slug": "gennadii-kuz-min", "display_name": "Геннадий Кузьмин", "shared_bands": ["Фобия"]},
            {"slug": "aleksei-kras-ko", "display_name": "Алексей Красько", "shared_bands": ["Дети Гранта"]},
        ]

        rendered = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/members/ruslan-kondrus/",
            en_url="/en/members/ruslan-kondrus/",
            members_index_url="/members/",
            musician=musician,
            collaborators=collaborators,
        )

        self.assertIn("<h1>Руслан Кондрусь</h1>", rendered)
        self.assertIn("Гоблин, Годблин", rendered)
        self.assertIn("/bands/fobiia/", rendered)
        self.assertIn("/bands/deti-granta/", rendered)
        self.assertIn("/members/gennadii-kuz-min/", rendered)
        self.assertIn("Геннадий Кузьмин", rendered)
        self.assertIn("(Фобия)", rendered)

    def test_band_template_links_members(self) -> None:
        from build import canonical_musician_slug
        from i18n import STRINGS
        from models import MusicGroup

        self.env.globals["musician_slug"] = canonical_musician_slug

        band = MusicGroup.model_validate({
            "name": "Фобия",
            "slug": "fobiia",
            "member": [
                {"name": "Руслан «Гоблин» Кондрусь", "role": "гитара"},
            ],
        })

        tmpl = self.env.get_template("band.html")
        rendered = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            band=band,
            releases=[],
            home_url="/",
            ru_url="/bands/fobiia/",
            en_url="/en/bands/fobiia/",
            jsonld="{}",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            media_page_url=None,
        )

        self.assertIn('<a href="/members/ruslan-kondrus/"><strong>Руслан «Гоблин» Кондрусь</strong></a>', rendered)


class RSSFeedGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.filters["duration"] = lambda d: d or ""
        self.env.filters["license_label"] = lambda u: u
        self.env.globals["localize"] = lambda obj, field, lang: (
            getattr(obj, f"{field}_en", None) if lang == "en" and getattr(obj, f"{field}_en", None) else getattr(obj, field, None)
        )
        self.env.globals["localize_list"] = lambda obj, field, lang: getattr(obj, field, []) or []
        self.env.globals["partial"] = lambda fn, *args: (lambda *a, **k: fn(*args, *a, **k))

    def test_generate_feed_items_and_xml_validation(self) -> None:
        import datetime
        import email.utils
        import xml.etree.ElementTree as ET
        from build import generate_feed_items
        from i18n import STRINGS
        from models import MusicAlbum, MusicGroup

        band = MusicGroup.model_validate({
            "name": "M. Spirit",
            "slug": "m-spirit",
            "image": [{"contentUrl": "media/m-spirit.jpg", "encodingFormat": "image/jpeg"}],
        })
        release = MusicAlbum.model_validate({
            "name": "Задушевные песенки",
            "slug": "1995-zadushevnie-pesenki",
            "datePublished": "1995",
            "byArtist": "m-spirit",
            "genre": ["Punk Rock"],
            "description": "Первый студийный альбом группы.",
            "image": [{"contentUrl": "media/cover.jpg", "encodingFormat": "image/jpeg"}],
            "track": [
                {
                    "position": 1,
                    "name": "Track 1",
                    "audio": {
                        "contentUrl": "01.mp3",
                        "encodingFormat": "audio/mpeg",
                        "duration": "PT1M30S",
                        "identifier": [{"propertyID": "sha256", "value": "abc"}],
                    },
                }
            ],
        })

        items = generate_feed_items("ru", [band], {"m-spirit": [release]}, base_path="")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "M. Spirit — Задушевные песенки (1995)")
        self.assertIn("/bands/m-spirit/1995-zadushevnie-pesenki/", item["link"])
        self.assertIsNotNone(item["enclosure"])
        self.assertEqual(item["enclosure"]["type"], "image/jpeg")
        self.assertIn("Первый студийный альбом группы.", item["description"])
        self.assertIn("Track 1", item["description"])
        self.assertIn("1:30", item["description"])

        feed_tmpl = self.env.get_template("feed.xml")
        xml_content = feed_tmpl.render(
            channel_title="Архив музыки Даугавпилса — daugavpils.fans",
            channel_link="https://daugavpils.fans/",
            channel_description=STRINGS["ru"]["site_tagline"],
            lang="ru",
            feed_url="https://daugavpils.fans/feed.xml",
            last_build_date=email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc)),
            items=items,
        )

        root = ET.fromstring(xml_content)
        self.assertEqual(root.tag, "rss")
        channel = root.find("channel")
        self.assertIsNotNone(channel)
        self.assertEqual(channel.find("title").text, "Архив музыки Даугавпилса — daugavpils.fans")
        self.assertEqual(channel.find("language").text, "ru")
        rss_items = channel.findall("item")
        self.assertEqual(len(rss_items), 1)
        self.assertEqual(rss_items[0].find("title").text, "M. Spirit — Задушевные песенки (1995)")
        self.assertEqual(rss_items[0].find("enclosure").attrib["type"], "image/jpeg")

    def test_base_template_includes_rss_feed_link(self) -> None:
        from i18n import STRINGS

        tmpl = self.env.get_template("base.html")
        # Russian
        html_ru = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/",
            en_url="/en/",
        )
        self.assertIn('<link rel="alternate" type="application/rss+xml" title="Архив музыки Даугавпилса" href="/feed.xml">', html_ru)

        # English
        html_en = tmpl.render(
            lang="en",
            t=STRINGS["en"],
            lang_prefix="en/",
            base_path="/site",
            home_url="/site/en/",
            ru_url="/site/",
            en_url="/site/en/",
        )
        self.assertIn('<link rel="alternate" type="application/rss+xml" title="Daugavpils music archive" href="/site/en/feed.xml">', html_en)


class SiteDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "tools"))
        sys.path.insert(0, str(repo_root / "webapp"))

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.env.filters["duration"] = lambda d: d or ""
        self.env.filters["license_label"] = lambda l: l or ""
        self.env.globals["localize"] = lambda obj, field, lang: getattr(obj, field, None)
        self.env.globals["localize_list"] = lambda obj, field, lang: getattr(obj, field, []) or []
        self.env.globals["partial"] = lambda fn, *args: (lambda *a, **k: fn(*args, *a, **k))
        self.env.globals["musician_slug"] = lambda name: "some-slug"
        self.env.globals["band_media_url"] = lambda band, url: url
        self.env.globals["release_media_url"] = lambda band, release, url: url

    def test_base_template_favicon_and_meta_description(self) -> None:
        from i18n import STRINGS

        tmpl = self.env.get_template("base.html")
        # Root-relative
        html_default = tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/",
            en_url="/en/",
            og_description="Custom description test",
        )
        self.assertIn('<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">', html_default)
        self.assertIn('<link rel="alternate icon" href="/static/favicon.ico">', html_default)
        self.assertIn('<meta name="description" content="Custom description test">', html_default)

        # With base_path
        html_prefixed = tmpl.render(
            lang="en",
            t=STRINGS["en"],
            lang_prefix="en/",
            base_path="/subpath",
            home_url="/subpath/en/",
            ru_url="/subpath/",
            en_url="/subpath/en/",
            og_description="Custom description test",
        )
        self.assertIn('<link rel="icon" href="/subpath/static/favicon.svg" type="image/svg+xml">', html_prefixed)
        self.assertIn('<link rel="alternate icon" href="/subpath/static/favicon.ico">', html_prefixed)

    def test_page_meta_descriptions(self) -> None:
        from i18n import STRINGS
        from models import MusicGroup, MusicAlbum

        # index.html
        index_tmpl = self.env.get_template("index.html")
        index_html = index_tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/",
            en_url="/en/",
            bands=[],
            og_description=STRINGS["ru"]["site_tagline"],
        )
        self.assertIn(f'<meta name="description" content="{STRINGS["ru"]["site_tagline"]}">', index_html)

        # band.html
        band_tmpl = self.env.get_template("band.html")
        band = MusicGroup(name="Test Band", slug="test-band", description="Test band bio.")
        band_html = band_tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/bands/test-band/",
            en_url="/en/bands/test-band/",
            band=band,
            releases=[],
            jsonld="{}",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            media_page_url=None,
            og_description="Test band bio.",
        )
        self.assertIn('<meta name="description" content="Test band bio.">', band_html)

        # release.html
        release_tmpl = self.env.get_template("release.html")
        release = MusicAlbum(
            name="Test Album",
            slug="test-album",
            datePublished="2000",
            byArtist="test-band",
            description="Test release bio.",
            track=[],
        )
        release_html = release_tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/bands/test-band/test-album/",
            en_url="/en/bands/test-band/test-album/",
            band=band,
            release=release,
            jsonld="{}",
            visible_same_as=[],
            photo_teaser_limit=6,
            video_teaser_limit=2,
            media_page_url=None,
            og_description="Test release bio.",
        )
        self.assertIn('<meta name="description" content="Test release bio.">', release_html)

        # support.html
        support_tmpl = self.env.get_template("support.html")
        support_html = support_tmpl.render(
            lang="ru",
            t=STRINGS["ru"],
            lang_prefix="",
            base_path="",
            home_url="/",
            ru_url="/support/",
            en_url="/en/support/",
            content="<p>Maintenance</p>",
            og_description=STRINGS["ru"]["support_desc"],
        )
        self.assertIn(f'<meta name="description" content="{STRINGS["ru"]["support_desc"]}">', support_html)

    def test_generate_robots_txt(self) -> None:
        from build import generate_robots_txt

        # Root-relative (default)
        robots_default = generate_robots_txt(base_path="", site_url="https://daugavpils.fans")
        self.assertEqual(
            robots_default,
            "User-agent: *\nAllow: /\n\nSitemap: https://daugavpils.fans/sitemap.xml\n",
        )

        # Path-prefixed
        robots_prefixed = generate_robots_txt(base_path="/daugavpils.fans", site_url="https://svalevka.github.io")
        self.assertEqual(
            robots_prefixed,
            "User-agent: *\nAllow: /\n\nSitemap: https://svalevka.github.io/daugavpils.fans/sitemap.xml\n",
        )

    def test_generate_sitemap(self) -> None:
        import xml.etree.ElementTree as ET
        from build import generate_sitemap
        from models import MusicGroup, MusicAlbum, ImageObject

        band1 = MusicGroup(
            name="Band One",
            slug="band-one",
            image=[ImageObject(contentUrl="photo1.jpg", encodingFormat="image/jpeg")] * 7,  # triggers needs_media_page (>6)
        )
        band2 = MusicGroup(
            name="Band Two",
            slug="band-two",
        )
        rel1 = MusicAlbum(
            name="Album One",
            slug="album-one",
            datePublished="2000",
            byArtist="band-one",
            track=[],
            image=[ImageObject(contentUrl="rel_photo1.jpg", encodingFormat="image/jpeg")] * 7,  # triggers needs_media_page (>6)
        )
        rel2 = MusicAlbum(
            name="Album Two",
            slug="album-two",
            datePublished="2002",
            byArtist="band-two",
            track=[],
        )

        bands = [band1, band2]
        releases_by_band = {
            "band-one": [rel1],
            "band-two": [rel2],
        }
        musicians_by_slug = {
            "john-doe": [(band1, None)],
        }

        # Test default
        sitemap_xml = generate_sitemap(
            bands,
            releases_by_band,
            musicians_by_slug,
            base_path="",
            site_url="https://daugavpils.fans",
            env=self.env,
        )
        root = ET.fromstring(sitemap_xml)
        self.assertEqual(root.tag, "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset")

        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [elem.text for elem in root.findall("ns:url/ns:loc", ns)]

        # Expected URLs (both ru and en)
        self.assertIn("https://daugavpils.fans/", locs)
        self.assertIn("https://daugavpils.fans/en/", locs)
        self.assertIn("https://daugavpils.fans/bands/band-one/", locs)
        self.assertIn("https://daugavpils.fans/en/bands/band-one/", locs)
        self.assertIn("https://daugavpils.fans/bands/band-one/media/", locs)
        self.assertIn("https://daugavpils.fans/en/bands/band-one/media/", locs)
        self.assertIn("https://daugavpils.fans/bands/band-one/album-one/", locs)
        self.assertIn("https://daugavpils.fans/en/bands/band-one/album-one/", locs)
        self.assertIn("https://daugavpils.fans/bands/band-one/album-one/media/", locs)
        self.assertIn("https://daugavpils.fans/en/bands/band-one/album-one/media/", locs)
        self.assertIn("https://daugavpils.fans/bands/band-two/", locs)
        # band-two has no media page
        self.assertNotIn("https://daugavpils.fans/bands/band-two/media/", locs)
        # members
        self.assertIn("https://daugavpils.fans/members/", locs)
        self.assertIn("https://daugavpils.fans/en/members/", locs)
        self.assertIn("https://daugavpils.fans/members/john-doe/", locs)
        self.assertIn("https://daugavpils.fans/en/members/john-doe/", locs)
        # support
        self.assertIn("https://daugavpils.fans/support/", locs)
        self.assertIn("https://daugavpils.fans/en/support/", locs)

        # Test with SITE_BASE_PATH
        sitemap_prefixed = generate_sitemap(
            bands,
            releases_by_band,
            musicians_by_slug,
            base_path="/daugavpils.fans",
            site_url="https://svalevka.github.io",
            env=self.env,
        )
        root_prefixed = ET.fromstring(sitemap_prefixed)
        locs_prefixed = [elem.text for elem in root_prefixed.findall("ns:url/ns:loc", ns)]
        self.assertIn("https://svalevka.github.io/daugavpils.fans/", locs_prefixed)
        self.assertIn("https://svalevka.github.io/daugavpils.fans/en/", locs_prefixed)
        self.assertIn("https://svalevka.github.io/daugavpils.fans/bands/band-one/", locs_prefixed)
        self.assertIn("https://svalevka.github.io/daugavpils.fans/en/bands/band-one/", locs_prefixed)
        self.assertIn("https://svalevka.github.io/daugavpils.fans/support/", locs_prefixed)
        self.assertIn("https://svalevka.github.io/daugavpils.fans/en/support/", locs_prefixed)


if __name__ == "__main__":
    unittest.main()


