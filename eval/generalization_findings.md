# Generalization findings

Run: `python eval/run_ingest_eval.py` (full output in `eval/ingest_eval_output.txt`).

Two corpora, scored identically against hand-authored ground truth. No model grades
anything here - every judgement is a comparison against `gold.yaml` /
`generalization_gold.yaml`.

| | classification | field extraction | hallucinations | schema mismatches |
|---|---|---|---|---|
| **Controlled fixtures** (written alongside `FIELD_SPECS`) | 3/3 | 17/17 | 0 | 0 |
| **Generalization set** (written independently, schemas unchanged) | **7/8** | **23/25** | **0** | 4 |

The generalization set was authored without reference to `docint/config.py`, and the
configured schemas were **not** adjusted afterwards to fit it. `scripts/build_generalization_set.py`
does not import `docint`.

---

## What held up

**Alternate labels are not a problem.** G1 uses "Our Ref" for the invoice number, "Bill
Date" for the date, "Your Order" for the PO reference and "Amount Payable" for the total.
All four mapped correctly. G3 never uses the phrase "purchase order" at all - it is a
BLANKET PURCHASE AGREEMENT with a "Maximum Commitment" instead of a total - and still
classified and extracted 5/5. This is the clearest evidence that extraction is reading the
documents rather than pattern-matching the generator: there is no template, no coordinate
map and no per-field regex anywhere in the pipeline.

**Long-form dates normalise.** "11 April 2026" -> `2026-04-11`.

**Multi-page works.** G1's line-item table continues overleaf and the totals block sits on
page 2; the total was extracted correctly.

**No hallucination on the absent-field test.** G2 carries no purchase-order reference of
any kind. `po_reference` came back `None`. This is the single most important positive
result in the run: extracting a plausible-looking PO number there would have been worse
than missing it, because a wrong value is indistinguishable from a right one downstream.

**Handwriting was not mistaken for authority.** G4 has a handwritten amendment striking
through the printed order value and writing `19,400.00` above it. The system did not report
19,400.00. It reported nothing - which is the safe failure.

**Cross-row consistency held.** G7 is a three-supplier sheet where the single-vendor schema
is underdetermined. All five extracted fields came from the same row (one chunk). Mixing
attributes across suppliers would have been the serious failure, and it did not happen.

---

## Failure 1 - a receipt was confidently classified as an invoice

```
G5_receipt_office_supply.pdf   got=invoice  expected=unknown  confidence=0.95
```

**The most significant finding in this run.** A retail receipt is invoice-adjacent - vendor,
date, line items, total - but it is not one of the three configured types. In an accounts
payable context the distinction is material: an invoice is a request for payment, a receipt
is evidence of a completed one. Classifying the latter as the former queues a paid
transaction for payment.

**Why the existing guard did not catch it.** The `unknown` route triggers on *low*
confidence. This misclassification arrived at **0.95**. The design assumed
miscategorisation surfaces as uncertainty; here the model was confidently wrong, and a
confidence threshold cannot catch that. The lexical prior did not help either - a receipt
genuinely contains invoice-ish vocabulary.

This is the classification analogue of the confidently-wrong-OCR case recorded in
`ocr_ladder.md`, and it did reproduce. A confidence gate is not a correctness gate.

**Two candidate responses, neither applied yet:**

1. *Add `receipt` to the taxonomy.* This is the PRD's stated extension path - a taxonomy
   entry plus a field list, no pipeline code - and it would double as a demonstration that
   the extension point is real. It does not fix the general problem: the next unmodelled
   near-miss type fails the same way.
2. *Sharpen the type definitions in the classification prompt* so `invoice` explicitly
   excludes proof-of-payment documents. Cheaper, and addresses the specific confusion
   rather than the class of it.

Neither is applied, because either would be tuning against a test already run. Recorded as
a known failure instead.

## Failure 2 - two fields missing on a degraded, annotated scan

```
G4_po_amended_scan.pdf   ocr=55.1   vendor_name=None   committed_amount=None
```

Both are **misses, not errors** - no wrong value was asserted. The amount is the field the
handwritten amendment sits on top of, and the vendor name is in the most degraded region of
the header.

Worth flagging: **55.1 is barely above `MIN_OCR_CONFIDENCE = 55.0`.** This document sits
0.1 points clear of the review gate, so it was extracted from rather than escalated - and
then silently returned nothing for two of five fields. A document that scrapes past the
threshold and yields partial data is arguably a worse outcome than one that fails it
cleanly, and this is the first evidence that a single mean-confidence gate is too blunt.
A per-field confidence check, or a "too many fields missing" rule, would catch it. Not
built; recorded.

## Failure 3 (not an error) - the schema does not fit multi-line documents

Four `schema_mismatch` entries, all the same shape. `FIELD_SPECS` gives an invoice one
`quantity` and one `unit_price`. G1 has six line items, G2 has three.

The pipeline does not error here - it silently returns the **first** line's values:

```
G1   quantity 120.0   unit_price 18.40     (line 1 of 6)
G2   quantity 12.0    unit_price None      (line 1 of 3)
```

Nothing marks these as partial. A downstream consumer reading `quantity=120` for G1 would
be wrong about a $4,865 invoice. The behaviour is also inconsistent - G2 returned a quantity
but not a unit price from the same document.

This is a **schema design limitation, not an extraction bug**, and it is exactly the kind of
thing that only surfaces against documents you did not write. The controlled fixtures each
have a single line item, so the mismatch was structurally invisible there. The fix is a
line-items array in the schema; the reason it is recorded rather than done is that changing
the schema in response to these documents is the thing this exercise exists to avoid.

---

## What this does and does not establish

**Does:** the pipeline maps genuinely varied layouts and vocabulary onto fixed schemas;
it declines to invent absent values; it does not treat handwriting as authoritative; it
keeps multi-record extraction internally consistent.

**Does not:** these documents are still synthetic and still written by the same author as
the pipeline. Alternate labels and layouts were chosen by someone who knew what the system
would find difficult, which is not the same as documents chosen by the world. Real scans
carry artefacts absent here - skew from a sheet feeder, shadow gradients, staple holes,
photocopier banding, multi-column layouts, rotated pages, mixed languages. The honest claim
is that the system is not merely recovering schemas from documents generated out of those
schemas; it is not that it is production-ready on real enterprise documents.

**The gap that matters most** remains a document where recognition is confident and wrong -
see `ocr_ladder.md`. Failure 1 shows the classification analogue of that gap is real.
