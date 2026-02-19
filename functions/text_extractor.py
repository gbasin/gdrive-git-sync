"""Extract diffable text from binary document formats.

- docx → markdown via pandoc (with track changes support)
- pdf → text via pdfplumber
- csv → markdown table via markdownify
"""

import csv
import logging
import os

import pdfplumber
import pypandoc

from pandoc_postprocess import postprocess

logger = logging.getLogger(__name__)

# Map of extractable extensions to their handler
EXTRACTABLE = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".csv": "csv",
}

# Google-native MIME types → export format → extraction type
GOOGLE_NATIVE_EXPORTS = {
    "application/vnd.google-apps.document": (
        "docx",
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "application/vnd.google-apps.spreadsheet": ("csv", ".csv", "text/csv"),
    "application/vnd.google-apps.presentation": ("pdf", ".pdf", "application/pdf"),
}


def get_extracted_filename(original_name: str, mime_type: str | None = None) -> str | None:
    """Return the extracted text filename for a given original, or None if not extractable.

    For Google-native files, uses the mime_type to determine format.
    """
    if mime_type and mime_type in GOOGLE_NATIVE_EXPORTS:
        fmt, ext, _ = GOOGLE_NATIVE_EXPORTS[mime_type]
        if fmt == "docx":
            return original_name + ext + ".md"
        if fmt == "csv" or fmt == "pdf":
            return original_name + ext + ".txt"

    _, ext = os.path.splitext(original_name.lower())
    if ext == ".docx":
        return original_name + ".md"
    if ext == ".pdf" or ext == ".csv":
        return original_name + ".txt"
    return None


def extract_text(file_path: str, output_path: str, mime_type: str | None = None) -> bool:
    """Extract diffable text from a file.

    Args:
        file_path: Path to the source file.
        output_path: Path to write extracted text.
        mime_type: MIME type (needed for Google-native files).

    Returns:
        True if extraction succeeded, False otherwise.
    """
    _, ext = os.path.splitext(file_path.lower())

    try:
        if ext == ".docx":
            text = extract_docx(file_path)
        elif ext == ".pdf":
            text = extract_pdf(file_path)
        elif ext == ".csv":
            text = extract_csv(file_path)
        else:
            logger.warning(f"No extractor for {ext}: {file_path}")
            return False

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        return True

    except Exception:
        logger.exception(f"Extraction failed for {file_path}")
        return False


def extract_docx(file_path: str) -> str:
    """Convert docx to markdown using pandoc with track changes support."""
    output = pypandoc.convert_file(
        file_path,
        "markdown",
        format="docx",
        extra_args=["--track-changes=all", "--wrap=none"],
    )
    return postprocess(output)


def extract_pdf(file_path: str) -> str:
    """Extract text from PDF using pdfplumber.

    Includes table extraction and scanned page warnings.
    """
    parts = []
    has_empty_pages = False

    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages):
            if i > 0:
                parts.append(f"\n--- Page {i + 1} ---\n")

            # Try table extraction first
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    parts.append(_format_table(table))
                    parts.append("")

            # Extract body text
            text = page.extract_text()
            if text:
                parts.append(text)
            elif not tables:
                has_empty_pages = True
                parts.append(f"[Page {i + 1}: no extractable text (possibly scanned)]")

    result = "\n".join(parts)
    if has_empty_pages:
        result = "WARNING: Some pages contain no extractable text (scanned/image-only).\n\n" + result

    return result


def extract_csv(file_path: str) -> str:
    """Convert CSV to markdown table."""
    with open(file_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return ""

    # csv.reader yields list[str]; _format_table accepts str | None for pdfplumber compat
    return _format_table(rows)  # type: ignore[arg-type]


def _format_table(rows: list[list[str | None]]) -> str:
    """Format a list of rows as a markdown pipe table."""
    if not rows:
        return ""

    # Clean None values
    cleaned = [[cell or "" for cell in row] for row in rows]

    # Normalize column count
    max_cols = max(len(row) for row in cleaned)
    for row in cleaned:
        while len(row) < max_cols:
            row.append("")

    # Calculate column widths
    widths = [max(len(row[c]) for row in cleaned) for c in range(max_cols)]
    widths = [max(w, 3) for w in widths]  # minimum width of 3

    def format_row(row):
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row)]
        return "| " + " | ".join(cells) + " |"

    lines = [format_row(cleaned[0])]
    lines.append("| " + " | ".join("-" * w for w in widths) + " |")
    for row in cleaned[1:]:
        lines.append(format_row(row))

    return "\n".join(lines)
