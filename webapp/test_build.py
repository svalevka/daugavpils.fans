from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "webapp"))

from pydantic import ValidationError

from models import GroupMember, ImageObject, MusicAlbum, MusicGroup, MusicRecording
from build import (
    localize,
    localize_list,
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


if __name__ == "__main__":
    unittest.main()
