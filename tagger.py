"""
Deterministic tagging assistant for Draftease.

Takes a *clean* lease (.docx with no {{tokens}}), best-effort detects standard
lease terms, and inserts {{tokens}} where the user confirms — turning a plain
lease into a redline-ready template. No AI, no network.

Public API:
    STANDARD_TOKENS              -> ordered [(key, label), ...]
    autodetect(path)             -> {token_key: detected_phrase}
    apply_tags(in_path, mapping, out_path) -> count of tokens inserted
"""
import re

from docx import Document
from docx.oxml.ns import qn

from redline_engine import _iter_paragraphs, _run, _sanitize

# Standard lease-term token library
STANDARD_TOKENS = [
    ("landlord", "Landlord"),
    ("tenant", "Tenant"),
    ("guarantor", "Guarantor"),
    ("premises", "Premises / address"),
    ("base_rent", "Base rent"),
    ("rentable_sf", "Rentable square feet"),
    ("lease_term", "Lease term"),
    ("commencement_date", "Commencement date"),
    ("expiration_date", "Expiration date"),
    ("free_rent", "Free rent / abatement"),
    ("security_deposit", "Security deposit"),
    ("ti_allowance", "TI allowance"),
    ("renewal_option", "Renewal option"),
    ("permitted_use", "Permitted use"),
]

_MONTH = ("(?:January|February|March|April|May|June|July|August|September|"
          "October|November|December)\\s+\\d{1,2},?\\s+\\d{4}")

# best-effort detection patterns; group(1) is the phrase to tokenize
_PATTERNS = {
    "tenant": [r'(?:between|and|,|by)\s+([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?Tenant',
               r'([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?Tenant'],
    "landlord": [r'(?:between|and|,|by)\s+([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?(?:Landlord|Lessor)',
                 r'([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?(?:Landlord|Lessor)'],
    "guarantor": [r'(?:between|and|,|by)\s+([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?Guarantor',
                  r'([A-Z][A-Za-z0-9 .,&\'-]{2,45}?)\s*\(\s*["“]?Guarantor'],
    "base_rent": [r'(\$[\d,]+(?:\.\d{2})?)\s*per\s+(?:rentable\s+)?square\s+foot',
                  r'Base Rent[^$]{0,40}?(\$[\d,]+(?:\.\d{2})?)'],
    "rentable_sf": [r'([\d]{1,3}(?:,\d{3})+|\d{3,})\s+rentable\s+square\s+feet'],
    "lease_term": [r'term\s+of\s+([A-Za-z0-9\-]+\s+(?:months|years))',
                   r'(\d+)\s*[- ]\s*month\s+term'],
    "commencement_date": [r'commenc\w*\s+(?:on\s+)?(' + _MONTH + r')'],
    "expiration_date": [r'expir\w*\s+(?:on\s+)?(' + _MONTH + r')',
                        r'ending\s+(?:on\s+)?(' + _MONTH + r')'],
    "security_deposit": [r'[Ss]ecurity [Dd]eposit[^$]{0,40}?(\$[\d,]+(?:\.\d{2})?)'],
    "ti_allowance": [r'(?:improvement allowance|tenant improvement allowance)[^$]{0,40}?(\$[\d,]+(?:\.\d{2})?)'],
    "free_rent": [r'([A-Za-z]+\s*\(\d+\)|\d+)\s+months?\s+of\s+(?:abated|free)'],
    "premises": [r'located at\s+([0-9][^,\n]{3,50},[^,\n]{2,40},\s*[A-Za-z .]+\s*\d{5})'],
    "permitted_use": [r'used\s+(?:solely\s+)?for\s+([a-z][^.;\n]{5,80}?)(?:\s+and\s+for\s+no|[.;])'],
    "renewal_option": [r'(option to renew[^.;\n]{0,80})'],
}


def autodetect(path: str) -> dict:
    doc = Document(path)
    text = "\n".join(p.text for p in _iter_paragraphs(doc))
    out = {}
    for key, pats in _PATTERNS.items():
        for pat in pats:
            m = re.search(pat, text)
            if m:
                out[key] = m.group(1).strip().rstrip(",")
                break
    return out


def apply_tags(in_path: str, mapping: dict, out_path: str) -> int:
    """Replace each confirmed phrase with its {{token}} in the .docx."""
    reps = sorted([(v.strip(), k) for k, v in mapping.items() if v and v.strip()],
                  key=lambda x: -len(x[0]))
    if not reps:
        Document(in_path).save(out_path)
        _sanitize(out_path)
        return 0

    doc = Document(in_path)
    count = 0
    skip = {qn("w:tab"), qn("w:br"), qn("w:drawing"), qn("w:cr"), qn("w:pict")}
    for p in _iter_paragraphs(doc):
        runs = p._p.findall(qn("w:r"))
        if not runs:
            continue
        chars, crpr = [], []
        for r in runs:
            rpr = r.find(qn("w:rPr"))
            for t in r.findall(qn("w:t")):
                for ch in (t.text or ""):
                    chars.append(ch); crpr.append(rpr)
        full = "".join(chars)
        used = [False] * len(full)
        marks = []
        for phrase, token in reps:
            start = 0
            while True:
                j = full.find(phrase, start)
                if j < 0:
                    break
                if not any(used[j:j + len(phrase)]):
                    marks.append((j, j + len(phrase), token))
                    for x in range(j, j + len(phrase)):
                        used[x] = True
                start = j + len(phrase)
        if not marks:
            continue
        if any(child.tag in skip for r in runs for child in r):
            continue
        marks.sort()
        new = []

        def emit(a, b):
            i = a
            while i < b:
                j = i
                cur = crpr[i]
                while j < b and crpr[j] is cur:
                    j += 1
                new.append(_run(full[i:j], cur))
                i = j

        pos = 0
        for s, e, token in marks:
            emit(pos, s)
            new.append(_run("{{" + token + "}}", crpr[s] if s < len(crpr) else None))
            count += 1
            pos = e
        emit(pos, len(full))
        at = list(p._p).index(runs[0])
        for n in new:
            p._p.insert(at, n); at += 1
        for r in runs:
            p._p.remove(r)

    doc.save(out_path)
    _sanitize(out_path)
    return count
