"""Unit tests for annotations.py — teaching-annotation tag grammar (hackathon).

The vision model appends [ARROW]/[CIRCLE]/[UNDERLINE]/[LABEL] tags to its
spoken answer; the parser strips them (so TTS never reads coordinates) and
returns shape objects in screenshot-pixel space.
"""
from __future__ import annotations


class TestParseAnnotations:
    def test_parses_arrow(self):
        from annotations import parse_annotations, Arrow
        spoken, anns = parse_annotations("Click here. [ARROW:10,20->30,40]")
        assert spoken == "Click here."
        assert anns == [Arrow(10, 20, 30, 40)]

    def test_parses_circle_with_label(self):
        from annotations import parse_annotations, Circle
        spoken, anns = parse_annotations("Your error. [CIRCLE:100,200,30:chain rule]")
        assert spoken == "Your error."
        assert anns == [Circle(100, 200, 30, "chain rule")]

    def test_parses_circle_without_label(self):
        from annotations import parse_annotations, Circle
        _, anns = parse_annotations("[CIRCLE:5,6,7]")
        assert anns == [Circle(5, 6, 7, "")]

    def test_parses_underline(self):
        from annotations import parse_annotations, Underline
        _, anns = parse_annotations("[UNDERLINE:50,60,120]")
        assert anns == [Underline(50, 60, 120)]

    def test_parses_label(self):
        from annotations import parse_annotations, Label
        _, anns = parse_annotations("[LABEL:200,300:multiply by inner derivative]")
        assert anns == [Label(200, 300, "multiply by inner derivative")]

    def test_parses_multiple_in_document_order(self):
        from annotations import parse_annotations, Arrow, Circle
        spoken, anns = parse_annotations(
            "You missed it. [CIRCLE:100,200,30:here][ARROW:100,200->150,250]"
        )
        assert spoken == "You missed it."
        assert len(anns) == 2
        assert isinstance(anns[0], Circle) and isinstance(anns[1], Arrow)

    def test_no_tags(self):
        from annotations import parse_annotations
        spoken, anns = parse_annotations("Just a plain explanation.")
        assert spoken == "Just a plain explanation."
        assert anns == []

    def test_malformed_tag_ignored_not_crashed(self):
        from annotations import parse_annotations
        _, anns = parse_annotations("text [ARROW:bad] [CIRCLE:1,2,3:ok]")
        assert len(anns) == 1
        assert anns[0].__class__.__name__ == "Circle"

    def test_all_tags_stripped_from_spoken(self):
        from annotations import parse_annotations
        spoken, _ = parse_annotations("A [CIRCLE:1,2,3] B [ARROW:1,2->3,4] C")
        assert "[" not in spoken and "]" not in spoken
        assert "A" in spoken and "B" in spoken and "C" in spoken

    def test_label_with_spaces_and_punctuation(self):
        from annotations import parse_annotations, Label
        _, anns = parse_annotations("[LABEL:10,20:multiply by 2, then add 3]")
        assert anns == [Label(10, 20, "multiply by 2, then add 3")]

    def test_dataclasses_are_frozen_and_comparable(self):
        from annotations import Arrow, Circle
        assert Arrow(1, 2, 3, 4) == Arrow(1, 2, 3, 4)
        assert Circle(1, 2, 3, "a") != Circle(1, 2, 3, "b")

    def test_unterminated_tag_stripped_so_coords_never_spoken(self):
        """Fail-closed: a truncated tag with no closing ] must not survive into
        the spoken text (else TTS reads the coordinates aloud)."""
        from annotations import parse_annotations
        spoken, anns = parse_annotations("look here [CIRCLE:120,40,15")
        assert spoken == "look here"
        assert "120" not in spoken and "[" not in spoken
        assert anns == []

    def test_unterminated_tag_after_complete_tag_both_handled(self):
        from annotations import parse_annotations
        spoken, anns = parse_annotations(
            "ok [CIRCLE:1,2,3:here] then [ARROW:10,20->"
        )
        assert "[" not in spoken and "10" not in spoken
        assert len(anns) == 1  # the complete circle parsed; truncated arrow dropped

    def test_no_coordinate_digits_leak_into_spoken(self):
        from annotations import parse_annotations
        spoken, _ = parse_annotations(
            "you missed the chain rule [CIRCLE:340,210,28:step][ARROW:340,210->410,260]"
        )
        for frag in ("340", "210", "28", "410", "260"):
            assert frag not in spoken

    def test_lowercase_tags_parse_and_strip(self):
        """The prompt asks for all-lowercase prose, so the model may emit
        lowercase tags. They must parse AND be stripped (no coords to TTS)."""
        from annotations import parse_annotations, Circle, Arrow
        spoken, anns = parse_annotations(
            "your error [circle:100,200,30:here][arrow:100,200->150,250]"
        )
        assert spoken == "your error"
        assert anns == [Circle(100, 200, 30, "here"), Arrow(100, 200, 150, 250)]

    def test_lowercase_unterminated_tag_fail_closed(self):
        from annotations import parse_annotations
        spoken, anns = parse_annotations("look here [label:10,20:fix this")
        assert spoken == "look here"
        assert "10" not in spoken and "[" not in spoken
        assert anns == []

    def test_whitespace_before_colon_parses_and_strips(self):
        """`[circle : ...]` (space before colon) must parse AND strip."""
        from annotations import parse_annotations, Circle
        spoken, anns = parse_annotations("your error [circle : 100,200,30:save]")
        assert spoken == "your error"
        assert anns == [Circle(100, 200, 30, "save")]

    def test_whitespace_before_colon_unterminated_fail_closed(self):
        from annotations import parse_annotations
        spoken, anns = parse_annotations("look here [label : 10,20:fix")
        assert spoken == "look here"
        assert "10" not in spoken and "[" not in spoken
        assert anns == []
