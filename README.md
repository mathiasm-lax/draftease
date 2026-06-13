# Draftease — Redline Engine (no-AI core)

This is the deterministic heart of Draftease: it turns a **form lease + deal terms**
into a **native Word tracked-changes redline**, with no AI and no network calls.
The lease template and the redline never leave the machine it runs on — exactly the
privacy property the product is built around.

## What it does

1. Takes a form lease `.docx` containing placeholder tokens like `{{base_rent_psf}}`.
2. Takes a dict of deal terms (the values an LOI would supply).
3. Replaces every placeholder with its value **as a tracked change** — a Word
   deletion (`w:del`) of the placeholder followed by an insertion (`w:ins`) of the
   value — preserving the surrounding text's formatting.
4. Saves a `.docx` that opens in Microsoft Word as a real redline you can
   **Accept** or **Reject**.

## Files

| File | Purpose |
|------|---------|
| `redline_engine.py` | The engine. Importable: `generate_redline(template, terms, output)`. Also runnable as a CLI. |
| `make_sample_lease.py` | Generates a realistic sample form lease with tokens (stands in for a landlord's uploaded form). |
| `deal_terms.json` | Sample LOI-extracted deal terms (token → value). |
| `verify_redline.py` | End-to-end test: builds the sample, runs the engine, asserts the tracked changes are correct. |
| `sample_form_lease.docx` | Generated form lease (the input). |
| `redline_350park_lockton.docx` | **The output redline** — open this in Word to see tracked changes. |
| `accepted_clean.docx` | The same redline with all changes accepted (proof the result is a correct, clean lease). |

## Run it

```bash
pip install python-docx lxml

# generate the sample form lease, then produce a redline from the sample terms
python make_sample_lease.py
python redline_engine.py sample_form_lease.docx deal_terms.json my_redline.docx

# or run the full self-check
python verify_redline.py
```

## How it stays private (and deterministic)

* **No model, no API.** Pure OOXML manipulation via `python-docx` / `lxml`.
* The only step in the full product that would use a model is reading a *free-form*
  LOI to populate `deal_terms` — and that runs in your own cloud/tenant, never
  touching this engine or the lease.
* Because it's deterministic, the same inputs always yield the same redline — which
  is what makes it auditable and safe for legal documents.

## Current scope & honest limitations

* **Token model.** The form lease must mark replaceable spots with `{{tokens}}`. In
  the product, the self-serve onboarding step is where a user tags their uploaded
  form (this is the one place an AI *assist* helps — one-time, not per-redline).
* **Single-value substitution.** It fills placeholders. It does not yet handle
  conditional clauses (include/exclude a section based on a term) or repeating
  blocks — natural next features.
* **Run rebuild guard.** Paragraphs whose tokenised text also contains tabs/line
  breaks/images are skipped (and reported) rather than risk dropping those elements.
* **Author/date** are set on every change (default author "Draftease") so reviews
  show provenance.

## Suggested next steps

1. Conditional clauses & optional sections driven by deal terms.
2. A small "tagging" UI to turn an uploaded form into a tokenised template.
3. Wire `deal_terms` to the (private, in-tenant) LOI extraction step.
4. A human review gate before the redline is finalized — always.
