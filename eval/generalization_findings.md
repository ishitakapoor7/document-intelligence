# Evaluation findings

`python eval/run_ingest_eval.py` — full output in `eval/ingest_eval_output.txt`.

Two corpora, scored identically against hand-authored ground truth. Nothing here is
model-graded: every judgement is a comparison against `gold.yaml` /
`generalization_gold.yaml`. The one model-reported number in the report — vision
legibility — is labelled wherever it appears and never averaged with measured figures.

| | classification | scalar fields | line items | **document completeness** | hallucinations |
|---|---|---|---|---|---|
| **Controlled fixtures** | 3/3 | 15/15 | 4/4 | **3/3** | 0 |
| **Synthetic adversarial set** | 8/8 | 22/23 | 15/15 | **7/8** | 0 |

**On the naming.** What was previously called the "generalization set" is now the
**synthetic adversarial set**. It was written without reference to `docint/config.py` and
the schemas were not adjusted to fit it — but it was authored by the same person who wrote
the pipeline, and the difficulties in it were chosen by someone who knew where the system
was weak. That is adversarial, not general. No externally-authored document has been
tested yet.

**On "22/23".** This counts **schema-representable scalar fields** only — single-valued
fields the configured schema can hold. It is not document accuracy. Repeating data is
scored separately as line items, and documents the schema cannot represent at all are
scored as mismatches rather than quietly passing. **Document completeness** — documents
with nothing wrong, nothing missing and nothing unrepresentable — is the number to read.

---

## The recognition ladder now works, and it is worth what it costs

Paired runs, same bytes, same thresholds, the only difference being whether the vision
escalation was allowed to fire:

| document | tesseract | fallback off | fallback on | post | cost | latency |
|---|---|---|---|---|---|---|
| L1 clean | 95.1 | extracted | extracted (not triggered) | – | $0.025 | 6.6s |
| L2 medium | 62.4 | extracted | extracted | 94.0 | $0.062 | 12.5s |
| **L3 degraded** | 43.4 | **needs_review_ocr** | **extracted** | 88.0 | $0.062 | 13.6s |
| G2 stamped scan | 89.1 | extracted | extracted (not triggered) | – | $0.026 | 7.8s |
| **G4 amended scan** | 55.1 | **needs_review_missing_fields** | **extracted** | 88.0 | $0.062 | 13.6s |
| G6 thermal receipt | 33.3 | unknown_type | unknown_type | 78.0 | $0.021 | 7.7s |

Two documents move from a review queue to fully extracted for about **4 cents and 8
seconds each**. The escalation fires only below 75 Tesseract confidence, so clean
documents pay nothing.

**`post` is not comparable to `tesseract`.** Tesseract reports a per-word score from its
own classifier — a measurement. A vision model has no equivalent, so that column is the
model's self-reported legibility: model-graded, uncalibrated, and useful as a gate ("did
the second read go better?") rather than as a number.

**A consequence worth stating:** with the fallback on, nothing in this corpus now reaches
`needs_review_ocr`. The review floor is only touched when vision *also* fails, and no
document here defeats it. The floor is therefore untested, which is a gap, not a success.

---

## Failure — extraction applied an uncountersigned handwritten amendment

```
G4_po_amended_scan.pdf   committed_amount = 19400.00   expected 18750.00
```

**This is the most important result in the run, and it inverts an earlier claim.**

A previous version of this document stated that "handwriting was not mistaken for
authority". That was wrong, and the correction matters: with the fallback off, the
handwritten figure was simply *unreadable to Tesseract*. Absence of a wrong answer was
read as evidence of correct judgement. It was not.

With vision recognition, the handwriting becomes legible — and the failure appears.

**The failure is not in recognition.** The vision transcription is exactly right:

```
[HANDWRITTEN] 19,400.00  DO 6/3
Order Value:                                      EUR 18,750.00
[Note: printed "EUR 18,750.00" is struck through by hand]
```

