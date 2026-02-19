"""Tests for functions/text_extractor.py."""

import os
from unittest.mock import MagicMock, patch

import pypandoc
import pytest

from text_extractor import (
    EXTRACTABLE,
    GOOGLE_NATIVE_EXPORTS,
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
        # Data row should have 3 pipe-delimited cells (including padded empty one)
        # Split on | gives ['', ' 1 ', ' 2 ', ' ', ''] — inner elements are cells
        cells = lines[2].split("|")[1:-1]  # strip leading/trailing empty from split
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

    @patch("text_extractor.pdfplumber")
    def test_multiple_tables_on_one_page(self, mock_plumber):
        """Multiple tables on a single page are all included."""
        page = MagicMock()
        page.extract_tables.return_value = [
            [["T1 A", "T1 B"], ["t1v1", "t1v2"]],
            [["T2 X", "T2 Y"], ["t2v1", "t2v2"]],
        ]
        page.extract_text.return_value = "Body text"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "T1 A" in result
        assert "T2 X" in result
        assert "Body text" in result

    @patch("text_extractor.pdfplumber")
    def test_table_only_page_no_warning(self, mock_plumber):
        """A page with tables but no text should NOT show a scanned-page warning."""
        page = MagicMock()
        page.extract_tables.return_value = [
            [["Header"], ["row1"]],
        ]
        page.extract_text.return_value = None  # no body text

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Header" in result
        assert "WARNING" not in result
        assert "no extractable text" not in result

    @patch("text_extractor.pdfplumber")
    def test_mixed_pages_some_empty(self, mock_plumber):
        """Multi-page PDF: page 1 has text, page 2 is empty/scanned."""
        page1 = MagicMock()
        page1.extract_tables.return_value = []
        page1.extract_text.return_value = "Real content"

        page2 = MagicMock()
        page2.extract_tables.return_value = []
        page2.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Real content" in result
        assert "WARNING" in result
        assert "[Page 2: no extractable text" in result

    @patch("text_extractor.pdfplumber")
    def test_all_empty_pages(self, mock_plumber):
        """PDF where every page is empty/scanned."""
        page1 = MagicMock()
        page1.extract_tables.return_value = []
        page1.extract_text.return_value = None

        page2 = MagicMock()
        page2.extract_tables.return_value = []
        page2.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "WARNING" in result
        assert "[Page 1: no extractable text" in result
        assert "[Page 2: no extractable text" in result

    @patch("text_extractor.pdfplumber")
    def test_zero_pages(self, mock_plumber):
        """PDF with no pages at all."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert result == ""
        assert "WARNING" not in result

    @patch("text_extractor.pdfplumber")
    def test_table_with_none_cells(self, mock_plumber):
        """Tables from pdfplumber can have None cells."""
        page = MagicMock()
        page.extract_tables.return_value = [
            [["Name", None], [None, "value"]],
        ]
        page.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        result = extract_pdf("dummy.pdf")
        assert "Name" in result
        assert "value" in result
        assert "None" not in result  # None values should be replaced


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
        assert output.read_text(encoding="utf-8") == "| A |\n| --- |\n| 1 |"

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

    @patch("text_extractor.extract_docx")
    def test_docx_dispatches_correctly(self, mock_docx, tmp_path):
        mock_docx.return_value = "# Heading\n\nBody text"
        output = tmp_path / "out.md"
        result = extract_text(str(tmp_path / "report.docx"), str(output))
        assert result is True
        mock_docx.assert_called_once()
        assert output.read_text(encoding="utf-8") == "# Heading\n\nBody text"

    @patch("text_extractor.extract_pdf")
    def test_pdf_dispatches_correctly(self, mock_pdf, tmp_path):
        mock_pdf.return_value = "Page one content"
        output = tmp_path / "out.txt"
        result = extract_text(str(tmp_path / "invoice.pdf"), str(output))
        assert result is True
        mock_pdf.assert_called_once()
        assert output.read_text(encoding="utf-8") == "Page one content"

    @patch("text_extractor.extract_docx")
    def test_case_insensitive_extension(self, mock_docx, tmp_path):
        """Extract_text should handle uppercase extensions."""
        mock_docx.return_value = "content"
        output = tmp_path / "out.md"
        result = extract_text(str(tmp_path / "Report.DOCX"), str(output))
        assert result is True

    @patch("text_extractor.extract_csv")
    def test_output_file_written_utf8(self, mock_csv, tmp_path):
        """Output file should be written with UTF-8 encoding."""
        mock_csv.return_value = "| Ñame | Ünit |\n| --- | --- |\n| café | résumé |"
        output = tmp_path / "out.txt"
        result = extract_text(str(tmp_path / "data.csv"), str(output))
        assert result is True
        content = output.read_text(encoding="utf-8")
        assert "café" in content
        assert "résumé" in content


# ---------------------------------------------------------------------------
# Real DOCX integration tests (round-trip through pypandoc)
# ---------------------------------------------------------------------------


def _pandoc_available():
    try:
        pypandoc.get_pandoc_version()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _pandoc_available(), reason="pandoc not available")
class TestExtractDocxIntegration:
    """Integration tests using real pypandoc to create and extract .docx files."""

    def test_round_trip_heading_and_paragraph(self, tmp_path):
        """Create a real docx from markdown, extract it back, verify content."""
        md_source = "# Test Heading\n\nThis is a paragraph with **bold** text.\n"
        docx_path = str(tmp_path / "test.docx")
        pypandoc.convert_text(md_source, "docx", format="md", outputfile=docx_path)

        result = extract_docx(docx_path)
        assert "Test Heading" in result
        assert "bold" in result
        assert "paragraph" in result

    def test_round_trip_bullet_list(self, tmp_path):
        """Bullet lists survive the round trip."""
        md_source = "Items:\n\n- Apple\n- Banana\n- Cherry\n"
        docx_path = str(tmp_path / "list.docx")
        pypandoc.convert_text(md_source, "docx", format="md", outputfile=docx_path)

        result = extract_docx(docx_path)
        assert "Apple" in result
        assert "Banana" in result
        assert "Cherry" in result

    def test_round_trip_table(self, tmp_path):
        """Tables survive the round trip."""
        md_source = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |\n"
        docx_path = str(tmp_path / "table.docx")
        pypandoc.convert_text(md_source, "docx", format="md", outputfile=docx_path)

        result = extract_docx(docx_path)
        assert "Name" in result
        assert "Alice" in result
        assert "Bob" in result

    def test_round_trip_unicode(self, tmp_path):
        """Unicode content survives the round trip."""
        md_source = "# Résumé\n\nCafé, naïve, über, 日本語\n"
        docx_path = str(tmp_path / "unicode.docx")
        pypandoc.convert_text(md_source, "docx", format="md", outputfile=docx_path)

        result = extract_docx(docx_path)
        assert "Résumé" in result
        assert "Café" in result or "café" in result.lower()
        assert "日本語" in result

    def test_extract_text_docx_end_to_end(self, tmp_path):
        """Full end-to-end: extract_text dispatcher with a real docx."""
        md_source = "# Hello World\n\nContent here.\n"
        docx_path = str(tmp_path / "hello.docx")
        output_path = str(tmp_path / "hello.docx.md")
        pypandoc.convert_text(md_source, "docx", format="md", outputfile=docx_path)

        result = extract_text(docx_path, output_path)
        assert result is True
        assert os.path.exists(output_path)
        with open(output_path, encoding="utf-8") as f:
            content = f.read()
        assert "Hello World" in content
        assert "Content here" in content


# ---------------------------------------------------------------------------
# Constants coverage
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify the extraction config maps are complete and consistent."""

    def test_extractable_extensions_all_handled(self):
        """Every extension in EXTRACTABLE should be handled by extract_text."""
        for ext in EXTRACTABLE:
            assert ext in (".docx", ".pdf", ".csv")

    def test_google_native_exports_all_have_extractors(self):
        """Every Google-native export format should map to an extractable extension."""
        for mime, (_fmt, ext, _) in GOOGLE_NATIVE_EXPORTS.items():
            assert ext in EXTRACTABLE, f"{mime} exports to {ext} which has no extractor"

    def test_google_native_exports_have_valid_mime_types(self):
        """All Google-native MIME types should start with the Google apps prefix."""
        for mime in GOOGLE_NATIVE_EXPORTS:
            assert mime.startswith("application/vnd.google-apps.")


# ---------------------------------------------------------------------------
# extract_pdf edge cases
# ---------------------------------------------------------------------------


class TestExtractPdfEdgeCases:
    """Edge cases for PDF extraction: encrypted files, mid-page errors."""

    @patch("text_extractor.pdfplumber")
    def test_encrypted_pdf_caught_gracefully(self, mock_plumber, tmp_path):
        """Encrypted PDFs cause pdfplumber.open to raise; extract_text should
        catch the exception and return False without crashing."""
        mock_plumber.open.side_effect = Exception("file has not been decrypted")

        output = tmp_path / "out.txt"
        result = extract_text("encrypted.pdf", str(output))

        assert result is False
        assert not output.exists()

    @patch("text_extractor.pdfplumber")
    def test_pdf_page_extract_text_raises(self, mock_plumber):
        """If page.extract_text() raises mid-page, extract_pdf should propagate
        the exception (it is extract_text that catches it, not extract_pdf)."""
        page = MagicMock()
        page.extract_tables.return_value = []
        page.extract_text.side_effect = RuntimeError("corrupt page data")

        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_plumber.open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
        mock_plumber.open.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="corrupt page data"):
            extract_pdf("broken.pdf")


