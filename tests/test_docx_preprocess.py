"""Tests for functions/docx_preprocess.py."""

import os
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from docx_preprocess import W_NS, preprocess_docx, split_mixed_runs

# Shorthand tags
_R = f"{{{W_NS}}}r"
_RPR = f"{{{W_NS}}}rPr"
_T = f"{{{W_NS}}}t"
_FLDCHAR = f"{{{W_NS}}}fldChar"
_INSTRTEXT = f"{{{W_NS}}}instrText"
_BODY = f"{{{W_NS}}}body"
_P = f"{{{W_NS}}}p"
_SZ = f"{{{W_NS}}}sz"
_U = f"{{{W_NS}}}u"


def _make_run(*children, rpr_children=None, attribs=None):
    """Build a w:r element with optional rPr and child elements."""
    r = ET.Element(_R, attribs or {})
    if rpr_children:
        rpr = ET.SubElement(r, _RPR)
        for child_tag, child_attribs in rpr_children:
            ET.SubElement(rpr, child_tag, child_attribs)
    for tag, attribs_dict, text in children:
        el = ET.SubElement(r, tag, attribs_dict)
        if text:
            el.text = text
    return r


def _make_tree(*runs):
    """Wrap runs in a minimal body > p > ... tree."""
    body = ET.Element(_BODY)
    p = ET.SubElement(body, _P)
    for run in runs:
        p.append(run)
    return body


def _fld(fld_type):
    """Shorthand for a fldChar child tuple."""
    return (_FLDCHAR, {f"{{{W_NS}}}fldCharType": fld_type}, None)


def _instr(text):
    """Shorthand for an instrText child tuple."""
    return (_INSTRTEXT, {"xml:space": "preserve"}, text)


def _text(text):
    """Shorthand for a w:t child tuple."""
    return (_T, {}, text)


# ---------------------------------------------------------------------------
# Unit tests for split_mixed_runs
# ---------------------------------------------------------------------------


