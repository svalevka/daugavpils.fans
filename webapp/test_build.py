from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "webapp"))

import re
from pydantic import ValidationError

from models import GroupMember, ImageObject, MusicAlbum, MusicGroup, MusicRecording
from build import (
    localize,
    localize_list,
    localize_genres,
    to_latin,
    translate_role_en,
    translate_role_lv,
    index_musicians,
    prepare_musicians_view,
    generate_search_index,
)


class LocalizeLatvianTest(unittest.TestCase):
    def test_member_name_uses_name_lv_when_present(self) -> None:
        m = GroupMember(name="Вантуз", name_en="Vantuz", name_lv="Vantuzs")
        self.assertEqual(localize(m, "name", "lv"), "Vantuzs")
        self.assertEqual(localize(m, "name", "en"), "Vantuz")
        self.assertEqual(localize(m, "name", "ru"), "Вантуз")

    def test_member_name_falls_back_to_name_en_in_latvian(self) -> None:
        m = GroupMember(name="Иван Иванов", name_en="Ivan Ivanov")
        self.assertEqual(localize(m, "name", "lv"), "Ivan Ivanov")

    def test_member_role_uses_role_lv_when_present(self) -> None:
        m = GroupMember(name="Гоблин", role="гитара", role_en="guitar", role_lv="ģitāra")
        self.assertEqual(localize(m, "role", "lv"), "ģitāra")
        self.assertEqual(localize(m, "role", "en"), "guitar")
        self.assertEqual(localize(m, "role", "ru"), "гитара")

    def test_member_role_translates_standard_roles_to_latvian(self) -> None:
        m = GroupMember(name="Музыкант", role="аккордеон, труба, скретч", role_en="accordion, trumpet, scratches")
        self.assertEqual(localize(m, "role", "lv"), "akordeons, trompete, skretčs")

    def test_translate_role_lv_various(self) -> None:
        self.assertEqual(translate_role_lv("вокал"), "vokāls")
        self.assertEqual(translate_role_lv("гитара"), "ģitāra")
        self.assertEqual(translate_role_lv("бас"), "bass")
        self.assertEqual(translate_role_lv("ударные"), "bungas")
        self.assertEqual(translate_role_lv(None, "drums"), "bungas")
        self.assertEqual(translate_role_lv("вокал, гитара"), "vokāls, ģitāra")
        self.assertIsNone(translate_role_lv(None, None))

    def test_band_description_uses_description_lv(self) -> None:
        band = MusicGroup(
            name="Дети Гранта",
            slug="deti-granta",
            description="Описание на русском",
            description_en="Description in English",
            description_lv="Apraksts latviešu valodā",
        )
        self.assertEqual(localize(band, "description", "lv"), "Apraksts latviešu valodā")
        self.assertEqual(localize(band, "description", "en"), "Description in English")
        self.assertEqual(localize(band, "description", "ru"), "Описание на русском")

    def test_image_caption_uses_caption_lv(self) -> None:
        img = ImageObject(
            contentUrl="media/photo.jpg",
            encodingFormat="image/jpeg",
            caption="Фото",
            caption_en="Photo",
            caption_lv="Foto",
        )
        self.assertEqual(localize(img, "caption", "lv"), "Foto")
        self.assertEqual(localize(img, "caption", "en"), "Photo")
        self.assertEqual(localize(img, "caption", "ru"), "Фото")


class CreditTextLvAlignmentTest(unittest.TestCase):
    def test_credit_text_lv_valid_when_shorter_or_equal(self) -> None:
        album = MusicAlbum(
            name="Album",
            slug="album",
            datePublished="2001",
            byArtist="test-band",
            track=[MusicRecording(position=1, name="Track 1")],
            creditText=["Иван - вокал", "Пётр - гитара"],
            creditText_lv=["Ivāns - vokāls"],
        )
        self.assertEqual(len(album.creditText_lv), 1)

    def test_credit_text_lv_fails_when_longer_than_credit_text(self) -> None:
        with self.assertRaises(ValidationError):
            MusicAlbum(
                name="Album",
                slug="album",
                datePublished="2001",
                byArtist="test-band",
                track=[MusicRecording(position=1, name="Track 1")],
                creditText=["Иван - вокал"],
                creditText_lv=["Ivāns - vokāls", "Pēteris - ģitāra"],
            )


class MusiciansViewLatvianTest(unittest.TestCase):
    def test_prepare_musicians_view_latvian(self) -> None:
        bands = [
            MusicGroup(
                name="Дети Гранта",
                slug="deti-granta",
                member=[
                    GroupMember(name="Вантуз", name_en="Vantuz", name_lv="Vantuzs", role="вокал", role_en="vocals"),
                    GroupMember(name="Константин Дзедзел", name_en="Konstantīns Dzedzelis", role="бас", role_en="bass"),
                ],
            )
        ]
        indexed = index_musicians(bands)
        view_lv = prepare_musicians_view(indexed, "lv")
        names_lv = {m["display_name"] for m in view_lv}
        self.assertIn("Vantuzs", names_lv)
        self.assertIn("Konstantīns Dzedzelis", names_lv)
        for m in view_lv:
            for b in m["bands"]:
                self.assertIn(b["display_role"], ("vokāls", "bass"))


