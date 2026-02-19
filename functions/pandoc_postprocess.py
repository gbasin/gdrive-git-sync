"""Post-process pandoc markdown output for cleaner diffs.

Fixes:
- Strip ::: fenced div wrappers from bullet lists
- Convert pandoc simple tables to pipe tables
- Clean {.underline} spans to <u> tags
"""

import re


def postprocess(markdown: str) -> str:
    """Apply all post-processing steps to pandoc markdown output."""
    result = markdown
    result = strip_fenced_divs(result)
    result = clean_underline_spans(result)
    result = simple_tables_to_pipe(result)
    return result


def strip_fenced_divs(text: str) -> str:
    """Remove ::: fenced div wrappers, keeping their content.

    Pandoc wraps list items and other blocks in ::: {.some-class} ... :::
    which adds noise to diffs.
    """
    # Remove opening ::: lines (with optional attributes)
    text = re.sub(r"^:::\s*\{[^}]*\}\s*$\n?", "", text, flags=re.MULTILINE)
    # Remove closing ::: lines
    text = re.sub(r"^:::\s*$\n?", "", text, flags=re.MULTILINE)
    return text


def clean_underline_spans(text: str) -> str:
    """Convert [text]{.underline} to <u>text</u>."""
    return re.sub(r"\[([^\]]+)\]\{\.underline\}", r"<u>\1</u>", text)


def simple_tables_to_pipe(text: str) -> str:
    """Convert pandoc simple tables to pipe tables.

    Simple tables look like:
      Col A   Col B
      -----   -----
      val1    val2
      val3    val4

    Pipe tables look like:
      | Col A | Col B |
      |-------|-------|
      | val1  | val2  |
      | val3  | val4  |
    """
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        # Look for a separator line (--- patterns with spaces)
        if _is_simple_table_separator(lines[i]):
            sep_line = lines[i]
            col_spans = _get_column_spans(sep_line)

            if col_spans and i > 0:
                # Previous line is the header
                header_line = lines[i - 1]
                # Replace the header we already added
                if result and result[-1] == header_line:
                    result.pop()

                header_cells = _extract_cells(header_line, col_spans)
                result.append("| " + " | ".join(header_cells) + " |")
                result.append("| " + " | ".join("-" * len(c) for c in header_cells) + " |")

                # Process body rows until empty line or end
                i += 1
                while i < len(lines) and lines[i].strip():
                    row_cells = _extract_cells(lines[i], col_spans)
                    result.append("| " + " | ".join(row_cells) + " |")
                    i += 1
                continue
        result.append(lines[i])
        i += 1

    return "\n".join(result)


def _is_simple_table_separator(line: str) -> bool:
    """Check if a line is a simple table separator (e.g., '---- -----')."""
    stripped = line.strip()
    if not stripped:
        return False
    # Must contain dashes and spaces, with at least 2 dash groups
    parts = stripped.split()
    if len(parts) < 2:
        return False
    return all(re.match(r"^-{2,}$", p) for p in parts)


def _get_column_spans(sep_line: str) -> list[tuple[int, int]]:
    """Get (start, end) character positions for each column from separator."""
    spans = []
    for match in re.finditer(r"-{2,}", sep_line):
        spans.append((match.start(), match.end()))
    return spans


def _extract_cells(line: str, col_spans: list[tuple[int, int]]) -> list[str]:
    """Extract cell values from a line based on column spans."""
    cells = []
    for start, end in col_spans:
        # Extend end to capture full cell content (up to next column or EOL)
        cell = line[start:end] if end <= len(line) else line[start:]
        cells.append(cell.strip())
    return cells
