# Auditable procurement-document prototype

A prototype that reads a folder of procurement paperwork such as invoices, purchase orders
and vendor records, whether scanned, digital or spreadsheet and makes it answerable
without being taken on trust.

It classifies each document, extracts typed fields anchored to a page or cell, and
answers questions spanning several documents. Every claim in an answer is checked
against the specific chunk it cites; claims that fail are removed, and if none
survive, it refuses. Documents it cannot read well enough are routed to human review
rather than guessed at.

It sits in front of a retrieval system rather than replacing one. It is a prototype,
not a product: three document types, one folder, one machine.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install tesseract && cp .env.example .env   # add your ANTHROPIC_API_KEY

python -m docint.cli doctor          # tesseract, API key, index state
python -m docint.cli ingest corpus/  # parse, classify, extract - run it twice
python -m docint.cli ask "Did Acme bill us above their contracted rate on INV-2026-0117, and does the invoice stay within what PO-2026-0043 committed?"
```

The second `ingest` prints `3 unchanged` and makes no model calls. The answer cites a
chunk id per claim; `docint show <chunk_id>` prints the page or cell it came from, and
`docint review` lists anything waiting on a human. Nothing needs a service or a
database.

Evaluation, which makes live model calls:

```bash
python eval/run_ingest_eval.py   # ingestion, 14 documents across 3 sets  (~$1.20)
python eval/harness.py           # query path, 7 cases -> runs/report.md  (~$0.50)
python -m pytest tests/ -q       # 21 unit tests, offline
```

## Architecture

```
  corpus/                    parse ──────────► classify ──► extract
  ├── invoice.pdf     (digital)   │              haiku        opus
  ├── po.pdf          (scanned)   │                             │
  └── vendors.xlsx                ▼                             ▼
                          recognition ladder            typed fields +
                          ─────────────────             chunk-anchored
                          tesseract                       locations
                             │ low confidence                 │
                             │ or missing fields              ▼
                             ▼                            Chroma index
                          claude vision                   (access-tagged)
                             │ still unreadable               │
                             ▼                                ▼
                          human review              retrieve ── access filter
                                                       applied inside the store
                                                          │
                                                          ▼
                                             answer ──► verify each claim
                                              opus       haiku, sees only the
                                                │        claim + cited chunk
                                                ▼
                                    strip → regenerate once → refuse
                                                │
                                                ▼
                                      runs/<trace_id>.json
```

## Evidence

- **Corpus.** 14 documents across three sets, scored against hand-written ground
  truth with no model in the scoring path: 3 controlled fixtures, 8 synthetic
  adversarial, and 3 external holdouts from a public archive with source URLs,
  hashes, and gold written before the pipeline was run against them. Plus 7 query
  cases. Transcripts: `eval/ingest_eval_output.txt`, `runs/report.md`.

- **What works.** Classification 14/14 across all three sets, including four
  documents correctly typed `unknown` rather than forced into a schema. No value was
  invented anywhere: 0 hallucinations across 51 scored fields. On the query path,
  7/7 cases reached the right verdict, 46/46 citations resolved into a gold-approved
  source, and no unauthorised chunk reached a principal who should not see it. Every
  remaining extraction error on real external documents is an abstention, not a
  wrong answer.

- **Known limitation.** Scope is **single-record documents** — one file, one
  invoice or purchase order or vendor record. It does not segment packets, and
  packets are not rare: two of the three external holdouts are multi-document files.
  The eval scores those as schema mismatches rather than passes, which is why
  document completeness on real external documents is **1 in 3**. Segmentation comes
  before this meets an archive. Fourteen documents is too small a sample for any
  figure above to be read as a rate.

## Detail

- [`eval/holdout_findings.md`](eval/holdout_findings.md) — what three externally
  authored documents exposed that eleven of my own had agreed with. The most useful
  thing to read if you only read one.
- [`eval/verifier_findings.md`](eval/verifier_findings.md),
  [`eval/ocr_ladder.md`](eval/ocr_ladder.md) — measured behaviour of the citation
  verifier and the recognition thresholds.

## Layout

```
docint/     models · config · parse · understand · vision_ocr
            index · ingest · answer · trace · cli
eval/       gold + findings for all three document sets, plus both harnesses
runs/       manifest.json · <trace_id>.json · report.md
```