# ---------------------------------------------------------------------------
# extract_docx edge cases
# ---------------------------------------------------------------------------


class TestExtractDocxEdgeCases:
    """Edge cases for DOCX extraction: corrupted files, empty content."""

    @patch("text_extractor.postprocess")
    @patch("text_extractor.pypandoc")
    def test_corrupted_docx_caught_gracefully(self, mock_pypandoc, mock_postprocess, tmp_path):
        """A corrupted DOCX that causes pypandoc to raise RuntimeError should
        be caught by extract_text, returning False without crashing."""
        mock_pypandoc.convert_file.side_effect = RuntimeError("Invalid docx file")

        output = tmp_path / "out.md"
        result = extract_text("bad.docx", str(output))

        assert result is False
        assert not output.exists()

    @patch("text_extractor.postprocess")
    @patch("text_extractor.pypandoc")
    def test_empty_docx_returns_empty_string(self, mock_pypandoc, mock_postprocess, tmp_path):
        """A DOCX with no content produces an empty output file and returns True."""
        mock_pypandoc.convert_file.return_value = ""
        mock_postprocess.return_value = ""

        output = tmp_path / "out.md"
        result = extract_text("empty.docx", str(output))

        assert result is True
        assert output.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# extract_csv edge cases
# ---------------------------------------------------------------------------


