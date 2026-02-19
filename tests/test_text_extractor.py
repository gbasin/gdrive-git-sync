"""Tests for functions/text_extractor.py."""

from unittest.mock import MagicMock, patch

import pytest

from text_extractor import (
    _format_table,
    extract_csv,
    extract_docx,
    extract_pdf,
    extract_text,
    get_extracted_filename,
)

# ---------------------------------------------------------------------------
# get_extracted_filename
# ---------------------------------------------------------------------------


class TestGetExtractedFilename:
    """Tests for the filename derivation logic."""

    def test_docx_returns_md(self):
        assert get_extracted_filename("Report.docx") == "Report.docx.md"

    def test_docx_case_insensitive(self):
        assert get_extracted_filename("Report.DOCX") == "Report.DOCX.md"

    def test_pdf_returns_txt(self):
        assert get_extracted_filename("Invoice.pdf") == "Invoice.pdf.txt"

    def test_csv_returns_txt(self):
        assert get_extracted_filename("data.csv") == "data.csv.txt"

    def test_unknown_extension_returns_none(self):
        assert get_extracted_filename("image.png") is None

    def test_no_extension_returns_none(self):
        assert get_extracted_filename("README") is None

    def test_google_doc_mime_type(self):
        mime = "application/vnd.google-apps.document"
        result = get_extracted_filename("My Doc", mime_type=mime)
        assert result == "My Doc.docx.md"

    def test_google_sheet_mime_type(self):
        mime = "application/vnd.google-apps.spreadsheet"
        result = get_extracted_filename("Budget", mime_type=mime)
        assert result == "Budget.csv.txt"

    def test_google_slides_mime_type(self):
        mime = "application/vnd.google-apps.presentation"
        result = get_extracted_filename("Deck", mime_type=mime)
        assert result == "Deck.pdf.txt"

    def test_unknown_mime_falls_back_to_extension(self):
        result = get_extracted_filename("doc.docx", mime_type="application/octet-stream")
        assert result == "doc.docx.md"

    def test_none_mime_falls_back_to_extension(self):
        result = get_extracted_filename("doc.pdf", mime_type=None)
        assert result == "doc.pdf.txt"


# ---------------------------------------------------------------------------
# extract_csv
# ---------------------------------------------------------------------------


