# External holdouts: what three real documents showed that eleven synthetic ones could not

Three documents from the UCSF Industry Documents Library, chosen and downloaded by
someone other than the author of this pipeline, read by hand, gold written before the
pipeline was ever pointed at them (`eval/holdout_gold.yaml`, `eval/holdouts/PROVENANCE.txt`).

Every other document in this evaluation — the three controlled fixtures and the eight
synthetic adversarial ones — was written by the same person who wrote the schemas.
That is the gap these three exist to close, and they closed it hard.

---

## 1. The headline: an inherited OCR text layer walked straight past every trust mechanism

**All three holdouts are scans.** Every page of all eleven pages is a full-page raster
image. Each also carries an OCR text layer produced by the archive years ago.

The pipeline decided whether a page was a scan by asking how much text it could pull
out (`MIN_TEXT_LAYER_CHARS = 100`). All eleven pages had more than 100 characters, so
all three documents were labelled **`pdf_digital`**, and as a direct consequence:

- Tesseract never ran
- no OCR confidence was ever measured (`ocr_mean_confidence = None`)
- the review gate could not fire — it is written `if effective is not None and effective < MIN_OCR_CONFIDENCE`
- the vision fallback was unreachable — it is gated on `tess is not None`
- fields extracted from that text were rendered to a reviewer as `digital`, i.e. as
  having come from a real text layer

`gkdb0226.pdf` was therefore reported as `status=extracted`, fully clean, no flags —
with the vendor name **wrong**. The letterhead reads *Arista Laboratories*. The
archive's OCR read it as *Uristo*, and the pipeline faithfully extracted, asserted and
would have cited that.

This is the **"confidently wrong OCR"** case that `eval/ocr_ladder.md` named as the
single most important missing test in this evaluation, that did not reproduce anywhere
on the synthetic corpus, and that I declined to manufacture on the grounds that a
synthetic example would only prove a degradation script can be tuned until a threshold
is crossed. It reproduced on the first real document, without being asked to.

The synthetic corpus could not have found this. Its scans were rendered by
`scripts/build_corpus.py` with no text layer, so they took the OCR path by
construction. The blind spot was in the *decision* about which path to take, and every
synthetic document agreed with that decision.

### The fix

`MIN_TEXT_LAYER_CHARS` asks about the text. The right question is about the page:

> A page whose area is essentially covered by a raster image is a scan, whatever text
> rides along with it. (`RASTER_PAGE_COVERAGE = 0.80`)

That is a structural property, not a guess about text content, so it holds for
documents nobody here has seen. The inherited layer is **discarded** rather than
merged: a value this system asserts should be one whose reading it measured.

**No regression.** Re-running the eleven earlier documents: every born-digital file is
still `pdf_digital`, every scan keeps its previous confidence (95.1 / 89.1 / 88.0 /
78.0), and the one known failure (G4) is unchanged. The change touches only
rasters-carrying-text-layers, which is precisely the population it was written for.

### The fix made recognition honest and extraction *worse*

| `gkdb0226.pdf` | before | after |
|---|---|---|
| file_type | `pdf_digital` | `pdf_scanned` |
| measured confidence | none | **90.1** |
| status | `extracted` | `needs_review_missing_fields` |
| invoice_number | 1179 ✓ | **missing** |
| vendor_name | "Uristo Laboratories" ✗ | "Marista Laboratories" ✗ |
| invoice_date | 2005-01-31 ✓ | **missing** |
| po_reference | null ✓ | **PM3006879334** ✗ (a Bates number) |
| total_amount | 133,337.50 ✓ | 133,337.50 ✓ |
| scalars correct | **4/5** | **1/5** |

Our Tesseract is *worse than the archive's OCR on this document*. It lost the invoice
number and date — both sit in a small boxed table at the top right that it mangled —
and it read the stylised logo as "Marista" where the archive read "Uristo". Neither is
"Arista".

I am keeping the fix, and the reason is not the accuracy column:

- Before, the system **silently asserted a wrong vendor name at full confidence**.
  After, it says `needs_review_missing_fields` and names what it could not read. For
  an accounts-payable workflow a flagged document is not a worse outcome than a
  confidently wrong one; it is the entire point of the product.
- The inherited layer is **unmeasurable**. A system whose central claim is calibrated
  trust cannot rest that claim on text of unknown provenance and unknown quality.

But the accuracy cost is real, it is measured on **exactly one document**, and I do not
know its general direction. That is an open question, not a resolved one.

### A third option, considered and rejected — and the better idea behind it

Use the inherited layer for extraction, attach our own measured confidence to it. It
would have got the best column of both: the archive read "1179" and the date
correctly, ours did not.

Rejected, because the confidence would then describe a *different transcription* from
the text it is attached to. A number that does not describe the thing it is printed
next to is worse than no number.

The better version of the same instinct, and **the strongest future item in this
evaluation**: run both transcriptions and treat *disagreement between them* as the
signal. Two independent OCR passes differing on a field is strong evidence that the
field is unreliable, and it requires no ground truth to compute. Here it would have
flagged the vendor name — "Uristo" vs "Marista" — which is the field both got wrong
and neither doubted.

---

## 2. Tesseract reported 90.1 on a document it had materially misread

`eval/ocr_ladder.md` recorded, from the synthetic ladder, that *on this corpus OCR
confidence tracked correctness* — there was no observed band where confidence was high
and a value silently wrong.

`gkdb0226.pdf` scores **90.1**, above both thresholds, with two required fields lost
and the vendor name wrong. The finding does not survive contact with a real 2005 scan.

The original note was honest about its scope ("on this corpus") and I am not treating
it as having been wrong, but the generalisation it invited is now falsified, and the
ladder's thresholds are calibrated against a corpus that does not contain this failure
mode.