class TestExtractCsvEdgeCases:
    """Edge cases for CSV extraction: encoding issues, wide tables, blank rows."""

    def test_non_utf8_csv_caught_gracefully(self, tmp_path):
        """A CSV encoded in Windows-1252 (not UTF-8) triggers a
        UnicodeDecodeError which extract_text should catch, returning False."""
        csv_file = tmp_path / "bad.csv"
        # \xe9 is 'e-acute' in Windows-1252 but invalid as a standalone
        # byte in UTF-8, so open(..., encoding="utf-8") will fail.
        csv_file.write_bytes(b"Name,City\nJos\xe9,Montr\xe9al\n")

        output = tmp_path / "out.txt"
        result = extract_text(str(csv_file), str(output))

        assert result is False
        assert not output.exists()

    def test_csv_with_many_columns(self, tmp_path):
        """A CSV with 20+ columns should still produce a correctly
        formatted markdown table with one pipe-delimited cell per column."""
        headers = [f"Col{i}" for i in range(25)]
        values = [f"val{i}" for i in range(25)]

        csv_file = tmp_path / "wide.csv"
        csv_file.write_text(
            ",".join(headers) + "\n" + ",".join(values) + "\n",
            encoding="utf-8",
        )

        result = extract_csv(str(csv_file))

        lines = result.strip().split("\n")
        assert len(lines) == 3  # header + separator + 1 data row

        # Every line should have exactly 25 cells (26 pipes = 25 cells)
        for line in lines:
            pipes = line.count("|")
            # Each row: "| c1 | c2 | ... | c25 |"  --> 26 pipes
            assert pipes == 26, f"Expected 26 pipes, got {pipes}: {line!r}"

        # Spot-check first and last column content
        assert "Col0" in result
        assert "Col24" in result
        assert "val0" in result
        assert "val24" in result

    def test_csv_with_empty_rows(self, tmp_path):
        """A CSV with blank lines between data rows should be handled
        gracefully without crashing."""
        csv_file = tmp_path / "gaps.csv"
        csv_file.write_text(
            "A,B\n\n1,2\n\n3,4\n",
            encoding="utf-8",
        )

        result = extract_csv(str(csv_file))

        # The CSV reader treats blank lines as rows with a single empty string,
        # so the table should still contain our data.
        assert "A" in result
        assert "B" in result
        assert "1" in result
        assert "3" in result


# ---------------------------------------------------------------------------
# Special filenames
# ---------------------------------------------------------------------------


class TestSpecialFilenames:
    """Verify get_extracted_filename handles unusual filenames correctly."""

    def test_filename_with_spaces_and_parens(self):
        result = get_extracted_filename("Q1 Report (Final).docx")
        assert result == "Q1 Report (Final).docx.md"

    def test_filename_with_unicode(self):
        result = get_extracted_filename("R\u00e9sum\u00e9.pdf")
        assert result == "R\u00e9sum\u00e9.pdf.txt"

    def test_filename_with_dots(self):
        result = get_extracted_filename("report.v2.final.docx")
        assert result == "report.v2.final.docx.md"

    def test_filename_with_leading_dot(self):
        result = get_extracted_filename(".hidden.docx")
        assert result == ".hidden.docx.md"
