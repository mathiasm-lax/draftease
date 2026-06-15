"""
Draftease redline engine
=========================

The deterministic core of Draftease. Given:
  * a *form lease* .docx containing placeholder tokens like ``{{base_rent}}``, and
  * a dict of *deal terms* (extracted from a signed LOI) mapping token -> value,

it produces a new .docx in which every placeholder has been replaced with the
deal-specific value **as a native Word tracked change** (a ``w:del`` of the
placeholder followed by a ``w:ins`` of the value). Opening the output in
Microsoft Word shows a real redline that a reviewer can Accept or Reject.

Design notes
------------
* No AI, no network. This is plain OOXML manipulation, so the lease template and
  the resulting redline never leave the machine this runs on.
* Run formatting (``w:rPr``) is preserved: each replacement copies the formatting
  of the text it replaces, so bold / font / size survive.
* Tokens that span multiple runs are handled (Word frequently splits text across
  runs); the paragraph text is reconstructed character-by-character with a map
  back to each character's formatting.
* Paragraphs whose tokenised runs also contain tabs/breaks/drawings are left
  untouched by the rebuild path to avoid dropping those elements (rare in lease
  bodies; logged in the report).

Public API
----------
    generate_redline(template_path, terms, output_path, author="Draftease", date=None) -> dict
"""

from __future__ import annotations

import copy
import datetime as _dt
import re
from typing import Dict

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# {{ token_name }} — letters, digits and underscores, optional surrounding spaces
TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
# run-level child tags that we are not willing to silently drop during a rebuild
_NON_TEXT_RUN_CHILDREN = {qn("w:tab"), qn("w:br"), qn("w:drawing"), qn("w:cr"), qn("w:pict")}


# --------------------------------------------------------------------------- #
# low-level OOXML builders
# --------------------------------------------------------------------------- #
def _clone_rpr(rpr):
    return copy.deepcopy(rpr) if rpr is not None else None


def _run(text: str, rpr):
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(_clone_rpr(rpr))
    t = OxmlElement("w:t")
    t.set(_XML_SPACE, "preserve")
    t.text = text
    r.append(t)
    return r


def _del(text: str, rpr, wid: int, author: str, date: str):
    d = OxmlElement("w:del")
    d.set(qn("w:id"), str(wid))
    d.set(qn("w:author"), author)
    d.set(qn("w:date"), date)
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(_clone_rpr(rpr))
    dt = OxmlElement("w:delText")
    dt.set(_XML_SPACE, "preserve")
    dt.text = text
    r.append(dt)
    d.append(r)
    return d


def _ins(text: str, rpr, wid: int, author: str, date: str):
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), str(wid))
    ins.set(qn("w:author"), author)
    ins.set(qn("w:date"), date)
    ins.append(_run(text, rpr))
    return ins


# --------------------------------------------------------------------------- #
# paragraph processing
# --------------------------------------------------------------------------- #
def _direct_runs(p):
    return p.findall(qn("w:r"))


def _paragraph_is_rebuildable(runs) -> bool:
    """True only if every run contains nothing but w:rPr and w:t (safe to rebuild)."""
    for r in runs:
        for child in r:
            if child.tag in _NON_TEXT_RUN_CHILDREN:
                return False
    return True