It marked the handwriting, preserved the printed value, and even noted the strikethrough.
**The failure is in extraction**, one stage later: given both values and a visible
strikethrough, it decided the amendment superseded the printed figure and returned
19,400.00. The document itself says *"Amendments must be countersigned"*, and this one is
unsigned.

So this is a **reasoning error, not a reading error**, and the ladder localises it
precisely. It is also the confidently-wrong case previously recorded as missing from this
evaluation — it did reproduce, by a route not anticipated: **improving recognition created
it.** A system that cannot read handwriting cannot misapply it.

**Not fixed.** The fix is a policy statement in the extraction prompt — extract printed
values; never apply a handwritten amendment as authoritative — and it is genuinely missing
behaviour rather than a tuning knob. It is left undone because adding it now, against a
test already run, would forfeit the evidence. It should be added and then validated on a
document not used to discover it.

---

## What the receipt fix changed, and what it does not prove

Classification went 7/8 → **8/8**. The retail receipt that was previously labelled
`invoice` at 0.95 confidence is now correctly `unknown`.

The fix was not a threshold. A confidence gate cannot catch a model that is confidently
wrong. The taxonomy previously listed four labels without saying what distinguished them,
and `invoice` is a reasonable answer for anything with a vendor, a date and a total. It now
carries definitions — *an invoice requests payment still owed; a receipt evidences payment
already made* — and names the near-misses explicitly (receipts, delivery notes, packing
slips, quotations, statements, remittance advice, credit notes).

`receipt` was deliberately **not** added to the taxonomy. The system should be able to say
"not one of mine" about a document type it does not model.

**This does not yet prove the fix generalises.** It was validated on the document that
exposed it. A fresh holdout is required.

---

## Multi-record schema mismatch (G7)

A three-supplier workbook against a single-vendor schema. The pipeline returns **nothing**
and routes to `needs_review_missing_fields` with the classification preserved.

An earlier version of the gold file accepted "any one of the three suppliers" as a pass.
That was wrong, and it has been corrected: returning one arbitrary row from a three-row
sheet is fabrication — a consumer would believe the workbook describes a single supplier at
$124.00/unit. Declining is the correct outcome, and the stricter extraction instruction
("do not infer, derive or invent a value that is not printed") is what produced it.

The underlying limitation stands: a repeating-vendor schema, the same shape as
`line_items[]`, is the fix. Not built.

---

## line_items[] closed a silent data-loss bug

`quantity` and `unit_price` were scalar invoice fields. On a six-line invoice the pipeline
returned line 1 of 6, with nothing marking it partial — `quantity=120` on a $4,865 invoice.
This was **structurally invisible** on the controlled fixtures, where every document has
exactly one line item.

Repeating data now lives in `line_items[]`; scalars describe the document, not its rows.
15/15 line items extracted across the adversarial set, including the six-line invoice whose
table continues onto a second page.

---

## What this establishes, and what it does not

**Establishes:** varied layouts and vocabulary map onto fixed schemas (G3 never uses the
words "purchase order" and still scores 5/5; G1's "Our Ref"/"Bill Date"/"Your Order"/"Amount
Payable" all map); absent fields are not invented (G2 carries no PO reference and
`po_reference` is null); multi-record ambiguity is declined rather than guessed; recognition
escalation converts two review items into extractions for cents.

**Does not establish:** every document here is synthetic and authored by the pipeline's
author. Real scans carry artefacts absent from all of them — sheet-feeder skew, shadow
gradients, staple holes, photocopier banding, multi-column layouts, mixed languages. The
receipt classification fix is validated only on the document that exposed it. The
`needs_review_ocr` floor is untested now that vision recovers everything in the corpus.

**Open gaps, in priority order:**
1. Externally-authored holdout documents. Nothing here was written by anyone but me.
2. The G4 amendment policy — specified, not implemented, deliberately.
3. A document that defeats vision, to test the review floor.
4. A repeating-vendor schema for multi-record workbooks.