class LatinZeroCyrillicTest(unittest.TestCase):
    CYR = re.compile(r"[\u0400-\u04ff]")

    def assertZeroCyrillic(self, text: str | None) -> None:
        if text:
            self.assertFalse(
                self.CYR.search(text),
                f"Found Cyrillic in text: {text!r}",
            )

    def test_to_latin(self) -> None:
        self.assertEqual(to_latin("Вантуз"), "Vantuz")
        self.assertEqual(to_latin("Гуна"), "Guna")
        self.assertEqual(to_latin("Дети Гранта"), "Deti Granta")
        self.assertEqual(to_latin('Александр «Рыб»'), 'Aleksandr "Ryb"')
        self.assertEqual(to_latin("Viselica"), "Viselica")
        self.assertEqual(to_latin("Vokāls"), "Vokāls")
        self.assertIsNone(to_latin(None))

    def test_translate_role_en(self) -> None:
        self.assertEqual(translate_role_en("вокал"), "vocals")
        self.assertEqual(translate_role_en("гитара"), "guitar")
        self.assertEqual(translate_role_en("бас"), "bass")
        self.assertEqual(translate_role_en("ударные"), "drums")
        self.assertEqual(translate_role_en("барабаны, запись"), "drums, sound recording")
        self.assertEqual(translate_role_en("гитара, тексты"), "guitar, lyrics")

    def test_localize_band_and_release_zero_cyrillic(self) -> None:
        band = MusicGroup(
            name="Виселица",
            slug="viselica",
            alternateName=["Петля"],
            description="Группа основана в Даугавпилсе",
            member=[
                GroupMember(name="Артур Петров (Будулай)", role="вокал, гитара"),
            ],
        )
        for lang in ("en", "lv"):
            name = localize(band, "name", lang)
            self.assertEqual(name, "Viselica")
            self.assertZeroCyrillic(name)
            self.assertZeroCyrillic(localize(band, "description", lang))
            for m in band.member:
                self.assertZeroCyrillic(localize(m, "name", lang))
                self.assertZeroCyrillic(localize(m, "role", lang))

        release = MusicAlbum(
            name="Мертвый альбом",
            slug="2007-mertvyi-al-bom",
            datePublished="2007",
            byArtist="viselica",
            track=[
                MusicRecording(position=1, name="Возмездие", alternateName="Трек 1"),
            ],
            creditText=["Кузя — бас, соло", "Емеля — флейта"],
        )
        for lang in ("en", "lv"):
            rel_name = localize(release, "name", lang)
            self.assertEqual(rel_name, "Mertvyi albom")
            self.assertZeroCyrillic(rel_name)
            trk_name = localize(release.track[0], "name", lang)
            self.assertZeroCyrillic(trk_name)
            for c in localize_list(release, "creditText", lang):
                self.assertZeroCyrillic(c)

    def test_localize_genres_zero_cyrillic(self) -> None:
        cyr_genres = ["панк-рок", "экспериментальная музыка", "лёёёёгкий роцк"]
        en_genres = localize_genres(cyr_genres, "en")
        lv_genres = localize_genres(cyr_genres, "lv")
        self.assertEqual(en_genres, ["punk rock", "experimental music", "light rock"])
        self.assertEqual(lv_genres, ["pankroks", "eksperimentālā mūzika", "viegls roks"])
        for g in en_genres + lv_genres:
            self.assertZeroCyrillic(g)

    def test_generate_search_index_zero_cyrillic(self) -> None:
        band = MusicGroup(
            name="Виселица",
            slug="viselica",
            alternateName=["Петля"],
            genre=["панк-рок"],
            member=[
                GroupMember(name="Артур Петров", role="вокал, гитара"),
            ],
        )
        release = MusicAlbum(
            name="Резня",
            slug="2002-reznia",
            datePublished="2002",
            byArtist="viselica",
            genre=["панк-рок"],
            track=[
                MusicRecording(position=1, name="Убивать", alternateName="Песня"),
            ],
            creditText=["Артур — гитара"],
        )
        for lang in ("en", "lv"):
            idx = generate_search_index(lang, [band], {"viselica": [release]})
            self.assertGreater(len(idx), 0)
            for item in idx:
                self.assertZeroCyrillic(item.get("name"))
                self.assertZeroCyrillic(item.get("band"))
                self.assertZeroCyrillic(item.get("release"))
                self.assertZeroCyrillic(item.get("alternate_name"))
                for g in item.get("genres", []):
                    self.assertZeroCyrillic(g)
                for m in item.get("members", []):
                    self.assertZeroCyrillic(m)
                for r in item.get("roles", []):
                    self.assertZeroCyrillic(r)
                for a in item.get("alternate_names", []):
                    self.assertZeroCyrillic(a)
                for c in item.get("credits", []):
                    self.assertZeroCyrillic(c)

    def test_prepare_musicians_view_zero_cyrillic(self) -> None:
        bands = [
            MusicGroup(
                name="Виселица",
                slug="viselica",
                member=[
                    GroupMember(name="Артур Петров (Будулай)", role="вокал, гитара"),
                ],
            )
        ]
        indexed = index_musicians(bands)
        for lang in ("en", "lv"):
            view = prepare_musicians_view(indexed, lang)
            self.assertGreater(len(view), 0)
            for m in view:
                self.assertZeroCyrillic(m["display_name"])
                for a in m["alternate_names"]:
                    self.assertZeroCyrillic(a)
                for b in m["bands"]:
                    self.assertZeroCyrillic(b["band_name"])
                    self.assertZeroCyrillic(b["display_role"])
                for c in m["collaborators"]:
                    self.assertZeroCyrillic(c["display_name"])
                    for sb in c["shared_bands"]:
                        self.assertZeroCyrillic(sb)


if __name__ == "__main__":
    unittest.main()
