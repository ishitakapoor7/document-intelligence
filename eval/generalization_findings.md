# Synthetic adversarial set

Eight documents written without reference to `docint/config.py`, with the schemas left
unchanged to fit them. Not a generalisation set: the same person wrote the pipeline, so
the difficulties in it were chosen by someone who knew where the system was weak.
Externally-authored documents are in `eval/holdout_findings.md`.

Current scores are in `eval/ingest_eval_output.txt`; the set now runs 8/8
classification, 23/23 scalars, 15/15 line items, 8/8 document completeness, 0
hallucinations.

## Recognition escalation earns its cost

Paired runs, same bytes, same thresholds, the only variable being whether the vision
escalation was allowed to fire:

| document | tesseract | fallback off | fallback on | post | cost |
|---|---|---|---|---|---|
| L1 clean | 95.1 | extracted | not triggered | – | $0.025 |
| L2 medium | 62.4 | extracted | extracted | 94.0 | $0.062 |
| **L3 degraded** | 43.4 | **needs_review_ocr** | **extracted** | 88.0 | $0.062 |
| G2 stamped scan | 89.1 | extracted | not triggered | – | $0.026 |
| **G4 amended scan** | 55.1 | **needs_review** | **extracted** | 88.0 | $0.062 |
| G6 thermal receipt | 33.3 | unknown_type | unknown_type | 78.0 | $0.021 |

Two documents move from a review queue to fully extracted for about 4 cents each, and
clean documents pay nothing.

`post` is the model's self-reported legibility, not a measurement, and is not
comparable to the Tesseract column.

## Improving recognition created a failure

With the fallback off, G4's handwritten amendment was simply unreadable, and the
absence of a wrong answer looked like good judgement. It was not: with vision the
handwriting becomes legible and extraction has to decide what it means.

The transcription is correct — it marks the handwriting, preserves the printed value
and notes the strikethrough. The decision sits one stage later, in extraction, which
is where the amendment policy now lives: a struck value is retired and the surviving
value beside it is the answer. G4 returns 19,400.00 on 5/5 runs.

A system that cannot read handwriting cannot misapply it. The ladder localises the
error to the right stage.

## Classification: definitions, not thresholds

A retail receipt was previously labelled `invoice` at 0.95 confidence. A confidence
gate cannot catch a model that is confidently wrong; the taxonomy listed four labels
without saying what distinguished them, and `invoice` is a reasonable answer for
anything with a vendor, a date and a total.

The types now carry definitions — an invoice requests payment still owed, a receipt
evidences payment already made — and name the near-misses explicitly. `receipt` was
deliberately not added: the system should be able to say "not one of mine".

Validated on `gkdb0226`, a real 2005 invoice that played no part in the fix.

## Multi-record workbooks (G7)

A three-supplier workbook against a single-vendor schema returns nothing and routes to
review with the classification preserved. Returning one arbitrary row is fabrication —
a consumer would believe the sheet describes one supplier at $124.00/unit.

This behaviour was assumed before it was instructed, and the assumption failed
silently. See `eval/holdout_findings.md` §4. A repeating-vendor schema, the same shape
as `line_items[]`, is the real fix; not built.

## line_items[] closed a silent data-loss bug

`quantity` and `unit_price` were scalar invoice fields, so a six-line invoice returned
line 1 of 6 with nothing marking it partial. Structurally invisible on the controlled
fixtures, where every document has exactly one line.

## What this set shows, and what it does not

**Shows:** varied layouts and vocabulary map onto fixed schemas — G3 never uses the
words "purchase order" and still scores 5/5; G1's "Our Ref" / "Bill Date" / "Amount
Payable" all map. Absent fields are not invented. Multi-record ambiguity is declined.

**Does not:** every document here is synthetic and written by the pipeline's author.
Real scans carry artefacts none of them have — feeder skew, shadow gradients, staple
holes, photocopier banding, mixed languages.
