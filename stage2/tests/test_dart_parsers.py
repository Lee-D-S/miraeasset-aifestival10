from __future__ import annotations

import unittest

from stage2.ingestion.dart.parsers import XmlParser, clean_unescaped_angle_brackets


class CleanUnescapedAngleBracketsTests(unittest.TestCase):
    def test_leaves_real_tags_untouched(self):
        xml = "<P><SPAN>text</SPAN></P><!--comment--><?pi?></P>"
        self.assertEqual(clean_unescaped_angle_brackets(xml), xml)

    def test_escapes_a_bare_caption_marker(self):
        xml = "<P>< TV 시장점유율 추이 ></P>"
        cleaned = clean_unescaped_angle_brackets(xml)
        self.assertEqual(cleaned, "<P>&lt; TV 시장점유율 추이 ></P>")

    def test_escapes_a_caption_embedded_in_a_table_cell(self):
        xml = "<TD><이사ㆍ감사 전체의 보수현황></TD>"
        cleaned = clean_unescaped_angle_brackets(xml)
        self.assertEqual(cleaned, "<TD>&lt;이사ㆍ감사 전체의 보수현황></TD>")


class XmlParserRecoversFromCaptionMarkersTests(unittest.TestCase):
    def test_content_after_a_stray_caption_marker_is_not_dropped(self):
        # Real-world regression: a DART filing used "< 제목 >" as a bare,
        # unescaped caption inside a <P> (e.g. "<P>< TV 시장점유율 추이 ></P>").
        # A literal "<" not starting a real tag/comment/PI is invalid XML;
        # lxml's XML parser recovers from it by silently dropping large
        # stretches of the surrounding tree instead of erroring loudly — one
        # real filing lost 96% of its elements (3,960 of ~89,000), including
        # every financial-statement chapter, because of a handful of these.
        xml = (
            "<DOCUMENT>"
            "<P><SPAN>앞 문단</SPAN></P>"
            "<P>< TV 시장점유율 추이 ></P>"
            "<P><SPAN>영업이익은 1조원입니다 뒷 문단 내용입니다</SPAN></P>"
            "</DOCUMENT>"
        )
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.xml"
            path.write_text(xml, encoding="utf-8")
            elements = XmlParser().parse(str(path))

        self.assertTrue(any("영업이익" in item["text_content"] for item in elements))


if __name__ == "__main__":
    unittest.main()