def _process_paragraph(p, terms: Dict[str, str], counter, author, date, report) -> None:
    runs = _direct_runs(p)
    if not runs:
        return

    # Reconstruct the paragraph's visible text and, for each character, remember
    # which run's rPr it came from (identity reference -> preserves formatting).
    chars: list[str] = []
    char_rpr: list = []
    for r in runs:
        rpr = r.find(qn("w:rPr"))
        for t in r.findall(qn("w:t")):
            for ch in (t.text or ""):
                chars.append(ch)
                char_rpr.append(rpr)
    full = "".join(chars)

    matches = list(TOKEN_RE.finditer(full))
    if not matches:
        return

    if not _paragraph_is_rebuildable(runs):
        # Don't risk dropping tabs/breaks/images; record and skip.
        for m in matches:
            report.setdefault("skipped", []).append(m.group(1))
        return

    new_nodes = []

    def emit_literal(a: int, b: int):
        """Emit text [a, b) as one or more runs, splitting on formatting changes."""
        i = a
        while i < b:
            j = i
            cur = char_rpr[i]
            while j < b and char_rpr[j] is cur:
                j += 1
            new_nodes.append(_run(full[i:j], cur))
            i = j

    pos = 0
    for m in matches:
        emit_literal(pos, m.start())
        name = m.group(1)
        raw = m.group(0)
        if name in terms:
            value = str(terms[name])
            rpr = char_rpr[m.start()]
            counter[0] += 1
            new_nodes.append(_del(raw, rpr, counter[0], author, date))
            counter[0] += 1
            new_nodes.append(_ins(value, rpr, counter[0], author, date))
            report.setdefault("applied", []).append({"token": name, "value": value})
        else:
            # Unknown token: leave the placeholder text in place, flag it.
            emit_literal(m.start(), m.end())
            report.setdefault("unmatched", []).append(name)
        pos = m.end()
    emit_literal(pos, len(full))

    # Splice: insert the new nodes where the first run was, then drop old runs.
    insert_at = list(p).index(runs[0])
    for node in new_nodes:
        p.insert(insert_at, node)
        insert_at += 1
    for r in runs:
        p.remove(r)


# --------------------------------------------------------------------------- #
# document traversal
# --------------------------------------------------------------------------- #
def _iter_paragraphs(document):
    """Yield every paragraph in body, tables (incl. nested), headers and footers."""
    def walk_tables(tables):
        for tbl in tables:
            for row in tbl.rows:
                for cell in row.cells:
                    yield from cell.paragraphs
                    yield from walk_tables(cell.tables)

    yield from document.paragraphs
    yield from walk_tables(document.tables)
    for section in document.sections:
        for hf in (section.header, section.footer,
                   section.first_page_header, section.first_page_footer,
                   section.even_page_header, section.even_page_footer):
            if hf is None:
                continue
            yield from hf.paragraphs
            yield from walk_tables(hf.tables)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def generate_redline(template_path: str,
                     terms: Dict[str, str],
                     output_path: str,
                     author: str = "Draftease",
                     date: str | None = None) -> dict:
    """
    Fill the placeholders in ``template_path`` with ``terms`` and write a
    tracked-changes .docx to ``output_path``.

    Returns a report dict: {"applied": [...], "unmatched": [...], "skipped": [...]}.
    """
    if date is None:
        date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    document = Document(template_path)
    counter = [0]                 # mutable tracked-change id counter
    report: dict = {"applied": [], "unmatched": [], "skipped": []}

    for p in _iter_paragraphs(document):
        _process_paragraph(p._p, terms, counter, author, date, report)

    document.save(output_path)
    _sanitize(output_path)
    return report


def _sanitize(path: str) -> None:
    """Repair a benign python-docx artifact: a <w:zoom/> with no w:percent fails
    strict OOXML validation. Rewrite settings.xml to drop the empty element."""
    import os
    import re as _re
    import shutil
    import zipfile

    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/settings.xml":
                txt = data.decode("utf-8")
                txt = _re.sub(r"<w:zoom(?![^>]*w:percent)[^>]*/>", "", txt)
                data = txt.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp, path)


def extract_tokens(template_path: str) -> list:
    """Return the ordered, unique list of {{token}} names found in a .docx."""
    document = Document(template_path)
    seen, out = set(), []
    for p in _iter_paragraphs(document):
        for m in TOKEN_RE.finditer(p.text or ""):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 4:
        print("usage: python redline_engine.py <template.docx> <terms.json> <output.docx>")
        raise SystemExit(1)

    with open(sys.argv[2]) as fh:
        deal_terms = json.load(fh)
    rep = generate_redline(sys.argv[1], deal_terms, sys.argv[3])
    print(f"Applied {len(rep['applied'])} tracked changes -> {sys.argv[3]}")
    if rep["unmatched"]:
        print("Unmatched tokens (no term supplied):", sorted(set(rep["unmatched"])))
    if rep["skipped"]:
        print("Skipped tokens (complex run):", sorted(set(rep["skipped"])))
