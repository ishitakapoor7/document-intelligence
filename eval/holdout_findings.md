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

## 6. A behaviour I had credited to the design turned out to belong to the model

Completing the holdout run re-ran the synthetic set, and `G7` — a sheet listing three
suppliers against a one-supplier schema — came back with **three invented values** at
`status=extracted`: vendor_id `SUP-0098`, vendor_name `Harlow Components GmbH`, rate
`124.00`. It had previously returned nulls, and that result is committed in
`eval/ingest_eval_output.txt`.

`docint/understand.py` had not changed between those runs. Measured 5 times: it picked
row 1 on **5 of 5**.

The uncomfortable part is what `generalization_gold.yaml` said about the old
behaviour, in my own words:

> "The right outcome is what the pipeline now does: decline to pick, leave the
> required fields empty, and route to human review."

Nothing in the extraction prompt ever asked the model to decline. I observed a
behaviour once and wrote it into gold as though it were a property of the system.
It was a property of the model on that day, and when it changed, the failure was
exactly the one this project exists to prevent: three fabricated values, asserted,
unflagged.

The prompt now states the policy — several records *of the type being extracted*
means no scalar has a single correct value, so return null rather than choosing —
and the gold comment has been corrected to say so. Declining is now **8/8 measured**
rather than assumed.

### The same policy, written bluntly, refused a document it could read

The first wording counted money-bearing paperwork rather than instances of the type
being extracted, and `lnml0028` — a packet containing exactly one invoice among
cheques and payment requests — went from extracting that invoice correctly to
returning nothing, 3/3. Sharpened to "count only records that would themselves be
classified as an invoice; if exactly one is present, extract it", both separate
cleanly at 3/3: G7 declines, `lnml0028` returns $4,667.00 out of a file containing
four different amounts.

### The methodological lesson, which I learned three times today

I validated that first wording with **one run** of each regression document, saw
`lnml0028` extract correctly, and moved on. The next full run showed it declining
3/3. That is the third time in this project a conclusion rested on a single sample:
the verifier appeared to catch a planted claim (it caught it 3/10), G7 appeared to
decline by design (it was one sample), and this. The eval harness exists precisely
because single observations of a non-deterministic system are not evidence, and I
kept spending them anyway when I was in a hurry.

**Still unstable, and reported as such:** `G4` — the purchase order whose printed
EUR 18,750.00 is overwritten by a handwritten 19,400.00 — declined 3/3 during the
probe above and asserted **19,400.00** in the final run. It flips. The right fix is
an amendment policy in the prompt (an uncountersigned handwritten change is not an
authority), which remains specified and unimplemented; what is *not* true is that
anything done today fixed it.

---

## 7. The review floor was finally earned by a real document

`lmcj0190.pdf` reads at **47.0** under Tesseract, escalates to vision, and comes back
at **51.7** — still below `MIN_OCR_CONFIDENCE`. It is now `needs_review_ocr`: a
document the system tried twice to read, could not, and says so.

Every previous exercise of that floor was a PDF this project degraded on purpose. It
is the first time the bottom rung has been reached by a document that simply is that
bad, and the answer — vision fallback fires, fails to clear the bar, and the system
stops — is the ladder behaving exactly as designed.

Reaching it required one more fix. The status was previously `unknown_type`, because
the `unknown` short-circuit ran before the quality gate: true, but it sent a reviewer
looking for a missing document type when the actionable fact was that the scan is
barely legible. "We cannot identify this document" is a conclusion drawn *from* the
text and is not available when the text cannot be read, so the gate now runs first
and names the earlier cause.

---

## Scoreboard

Final, complete, after every change described above:

| | classification | scalars | line items | completeness | hallucinations |
|---|---|---|---|---|---|
| Controlled (3 docs) | 3/3 | 15/15 | 4/4 | 3/3 | 0 |
| Synthetic adversarial (8 docs) | 8/8 | 22/23 | 15/15 | 7/8 | 0 |
| **External holdout (3 docs)** | **3/3** | **9/13** | **0/1** | **1/3** | **0** |

Per holdout:

| document | type | status | outcome |
|---|---|---|---|
| `gkdb0226` invoice | invoice ✓ | `needs_review_missing_fields` | total correct; invoice number and date lost to OCR; vendor read "Marista"; PO field took a Bates number |
| `lmcj0190` freight/returns packet | unknown ✓ | `needs_review_ocr` | all fields null ✓ — tried twice to read it, could not, said so |
| `lnml0028` AP packet | invoice (accepted) | `needs_review_missing_fields` | vendor, date and $4,667.00 correct out of four competing amounts; no invoice number exists, correctly flagged |

**Classification: 3/3 on documents from a different author, a different decade and a
different industry**, including a correct `unknown` on a freight-bill packet and a
correct `invoice` on a 2005 lab invoice — the fresh validation the tightened invoice
taxonomy needed. **No value was invented in any of the three.**

