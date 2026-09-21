import pytest

from plexgraph_bridge.color import parse_color


def test_passes_through_01_range_array():
    assert parse_color([1.0, 0.5, 0.0, 1.0]) == [1.0, 0.5, 0.0, 1.0]


def test_appends_alpha_for_3_component_array():
    assert parse_color([1.0, 0.5, 0.0]) == [1.0, 0.5, 0.0, 1.0]


def test_scales_255_range_int_array():
    assert parse_color([255, 128, 0]) == pytest.approx([1.0, 128 / 255, 0.0, 1.0])


def test_rejects_wrong_length_array():
    with pytest.raises(ValueError, match="3 or 4 components"):
        parse_color([1.0, 0.5])


def test_hex_6_digit():
    assert parse_color("#ff6347") == pytest.approx([1.0, 99 / 255, 71 / 255, 1.0])


def test_hex_3_digit_shorthand():
    assert parse_color("#f63") == pytest.approx([1.0, 102 / 255, 51 / 255, 1.0])


def test_hex_8_digit_with_alpha():
    assert parse_color("#ff634780") == pytest.approx([1.0, 99 / 255, 71 / 255, 128 / 255])


def test_hex_without_leading_hash_is_rejected():
    # Only "#"-prefixed strings are treated as hex — a bare hex-looking
    # string is ambiguous with a (hypothetical) color name, so require the
    # explicit marker rather than guessing.
    with pytest.raises(ValueError):
        parse_color("ff6347")


def test_invalid_hex_raises():
    with pytest.raises(ValueError, match="invalid hex color"):
        parse_color("#zzzzzz")


def test_named_color_case_insensitive():
    assert parse_color("red") == [1.0, 0.0, 0.0, 1.0]
    assert parse_color("RED") == [1.0, 0.0, 0.0, 1.0]
    assert parse_color("Tomato") == pytest.approx([1.0, 99 / 255, 71 / 255, 1.0])


def test_rebeccapurple_is_supported():
    assert parse_color("rebeccapurple") == pytest.approx([102 / 255, 51 / 255, 153 / 255, 1.0])


def test_unknown_name_raises():
    with pytest.raises(ValueError, match="unrecognized color"):
        parse_color("not-a-real-color")


def test_wrong_type_raises():
    with pytest.raises(TypeError):
        parse_color(42)
