"""Tests for functions/pandoc_postprocess.py."""

import pytest

from pandoc_postprocess import (
    _extract_cells,
    _get_column_spans,
    _is_simple_table_separator,
    clean_underline_spans,
    postprocess,
    simple_tables_to_pipe,
    strip_fenced_divs,
)


# ---------------------------------------------------------------------------
# strip_fenced_divs
# ---------------------------------------------------------------------------


class TestStripFencedDivs:
    """Tests for removing pandoc ::: fenced div wrappers."""

    def test_removes_div_with_class(self):
        text = "::: {.some-class}\nContent here\n:::\n"
        result = strip_fenced_divs(text)
        assert ":::" not in result
        assert "Content here" in result

    def test_removes_div_with_multiple_attributes(self):
        text = '::: {.class1 .class2 #id}\nInner text\n:::\n'
        result = strip_fenced_divs(text)
        assert ":::" not in result
        assert "Inner text" in result

    def test_preserves_non_div_content(self):
        text = "Before\n::: {.wrapper}\nMiddle\n:::\nAfter\n"
        result = strip_fenced_divs(text)
        assert "Before" in result
        assert "Middle" in result
        assert "After" in result

    def test_nested_divs_all_removed(self):
        text = "::: {.outer}\n::: {.inner}\nDeep content\n:::\n:::\n"
        result = strip_fenced_divs(text)
        assert ":::" not in result
        assert "Deep content" in result

    def test_no_divs_unchanged(self):
        text = "Just plain text\nWith multiple lines\n"
        assert strip_fenced_divs(text) == text

    def test_empty_input(self):
        assert strip_fenced_divs("") == ""


# ---------------------------------------------------------------------------
# clean_underline_spans
# ---------------------------------------------------------------------------


class TestCleanUnderlineSpans:
    """Tests for converting pandoc underline spans to <u> tags."""

    def test_basic_underline(self):
        text = "This is [underlined text]{.underline} here."
        result = clean_underline_spans(text)
        assert result == "This is <u>underlined text</u> here."

    def test_multiple_underlines(self):
        text = "[first]{.underline} and [second]{.underline}"
        result = clean_underline_spans(text)
        assert "<u>first</u>" in result
        assert "<u>second</u>" in result

    def test_no_underline_unchanged(self):
        text = "Nothing to change here"
        assert clean_underline_spans(text) == text

    def test_other_span_classes_untouched(self):
        text = "[bold]{.bold} and [underlined]{.underline}"
        result = clean_underline_spans(text)
        assert "[bold]{.bold}" in result
        assert "<u>underlined</u>" in result

    def test_empty_input(self):
        assert clean_underline_spans("") == ""


# ---------------------------------------------------------------------------
# simple_tables_to_pipe
# ---------------------------------------------------------------------------


class TestSimpleTablesToPipe:
    """Tests for converting pandoc simple tables to pipe tables."""

    def test_basic_simple_table(self):
        text = "Col A   Col B\n-----   -----\nval1    val2\nval3    val4\n"
        result = simple_tables_to_pipe(text)
        assert "| Col A" in result
        assert "| val1" in result
        assert "| val3" in result
        # Should have pipe-table separator
        assert "|---" in result or "| ---" in result

    def test_table_ends_at_blank_line(self):
        text = "H1   H2\n---   ---\nr1   r2\n\nRegular paragraph\n"
        result = simple_tables_to_pipe(text)
        assert "| H1" in result
        assert "| r1" in result
        assert "Regular paragraph" in result

    def test_no_table_unchanged(self):
        text = "Just a regular paragraph.\nAnother line.\n"
        assert simple_tables_to_pipe(text) == text

    def test_single_dash_group_not_treated_as_table(self):
        # Need at least 2 dash groups to be a separator
        text = "Header\n------\nBody\n"
        result = simple_tables_to_pipe(text)
        # Should not be converted (only 1 dash group)
        assert "|" not in result

    def test_empty_input(self):
        assert simple_tables_to_pipe("") == ""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestIsSimpleTableSeparator:
    """Tests for separator line detection."""

    def test_valid_separator(self):
        assert _is_simple_table_separator("-----   -----") is True

    def test_three_columns(self):
        assert _is_simple_table_separator("---  ----  -----") is True

    def test_single_group_not_separator(self):
        assert _is_simple_table_separator("------") is False

    def test_empty_line(self):
        assert _is_simple_table_separator("") is False

    def test_short_dashes_rejected(self):
        # Single dash not enough (need 2+)
        assert _is_simple_table_separator("- -") is False

    def test_two_char_dashes_ok(self):
        assert _is_simple_table_separator("-- --") is True


class TestGetColumnSpans:
    """Tests for column span extraction from separator."""

    def test_basic_spans(self):
        spans = _get_column_spans("-----   -----")
        assert len(spans) == 2
        assert spans[0] == (0, 5)
        assert spans[1] == (8, 13)

    def test_three_column_spans(self):
        spans = _get_column_spans("---  ----  -----")
        assert len(spans) == 3


class TestExtractCells:
    """Tests for cell extraction based on column spans."""

    def test_basic_extraction(self):
        spans = [(0, 5), (8, 13)]
        cells = _extract_cells("hello   world", spans)
        assert cells == ["hello", "world"]

    def test_short_line_handled(self):
        spans = [(0, 5), (8, 13)]
        cells = _extract_cells("hi", spans)
        assert cells[0] == "hi"
        assert cells[1] == ""  # beyond line length


# ---------------------------------------------------------------------------
# postprocess (full pipeline)
# ---------------------------------------------------------------------------


class TestPostprocess:
    """Tests for the combined postprocess pipeline."""

    def test_applies_all_transforms(self):
        text = (
            "::: {.wrapper}\n"
            "[important]{.underline}\n"
            ":::\n"
        )
        result = postprocess(text)
        assert ":::" not in result
        assert "<u>important</u>" in result

    def test_no_transforms_needed(self):
        text = "Plain markdown content\n\n## Heading\n\nParagraph.\n"
        result = postprocess(text)
        assert result == text

    def test_empty_input(self):
        assert postprocess("") == ""

    def test_all_transforms_combined(self):
        text = (
            "::: {.list-item}\n"
            "Col A   Col B\n"
            "-----   -----\n"
            "val1    val2\n"
            "\n"
            "[note]{.underline}\n"
            ":::\n"
        )
        result = postprocess(text)
        assert ":::" not in result
        assert "<u>note</u>" in result
        assert "| Col A" in result