Scalar accuracy on the holdouts fell from 12/13 to 9/13 across the day's changes,
while every remaining error moved from *silent* to *flagged*. That is the trade this
system is supposed to make, and it is a trade: a document-intelligence layer that
declines too readily is safe and useless. On this evidence it is not yet too cautious
— `lnml0028` proves it still reads a hard document correctly — but three documents
cannot settle where the line sits.

---

## What three documents can and cannot establish

**Can:** that a design decision every one of eleven synthetic documents agreed with was
wrong, and wrong in the direction that silently produces confident false values — the
exact failure this project claims to prevent. One real document found it in one run.
It also found a multi-record hallucination, a status that hid its own root cause, and
a confidence score of 90.1 on a misread page.

**Cannot:** anything with a rate attached. Three documents, one domain, one archive,
one era of scanning technology. Every row above is a count.

The lesson is not about tobacco-industry paperwork. It is that a corpus authored by
the same person as the schemas will agree with the schemas' assumptions, including
the assumptions nobody wrote down — and that the cheapest, highest-yield thing
available to this project was three documents somebody else picked.

---

## 8. The amendment policy, and validating it on a document it was not written for

**Policy decision (product owner, reversing my earlier gold):** a value that is struck
through has been *retired by the document*. Disregard it and take the surviving value
written beside it, handwritten included.

My earlier gold said the opposite for `G4` — prefer the printed EUR 18,750.00, because
the handwritten 19,400.00 is uncountersigned while the document's own footer requires
countersignature. That reading is still defensible. It lost on a judgement call, not
on evidence, and `generalization_gold.yaml` now records both the reversal and the
reason, because **gold may be changed by a decision about what the right answer is and
must never be changed to match what the system happened to output.**

### It needed two layers, not one

Recognition had no vocabulary for a correction. The vision prompt marked `[HANDWRITTEN]`
and `[STAMP]` but nothing for strike-through, so a transcription flattened a retired
value and its replacement into two equally-valid-looking numbers, and no downstream
policy could tell them apart. Marking a correction is recognition's job; deciding what
it means is extraction's. The transcriber now emits `[STRUCK]...[/STRUCK]`.

### G4: 5/5, from 8-of-9 abstaining

| | before | after |
|---|---|---|
| `committed_amount` | abstained 8/9, asserted 19,400.00 once | **19,400.00, 5/5** |

The earlier abstaining was never an amendment policy — it was an accidental side
effect of the multi-record rule, which is exactly why it was unstable. The synthetic
adversarial set is now **23/23 scalars and 8/8 document completeness**, its first
clean sweep.

### gkdb0226: the independent test

The holdout had no part in writing the policy. Its P.O. Number field is a three-way
correction: printed `8500005968` struck through, handwritten `850001216 5` below it
*also* struck through, and `85000 14180` handwritten above the table, unstruck.

Vision transcribes it exactly, every time:

```
[HANDWRITTEN] 8500014180
| [STRUCK]8500005968[/STRUCK] | Net 30 | 1862 | 14674 |
[HANDWRITTEN] [STRUCK]850001216 5[/STRUCK]
```

`[STRUCK]` markers present **4/4 runs**; the surviving value present **4/4**.
Extraction over 7 runs: **5 correct, 2 abstentions, 0 wrong values.**

Escalated to vision, the same page also finally yields `invoice_number = 1179`,
`invoice_date = 2005-01-31` and — for the first time anywhere in this project — the
vendor name **"Arista Laboratories"**, which the archive's OCR reads as "Uristo" and
our Tesseract reads as "Marista".

### Pushing it to abstain less bought wrong answers

The two abstentions looked like a rule collision: struck-plus-survivor reads as
"several candidates that differ", which the multi-record rule answers with null. So a
sentence was added giving the amendment rule precedence — *one surviving value means
one answer, not an ambiguity*.

| gkdb0226 `po_reference` | correct | abstained | **wrong** |
|---|---|---|---|
| without the precedence rule (n=7) | 5 | 2 | **0** |
| with it (n=5) | 2 | 1 | **2** |

Both wrong answers were variants of the struck-through handwritten candidate
(`8500012105`, `8500012155`). Telling the model to commit rather than abstain did not
make it more certain; it made it guess, and it guessed at the value the document had
crossed out.

**Reverted.** The abstentions were the system declining when it could not tell which
value had survived, and that is the behaviour worth keeping. This is the trade the
whole project turns on, measured on one field: buying a 29% reduction in abstention
cost a 40% wrong-answer rate on a field where being wrong means citing a cancelled
purchase order number.

### What still blocks it in the real pipeline

None of this fires by default on `gkdb0226`. Tesseract scores the page **90.1** —
above the escalation gate — while having silently dropped the *entire* P.O. Number
field, printed and handwritten, along with the invoice number and date. Its text
contains no `8500`, no `14180`, no `P.O` at all.

**Confidence scores the words it found and says nothing about the ones it never
found.** Coverage is invisible to it. Rather than retune a threshold against one
document, the control is exposed — `docint ingest --force-vision` — and the real fix
is recorded above as this evaluation's strongest future item: run both transcriptions
and treat disagreement between them as the signal.