class TestExtractCsv:
    """Tests for CSV-to-markdown-table conversion."""

    def test_basic_csv(self, tmp_path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("Name,Age\nAlice,30\nBob,25\n", encoding="utf-8")
        result = extract_csv(str(csv_file))
        assert "| Name" in result
        assert "| Alice" in result
        assert "| Bob" in result
        # Separator row
        assert "| ---" in result

    def test_empty_csv(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("", encoding="utf-8")
        result = extract_csv(str(csv_file))
        assert result == ""

    def test_single_column_csv(self, tmp_path):
        csv_file = tmp_path / "single.csv"
        csv_file.write_text("Header\nval1\nval2\n", encoding="utf-8")
        result = extract_csv(str(csv_file))
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows

    def test_csv_with_special_chars(self, tmp_path):
        csv_file = tmp_path / "special.csv"
        csv_file.write_text('A,B\n"hello, world",test\n', encoding="utf-8")
        result = extract_csv(str(csv_file))
        assert "hello, world" in result


# ---------------------------------------------------------------------------
# _format_table
# ---------------------------------------------------------------------------


class TestFormatTable:
    """Tests for the markdown pipe-table formatter."""

    def test_basic_table(self):
        rows = [["A", "B"], ["1", "2"]]
        result = _format_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header, separator, 1 data row
        assert lines[0].startswith("| A")
        assert "---" in lines[1]
        assert "| 1" in lines[2]

    def test_none_values_replaced(self):
        rows = [["H1", "H2"], [None, "val"]]
        result = _format_table(rows)
        # None should be replaced with empty string, not the word "None"
        assert "None" not in result

    def test_uneven_rows_padded(self):
        rows = [["A", "B", "C"], ["1", "2"]]  # second row is short
        result = _format_table(rows)
        lines = result.strip().split("\n")
        # Data row should have 3 pipe-delimited cells
        cells = [c.strip() for c in lines[2].split("|") if c.strip() != ""]
        assert len(cells) == 3

    def test_empty_rows_returns_empty(self):
        assert _format_table([]) == ""

    def test_minimum_column_width(self):
        rows = [["X", "Y"], ["a", "b"]]
        result = _format_table(rows)
        # Minimum width is 3 characters
        sep_line = result.strip().split("\n")[1]
        dashes = [seg.strip() for seg in sep_line.split("|") if seg.strip()]
        for d in dashes:
            assert len(d) >= 3


# ---------------------------------------------------------------------------
# extract_pdf (mocked)
# ---------------------------------------------------------------------------


class TestExtractPdf:
    """Tests for PDF extraction with mocked pdfplumber."""

    @patch("text_extractor.pdfplumber")
    def test_single_page_text(self, mock_plumber):
        page = MagicMock()
        page.extract_tables.return_value = []
        page.extract_text.return_value = "Hello PDF world"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Hello PDF world" in result
        assert "WARNING" not in result

    @patch("text_extractor.pdfplumber")
    def test_multi_page_with_separator(self, mock_plumber):
        page1 = MagicMock()
        page1.extract_tables.return_value = []
        page1.extract_text.return_value = "Page one text"

        page2 = MagicMock()
        page2.extract_tables.return_value = []
        page2.extract_text.return_value = "Page two text"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Page one text" in result
        assert "--- Page 2 ---" in result
        assert "Page two text" in result

    @patch("text_extractor.pdfplumber")
    def test_empty_page_shows_warning(self, mock_plumber):
        page = MagicMock()
        page.extract_tables.return_value = []
        page.extract_text.return_value = None  # empty / scanned page

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "WARNING" in result
        assert "no extractable text" in result

    @patch("text_extractor.pdfplumber")
    def test_page_with_table(self, mock_plumber):
        page = MagicMock()
        page.extract_tables.return_value = [
            [["Col A", "Col B"], ["v1", "v2"]],
        ]
        page.extract_text.return_value = "Some text after table"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Col A" in result
        assert "Some text after table" in result


# ---------------------------------------------------------------------------
# extract_docx (mocked)
# ---------------------------------------------------------------------------


class TestExtractDocx:
    """Tests for DOCX extraction with mocked pypandoc."""

    @patch("text_extractor.pypandoc")
    @patch("text_extractor.postprocess")
    def test_returns_postprocessed_output(self, mock_postprocess, mock_pypandoc):
        mock_pypandoc.convert_file.return_value = "raw pandoc output"
        mock_postprocess.return_value = "cleaned output"

        result = extract_docx("doc.docx")
        assert result == "cleaned output"

        mock_pypandoc.convert_file.assert_called_once_with(
            "doc.docx",
            "markdown",
            format="docx",
            extra_args=["--track-changes=all", "--wrap=none"],
        )
        mock_postprocess.assert_called_once_with("raw pandoc output")

    @patch("text_extractor.pypandoc")
    @patch("text_extractor.postprocess")
    def test_passes_through_pandoc_errors(self, mock_postprocess, mock_pypandoc):
        mock_pypandoc.convert_file.side_effect = RuntimeError("pandoc failed")

        with pytest.raises(RuntimeError, match="pandoc failed"):
            extract_docx("bad.docx")


# ---------------------------------------------------------------------------
# extract_text (integration-style, mocked)
# ---------------------------------------------------------------------------


class TestExtractText:
    """Tests for the top-level extract_text dispatcher."""

    @patch("text_extractor.extract_csv")
    def test_csv_dispatches_correctly(self, mock_csv, tmp_path):
        mock_csv.return_value = "| A |\n| --- |\n| 1 |"
        output = tmp_path / "out.txt"
        result = extract_text(str(tmp_path / "data.csv"), str(output))
        assert result is True
        mock_csv.assert_called_once()

    def test_unknown_extension_returns_false(self, tmp_path):
        output = tmp_path / "out.txt"
        result = extract_text(str(tmp_path / "image.png"), str(output))
        assert result is False

    @patch("text_extractor.extract_pdf")
    def test_extraction_failure_returns_false(self, mock_pdf, tmp_path):
        mock_pdf.side_effect = Exception("boom")
        output = tmp_path / "out.txt"
        result = extract_text(str(tmp_path / "doc.pdf"), str(output))
        assert result is False