---

## 3. A document-level confidence mean hides the pages that need help

`lnml0028.pdf` is seven pages. Its mean is **80.0** — above the vision-fallback
threshold, so nothing escalates. Per page:

| p1 | p2 | p3 | p4 | p5 | p6 | p7 |
|---|---|---|---|---|---|---|
| 87.2 | 82.0 | 88.6 | 85.1 | 87.1 | **58.8** | **71.7** |

Pages 6 and 7 are both below the escalation threshold and neither can reach it,
because the ladder gates on the document mean. The two worst pages are the two cheque
stubs — the pages carrying payment amounts.

The fix is to escalate per page rather than per document. It is a small change and I
have not made it: it is a behavioural change to the recognition ladder discovered at
the end of the day, and shipping it untested would be worse than recording it
precisely. `lmcj0190.pdf` shows the same shape in the other direction — mean 47.0
across pages of 31.9 / 73.3 / 35.8.

---

## 4. Two of the three holdouts are not one document

This is the architectural finding, and it is the one I would raise first in a design
review.

`lnml0028.pdf` is a seven-page accounts-payable packet holding **three unrelated
payment matters**:

| page | content |
|---|---|
| 1 | check request, $4,667.00, to Cigar Association of America |
| 2 | covering letter |
| 3 | **a genuine invoice**, CAA, 1996-05-29, $4,667 (of a $14,000 event) |
| 4 | a *different* check request, $1,000.00, to Food Industry Association Executives |
| 5 | the solicitation letter behind that one |
| 6 | cheque stub, $1,000.00, no. 023001 |
| 7 | cheque stub, $657.75, no. 023774, to a third party entirely |

`lmcj0190.pdf` is three pages: a scrawled cover sheet, an RJR *Returned Goods
Authorization Form*, and an Overnite Transportation freight bill.

The whole data model assumes **one file = one document = one record**. `document_id`
is the hash of the file; `document_type` is one label; the field set holds one record.
There is no representation for "this PDF contains an invoice, two payment
authorisations and two cheques for unrelated matters". This is `G7`'s multi-record
problem — a sheet holding three suppliers — one level up, and it is *far* more common
in real archives than in anything I would have thought to synthesise.

What the system did with them is better than the architecture deserves:

- `lmcj0190` → `unknown`, extraction skipped, all fields null. Correct, and the
  freight bill's "TOTAL CHARGES (USD)" column did not lure it into `invoice`.
- `lnml0028` → `invoice`, and it found page 3's vendor, date and total **correctly**,
  from among four competing amounts in the same file ($14,000, $4,667, $1,000,
  $657.75). It then correctly flagged `needs_review_missing_fields`, because that
  invoice genuinely has no invoice number.

Gold accepted either `invoice` or `unknown` for `lnml0028` on the grounds that neither
is right — the taxonomy cannot say "bundle" — with the document-level verdict carried
by a schema-mismatch row instead. That was written before the run, not after seeing
the answer.

---

## 5. Line items: a definitional gap, not an error

`gkdb0226` returned **16** line items where gold says 14. The two extra are the
`Subtotal` rows (36,700.00 and 91,750.00), captured with no quantity and no unit
price. They are legitimately rows of the table and they are not billable lines —
summing all sixteen double-counts the invoice.

`LineItem` has no way to say "this row is a subtotal", so there is no way for a
consumer to tell. The narrow fix is a row-kind discriminator; gold's definition
("subtotal rows and the bare condition line are not line items") is currently stated
in a comment that the schema cannot enforce.

One row also lost its description: the MASS surcharge came back described as `5.00%`,
and the "MASS — 45 mL / 2 sec, 30 sec, 50% vents blocked" descriptor was attached
instead to the six qty-50 assay rows above it, which it does not belong to.

---

## Scoreboard

Complete, **before** the parse fix:

| | classification | scalars | line items | completeness | hallucinations |
|---|---|---|---|---|---|
| Controlled (3 docs) | 3/3 | 15/15 | 4/4 | 3/3 | 0 |
| Synthetic adversarial (8 docs) | 8/8 | 22/23 | 15/15 | 7/8 | 0 |
| **External holdout (3 docs)** | **3/3** | **12/13** | **0/1** | **1/3** | **0** |

Classification held up on genuinely foreign documents, including a correct `unknown`
on a freight-bill packet designed by nobody to fool anything, and no field was ever
invented. Document completeness — the honest number — is 1 in 3.

**Incomplete, after the parse fix.** The run stopped partway through: the Anthropic
account ran out of credit during `lmcj0190`'s vision escalation. What is measured:

- `gkdb0226` full result, in the table above
- recognition for all three (no model calls needed): `pdf_scanned` for all, at 90.1,
  47.0 and 80.0
- the eleven earlier documents, re-run with no regression

What is **not** measured: classification and extraction for `lmcj0190` and `lnml0028`
after the fix. `lmcj0190` at mean 47.0 should now escalate to vision and then, if that
does not lift it above 55.0, land in `needs_review_ocr` — that would be the first time
the review floor has been exercised by a document that genuinely deserves it rather
than by a synthetically degraded one. Untested. Rerun with `make eval-ingest`.

---

## What three documents can and cannot establish

**Can:** that a design decision every one of eleven synthetic documents agreed with was
wrong, and wrong in the direction that silently produces confident false values — the
exact failure this project claims to prevent. One real document found it in one run.

**Cannot:** anything with a rate attached. Three documents, one domain, one archive,
one era of scanning technology. The scoreboard rows above are counts.

The lesson is not about tobacco-industry paperwork. It is that a corpus authored by
the same person as the schemas will agree with the schemas' assumptions, including the
assumptions nobody wrote down — and that the cheapest, highest-yield thing available to
this project was three documents somebody else picked.
