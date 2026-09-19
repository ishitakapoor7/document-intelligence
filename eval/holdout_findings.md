# External holdouts

Three real business documents from the UCSF Industry Documents Library, chosen and
downloaded by someone other than the author of this pipeline, read by hand, with gold
written before the pipeline was run against them.
Sources and hashes: `eval/holdouts/PROVENANCE.txt`.

Everything else in this evaluation — 3 controlled fixtures, 8 synthetic adversarial
documents — was written by the same person who wrote the schemas. These three exist to
close that gap.

| document | what it is |
|---|---|
| `gkdb0226.pdf` | 2005 laboratory invoice, $133,337.50, 14 billable lines, handwritten annotations |
| `lmcj0190.pdf` | returned-goods form + freight bill, none of the configured types |
| `lnml0028.pdf` | 7-page AP packet: 3 unrelated payment matters and one genuine invoice |

## Results

| | classification | scalars | line items | completeness | hallucinations |
|---|---|---|---|---|---|
| Controlled (3) | 3/3 | 15/15 | 4/4 | 3/3 | 0 |
| Synthetic adversarial (8) | 8/8 | 23/23 | 15/15 | 8/8 | 0 |
| **External holdouts (3)** | **3/3** | **12/13** | 0/1 | 1/3 | **0** |

| document | status | outcome |
|---|---|---|
| `gkdb0226` | `extracted` | number, vendor, date, total correct; struck-through PO number returned or declined by run, never wrong |
| `lmcj0190` | `needs_review_ocr` | correctly `unknown`, all fields null — read twice, could not, said so |
| `lnml0028` | `needs_review_missing_fields` | vendor, date and $4,667.00 correct from four competing amounts; no invoice number exists, correctly flagged |

Classification held on documents from a different author, decade and industry, and no
value was invented in any of them. Document completeness — nothing wrong, missing or
unrepresentable — is 1 in 3.

## 1. An inherited OCR text layer bypassed the recognition ladder

All three holdouts are scans. All three also carry an OCR text layer the archive
produced years ago.

The pipeline decided "is this a scan?" by counting extractable characters, so all
eleven pages cleared the threshold and all three documents were labelled
`pdf_digital`. Tesseract never ran, no confidence was measured, the review gate could
not fire (it tests `is not None`), and the vision fallback was unreachable.
`gkdb0226` was reported clean with the vendor name wrong: the archive's OCR reads
*Arista Laboratories* as *Uristo*, and the pipeline asserted it.

This is the "confidently wrong OCR" case `ocr_ladder.md` had named as the most
important missing test, which did not reproduce on eleven synthetic documents. Those
scans were rendered without text layers, so every one of them agreed with the decision
that was wrong.

**Fix:** a page mostly covered by a raster image is a scan whatever text rides along
with it — structural, rather than a guess about content. The inherited layer is
discarded. No regression across the eleven earlier documents.

## 2. Confidence measures the words OCR found, not the ones it dropped

With the fix, `gkdb0226` routes to Tesseract and scores **90.1** — above any sensible
gate — while having silently dropped the invoice number, the date and the entire P.O.
Number field. Its text contains no `8500`, no `14180`, no `P.O` at all.

Escalating on low confidence cannot catch this. Escalating on a **field deficit** can:
if required fields are missing after extraction, re-read with vision and extract
again; if still missing, send to a human.

| | extra cost | effect |
|---|---|---|
| `gkdb0226` | ~$0.12 | 1/5 → 5/5 fields, two silently wrong values eliminated |
| `lnml0028` | ~$0.35 | none — spent confirming a genuinely absent invoice number |

It pays when a field is missing because recognition failed and pays nothing when the
field is genuinely absent, and it cannot tell which in advance. The content-addressed
manifest makes it a one-time cost per document.

`ocr_ladder.md`'s finding that confidence tracked correctness does not survive this
document, and is marked there.

## 3. Two of three holdouts are not one document

`lnml0028` is a 7-page AP packet holding three unrelated payment matters: two check
requests, two cheque stubs, correspondence, and one genuine invoice on page 3.
`lmcj0190` is a returned-goods form and a freight bill.

The data model assumes one file is one document is one record: `document_id` hashes
the file, `document_type` is one label, the field set holds one record. Nothing can
represent a bundle. This is the multi-record problem one level up from a spreadsheet
holding three suppliers, and it is far more common in real archives than in anything
I would have thought to synthesise.

What the system does with them is better than the architecture deserves: `lmcj0190`
is correctly `unknown` despite the freight bill's "TOTAL CHARGES (USD)" column, and
`lnml0028` finds page 3's vendor, date and total correctly from among four competing
amounts, then flags the genuinely absent invoice number.

## 4. A behaviour credited to design belonged to the model

`G7` — a sheet of three suppliers against a one-supplier schema — returned nulls, and
gold recorded that as the design working. Nothing in the extraction prompt asked it to
decline. It later picked row 1 on **5 of 5** runs: three invented values, asserted,
unflagged.

The prompt now states the policy explicitly — several records *of the type being
extracted* means no scalar has a single value — and declining is 8/8 measured. The
first wording was too blunt and refused `lnml0028`, which holds exactly one invoice;
scoped to the type being extracted, both separate cleanly at 3/3.

## 5. Abstention beats a forced choice

`gkdb0226`'s P.O. Number is a three-way correction: printed `8500005968` struck
through, handwritten `850001216 5` also struck through, `85000 14180` written above
and unstruck. Vision transcribes it correctly every time:

```
[HANDWRITTEN] 8500014180
| [STRUCK]8500005968[/STRUCK] | Net 30 | 1862 | 14674 |
[HANDWRITTEN] [STRUCK]850001216 5[/STRUCK]
```

Under the amendment policy (a struck value is retired; the survivor is the answer),
extraction returns the survivor on 5 of 7 runs and abstains twice, never returning a
struck value.

Adding a rule giving the amendment policy precedence over the multi-record rule, to
remove those abstentions:

| `po_reference` | correct | abstained | **wrong** |
|---|---|---|---|
| without the precedence rule (n=7) | 5 | 2 | **0** |
| with it (n=5) | 2 | 1 | **2** |

Both wrong answers were variants of the struck-through candidate. Pushing the model to
commit made it guess at the value the document had cancelled. Reverted.

## 6. Line items

`gkdb0226` returns 16–17 line items where gold says 14. Two extras are the `Subtotal`
rows, captured with no quantity or unit price; summing them double-counts the invoice.
`LineItem` cannot mark a row as a subtotal, so nothing downstream can tell. One row
also loses its description.

## What three documents can and cannot show

**Can:** that a design decision all eleven synthetic documents agreed with was wrong,
in the direction that silently produces confident false values. One real document
found it in one run, along with a multi-record hallucination, a status that hid its
own root cause, and a confidence of 90.1 on a misread page.

**Cannot:** anything with a rate attached. Three documents, one archive, one era of
scanning. Every figure here is a count.