class TestSplitMixedRuns:
    def test_basic_mixed_run_is_split(self):
        """A run with t + fldChar + instrText + fldChar + t + fldChar is split into 6 runs."""
        run = _make_run(
            _text("See "),
            _fld("begin"),
            _instr(" REF _Ref123 \\w \\h "),
            _fld("separate"),
            _text("4.2"),
            _fld("end"),
        )
        tree = _make_tree(run)
        count = split_mixed_runs(tree)

        assert count == 1
        p = tree.find(_P)
        runs = list(p)
        assert len(runs) == 6
        # Each run should have exactly one content child
        for r in runs:
            content = [c for c in r if c.tag != _RPR]
            assert len(content) == 1

    def test_text_content_preserved_after_split(self):
        """All text values survive the split unchanged."""
        run = _make_run(
            _text("Item "),
            _fld("begin"),
            _instr(" REF _Ref999 \\r \\h "),
            _fld("separate"),
            _text("3.1(b)"),
            _fld("end"),
        )
        tree = _make_tree(run)
        split_mixed_runs(tree)

        p = tree.find(_P)
        texts = [r.find(_T).text for r in p if r.find(_T) is not None]
        assert texts == ["Item ", "3.1(b)"]

    def test_trailing_text_after_field_end(self):
        """Text after fldChar end gets its own run."""
        run = _make_run(
            _text("See "),
            _fld("begin"),
            _instr(" REF _Ref1 \\h "),
            _fld("separate"),
            _text("2.1"),
            _fld("end"),
            _text("; and"),
        )
        tree = _make_tree(run)
        split_mixed_runs(tree)

        p = tree.find(_P)
        runs = list(p)
        assert len(runs) == 7
        # Last run should be "; and"
        last_t = runs[-1].find(_T)
        assert last_t is not None
        assert last_t.text == "; and"

    def test_two_fields_in_one_run(self):
        """Two back-to-back field codes in one run are correctly split."""
        run = _make_run(
            _text("Parts "),
            _fld("begin"),
            _instr(" REF _RefA \\h "),
            _fld("separate"),
            _text("I"),
            _fld("end"),
            _text(" and "),
            _fld("begin"),
            _instr(" REF _RefB \\h "),
            _fld("separate"),
            _text("II"),
            _fld("end"),
        )
        tree = _make_tree(run)
        count = split_mixed_runs(tree)

        assert count == 1
        p = tree.find(_P)
        runs = list(p)
        assert len(runs) == 12

    def test_clean_run_not_touched(self):
        """A run with only text is left unchanged."""
        run = _make_run(_text("Hello world"))
        tree = _make_tree(run)
        count = split_mixed_runs(tree)

        assert count == 0
        p = tree.find(_P)
        assert len(list(p)) == 1

    def test_field_only_run_not_touched(self):
        """A run with only fldChar (no text) is left unchanged."""
        run = _make_run(_fld("begin"))
        tree = _make_tree(run)
        count = split_mixed_runs(tree)

        assert count == 0

    def test_rpr_is_deep_copied_to_each_new_run(self):
        """Each split run gets an independent copy of rPr."""
        formatting = [(_SZ, {f"{{{W_NS}}}val": "22"}), (_U, {f"{{{W_NS}}}val": "single"})]
        run = _make_run(
            _text("Clause "),
            _fld("begin"),
            _instr(" REF _Ref1 \\h "),
            _fld("separate"),
            _text("5"),
            _fld("end"),
            rpr_children=formatting,
        )
        tree = _make_tree(run)
        split_mixed_runs(tree)

        p = tree.find(_P)
        rprs = [r.find(_RPR) for r in p]
        # All 6 runs should have rPr
        assert all(rpr is not None for rpr in rprs)
        # Each rPr should be a distinct object
        rpr_ids = [id(rpr) for rpr in rprs]
        assert len(set(rpr_ids)) == 6

    def test_run_attribs_preserved(self):
        """Original run attributes (like rsidR) propagate to split runs."""
        run = _make_run(
            _text("Art. "),
            _fld("begin"),
            _instr(" REF _Ref1 \\h "),
            _fld("separate"),
            _text("7"),
            _fld("end"),
            attribs={f"{{{W_NS}}}rsidR": "00AB1234"},
        )
        tree = _make_tree(run)
        split_mixed_runs(tree)

        p = tree.find(_P)
        for r in p:
            assert r.get(f"{{{W_NS}}}rsidR") == "00AB1234"

    def test_returns_count_of_split_runs(self):
        """Return value is the number of original runs that were split."""
        run1 = _make_run(_text("A "), _fld("begin"), _instr(" REF _R1 "), _fld("separate"), _text("1"), _fld("end"))
        run2 = _make_run(_text("plain text"))
        run3 = _make_run(_text("B "), _fld("begin"), _instr(" REF _R2 "), _fld("separate"), _text("2"), _fld("end"))
        tree = _make_tree(run1, run2, run3)

        count = split_mixed_runs(tree)
        assert count == 2

    def test_no_mixed_runs_returns_zero(self):
        """A tree with no mixed runs returns 0."""
        run1 = _make_run(_text("Hello"))
        run2 = _make_run(_fld("begin"))
        run3 = _make_run(_instr(" REF _Ref1 \\h "))
        run4 = _make_run(_fld("separate"))
        run5 = _make_run(_text("1.1"))
        run6 = _make_run(_fld("end"))
        tree = _make_tree(run1, run2, run3, run4, run5, run6)

        count = split_mixed_runs(tree)
        assert count == 0


# ---------------------------------------------------------------------------
# Integration tests for preprocess_docx using synthetic .docx files
# ---------------------------------------------------------------------------

# Minimal OOXML boilerplate
_CONTENT_TYPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """\
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""

_DOC_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>{runs}</w:p>
  </w:body>
</w:document>"""


def _build_docx(document_xml: str) -> str:
    """Create a minimal .docx file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document_xml)
    return path


# A mixed run: text + fldChar + instrText + fldChar + text + fldChar in one w:r
_MIXED_RUN_DOC = _DOC_TEMPLATE.format(
    runs="""
      <w:r>
        <w:rPr><w:u w:val="single"/></w:rPr>
        <w:t xml:space="preserve">Clause </w:t>
        <w:fldChar w:fldCharType="begin"/>
        <w:instrText xml:space="preserve"> REF _Ref12345 \\w \\h </w:instrText>
        <w:fldChar w:fldCharType="separate"/>
        <w:t>9.3(a)</w:t>
        <w:fldChar w:fldCharType="end"/>
      </w:r>"""
)

# A clean run: already split across multiple w:r
_CLEAN_RUN_DOC = _DOC_TEMPLATE.format(
    runs="""
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">Clause </w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:instrText xml:space="preserve"> REF _Ref12345 \\w \\h </w:instrText></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>9.3(a)</w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>"""
)

