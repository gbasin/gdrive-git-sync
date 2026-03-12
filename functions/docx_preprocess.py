"""Pre-process .docx XML to normalize field code structures before pandoc.

Google Docs compacts Word field code sequences (fldChar, instrText, text)
into single w:r runs.  Pandoc silently drops text from these mixed runs.
This module splits them back into one-child-per-run so pandoc can extract
the field values correctly.
"""

import logging
import os
import re
import tempfile
import zipfile
from copy import deepcopy
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Tags we care about
_RPR = f"{{{W_NS}}}rPr"
_FLDCHAR = f"{{{W_NS}}}fldChar"
_INSTRTEXT = f"{{{W_NS}}}instrText"
_T = f"{{{W_NS}}}t"
_R = f"{{{W_NS}}}r"

_FIELD_TAGS = {_FLDCHAR, _INSTRTEXT}
_TEXT_TAGS = {_T}


def _register_namespaces(xml_bytes: bytes) -> None:
    """Register all xmlns declarations so ET preserves prefixes on output."""
    for match in re.finditer(rb'xmlns:(\w+)="([^"]+)"', xml_bytes):
        prefix = match.group(1).decode()
        uri = match.group(2).decode()
        ET.register_namespace(prefix, uri)
    # Also register the default namespace if present
    for match in re.finditer(rb'xmlns="([^"]+)"', xml_bytes):
        uri = match.group(1).decode()
        ET.register_namespace("", uri)


def split_mixed_runs(root: ET.Element) -> int:
    """Split w:r elements that mix fldChar/instrText with w:t children.

    Mutates the tree in place.  Returns the number of runs that were split.
    """
    count = 0

    # Iterate over all parents that contain w:r children
    for parent in root.iter():
        children = list(parent)
        i = 0
        while i < len(children):
            run = children[i]
            if run.tag != _R:
                i += 1
                continue

            run_children = list(run)
            rpr = None
            content_children = []
            for child in run_children:
                if child.tag == _RPR:
                    rpr = child
                else:
                    content_children.append(child)

            # Check if this run has BOTH field elements and text elements
            tags = {c.tag for c in content_children}
            has_field = bool(tags & _FIELD_TAGS)
            has_text = bool(tags & _TEXT_TAGS)

            if not (has_field and has_text):
                i += 1
                continue

            # Build replacement runs: one child per new run
            new_runs = []
            for child in content_children:
                new_run = ET.Element(_R, run.attrib)
                if rpr is not None:
                    new_run.append(deepcopy(rpr))
                new_run.append(child)
                new_runs.append(new_run)

            # Replace original run with the sequence of new runs
            idx = list(parent).index(run)
            parent.remove(run)
            for j, new_run in enumerate(new_runs):
                parent.insert(idx + j, new_run)

            count += 1
            # Re-read children list since we mutated
            children = list(parent)
            i += len(new_runs)

        # end while
    # end for

    return count


def preprocess_docx(file_path: str) -> str:
    """Normalize .docx field code XML so pandoc can extract cross-references.

    Returns the original path if no transformation was needed, or a new
    temp file path if the XML was modified.  The caller must delete the
    temp file when done.
    """
    xml_parts: dict[str, tuple[ET.Element, bytes]] = {}  # name -> (root, raw_bytes)

    with zipfile.ZipFile(file_path, "r") as zf:
        for name in zf.namelist():
            # Process document body parts (document, headers, footers, notes)
            if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", name):
                raw = zf.read(name)
                if b"fldChar" not in raw:
                    continue
                _register_namespaces(raw)
                root = ET.fromstring(raw)
                xml_parts[name] = (root, raw)

    if not xml_parts:
        return file_path

    # Apply the transform
    total_splits = 0
    modified_parts: dict[str, bytes] = {}
    for name, (root, _raw) in xml_parts.items():
        splits = split_mixed_runs(root)
        if splits > 0:
            total_splits += splits
            modified_parts[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8")

    if total_splits == 0:
        return file_path

    logger.info("docx_preprocess: split %d mixed runs in %s", total_splits, os.path.basename(file_path))

    # Write a new .docx with the fixed XML
    fd, tmp_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        with zipfile.ZipFile(file_path, "r") as zf_in, zipfile.ZipFile(tmp_path, "w") as zf_out:
            for item in zf_in.infolist():
                if item.filename in modified_parts:
                    zf_out.writestr(item, modified_parts[item.filename])
                else:
                    zf_out.writestr(item, zf_in.read(item.filename))
    except Exception:
        os.unlink(tmp_path)
        raise

    return tmp_path
