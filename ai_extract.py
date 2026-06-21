"""
Optional AI layer: read a signed LOI with Claude on AWS Bedrock and extract the
standard deal terms. Runs in YOUR AWS tenant; the redline itself stays deterministic.

Enabled only when DRAFTEASE_BEDROCK_MODEL is set (plus AWS credentials in the
environment, which boto3 picks up automatically). When not configured, the app
falls back to manual term entry.
"""
import json
import os
import re

BEDROCK_MODEL = os.environ.get("DRAFTEASE_BEDROCK_MODEL", "")
BEDROCK_REGION = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
ENABLED = bool(BEDROCK_MODEL)

# the standard term keys (mirror tagger.STANDARD_TOKENS)
KEYS = ["landlord", "tenant", "guarantor", "premises", "base_rent", "rentable_sf",
        "lease_term", "commencement_date", "expiration_date", "free_rent",
        "security_deposit", "ti_allowance", "renewal_option", "permitted_use"]

PROMPT = (
    "You are extracting the business terms from a commercial real estate Letter of Intent (LOI). "
    "Return ONLY a single JSON object (no markdown fences, no commentary) with exactly these keys: "
    + ", ".join(KEYS) + ". "
    "For each key, give the value as a short string exactly as it would read in a lease "
    "(for example base_rent: \"$72.50 per rentable square foot per annum\"; lease_term: \"87 months\"; "
    "free_rent: \"four (4)\"; commencement_date: \"September 1, 2026\"). "
    "If a term is not stated in the LOI, use an empty string. Do not invent values."
)


def _fmt(filename: str) -> str:
    fn = (filename or "").lower()
    for ext in ("pdf", "docx", "doc", "txt", "html", "md", "csv"):
        if fn.endswith("." + ext):
            return ext
    return "pdf"


def extract_terms(file_bytes: bytes, filename: str) -> dict:
    """Send the LOI document to Claude on Bedrock; return {standard_key: value}."""
    import boto3  # imported lazily so the app runs without AWS configured

    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    resp = client.converse(
        modelId=BEDROCK_MODEL,
        messages=[{"role": "user", "content": [
            {"document": {"name": "LOI", "format": _fmt(filename),
                          "source": {"bytes": file_bytes}}},
            {"text": PROMPT},
        ]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0},
    )
    parts = resp.get("output", {}).get("message", {}).get("content", [])
    text = "".join(p.get("text", "") for p in parts)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    data = json.loads(m.group(0) if m else text)
    return {k: str(data.get(k) or "").strip() for k in KEYS}