# Plain doc with no field codes at all
_NO_FIELDS_DOC = _DOC_TEMPLATE.format(runs="<w:r><w:t>Just plain text.</w:t></w:r>")


class TestPreprocessDocx:
    def test_mixed_runs_returns_new_path(self):
        """A .docx with mixed runs should return a new temp file path."""
        path = _build_docx(_MIXED_RUN_DOC)
        try:
            result = preprocess_docx(path)
            assert result != path
            assert os.path.exists(result)
            os.unlink(result)
        finally:
            os.unlink(path)

    def test_clean_runs_returns_original_path(self):
        """A .docx with already-clean runs should return the original path."""
        path = _build_docx(_CLEAN_RUN_DOC)
        try:
            result = preprocess_docx(path)
            assert result == path
        finally:
            os.unlink(path)

    def test_no_fields_returns_original_path(self):
        """A .docx with no field codes should return the original path."""
        path = _build_docx(_NO_FIELDS_DOC)
        try:
            result = preprocess_docx(path)
            assert result == path
        finally:
            os.unlink(path)

    def test_fixed_docx_is_valid_zip(self):
        """The returned file should be a valid zip with the same entries."""
        path = _build_docx(_MIXED_RUN_DOC)
        try:
            result = preprocess_docx(path)
            with zipfile.ZipFile(path) as orig, zipfile.ZipFile(result) as fixed:
                assert set(orig.namelist()) == set(fixed.namelist())
            os.unlink(result)
        finally:
            os.unlink(path)

    def test_fixed_docx_has_no_mixed_runs(self):
        """After preprocessing, document.xml should have no mixed runs."""
        path = _build_docx(_MIXED_RUN_DOC)
        try:
            result = preprocess_docx(path)
            with zipfile.ZipFile(result) as zf:
                root = ET.fromstring(zf.read("word/document.xml"))
            # Running split again should find 0 mixed runs
            assert split_mixed_runs(root) == 0
            os.unlink(result)
        finally:
            os.unlink(path)

    def test_non_document_xml_unchanged(self):
        """Other zip entries should be byte-identical."""
        path = _build_docx(_MIXED_RUN_DOC)
        try:
            result = preprocess_docx(path)
            with zipfile.ZipFile(path) as orig, zipfile.ZipFile(result) as fixed:
                assert orig.read("[Content_Types].xml") == fixed.read("[Content_Types].xml")
                assert orig.read("_rels/.rels") == fixed.read("_rels/.rels")
            os.unlink(result)
        finally:
            os.unlink(path)


class TestPreprocessDocxPandocIntegration:
    """End-to-end: preprocess + pandoc extraction preserves field values."""

    def test_mixed_run_field_value_extracted(self):
        """Pandoc should extract field display text from a preprocessed mixed-run .docx."""
        from text_extractor import extract_docx

        path = _build_docx(_MIXED_RUN_DOC)
        try:
            text = extract_docx(path)
            assert "9.3(a)" in text
            assert "Clause" in text
        finally:
            os.unlink(path)

    def test_clean_run_field_value_extracted(self):
        """Pandoc should extract field text from an already-clean .docx (no preprocessing needed)."""
        from text_extractor import extract_docx

        path = _build_docx(_CLEAN_RUN_DOC)
        try:
            text = extract_docx(path)
            assert "9.3(a)" in text
            assert "Clause" in text
        finally:
            os.unlink(path)

    def test_mixed_and_clean_produce_same_output(self):
        """Mixed and clean versions of the same content should produce identical pandoc output."""
        from text_extractor import extract_docx

        mixed_path = _build_docx(_MIXED_RUN_DOC)
        clean_path = _build_docx(_CLEAN_RUN_DOC)
        try:
            mixed_text = extract_docx(mixed_path)
            clean_text = extract_docx(clean_path)
            assert mixed_text == clean_text
        finally:
            os.unlink(mixed_path)
            os.unlink(clean_path)
