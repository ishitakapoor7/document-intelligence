# Query-path eval

`7` cases over a 3-document corpus. Ingestion is scored separately in `eval/run_ingest_eval.py`; nothing below measures classification or extraction.

## Deterministic (recomputable from the trace files by hand)

| metric | n | result |
|---|---|---|
| cases fully passing | 7 | **7/7** |
| final status matches gold | 7 | 7/7 |
| answer states the gold figure | 8 | 8/8 |
| citation resolves and lands in a gold source | 50 | 50/50 |
| withheld document stayed withheld | 2 | 2/2 |
| response cites only verified claims | 12 | 12/12 |
| unresolvable citation IDs emitted | 68 claims | 0 |
| planted claim reached the user | 2 injected | 0 |

## Model-graded (the runtime verifier is a Haiku call)

| metric | n | result |
|---|---|---|
| claims stripped | 68 drafted | 6 |
| regenerations | 7 cases | 2 |

Total spend for this run: **$0.59**.

## Per case

| case | status | docs retrieved | verified claims | stripped | result | failing stage |
|---|---|---|---|---|---|---|
| C1 single-document extraction | answered | 2 | 9 | 0 | pass | - |
| C2 cross-document comparison | answered | 4 | 8 | 0 | pass | - |
| C3 exact identifier lookup on a scanned document | answered | 1 | 5 | 0 | pass | - |
| C4 unsupported question | refused | 1 | 0 | 1 | pass | - |
| C5 access denied, partial evidence | refused | 1 | 0 | 0 | pass | - |
| C6 planted claim, stripped and recovered | answered | 4 | 8 | 2 | pass | - |
| C7 planted claim, unrecoverable | refused | 4 | 0 | 3 | pass | - |

## Reading this honestly

- **n is small.** Seven cases over three documents. Every row above is a count, not a rate, and no figure here should be read as a population estimate.
- **C6 and C7 are fault injection, not observed hallucinations.** The unsupported claim is planted by the harness and the trace records `injected_claim` to say so. They measure whether the verifier catches a known fabrication - not how often the generator produces one, which this corpus is far too small to estimate.
- **The verifier is not deterministic.** Sampling parameters are unavailable on these models, so re-running moves the model-graded rows. `eval/verifier_probe.py` measures that component directly at n=10 per claim; see `eval/verifier_findings.md` for a defect it caught and what the fix does and does not establish.
- **C3 is the falsifier for a deferral.** Dense-only retrieval is weakest on exact identifier lookup, and hybrid search is deferred in the README on the grounds that it is not needed yet. C3 is the case that would show otherwise; a deferral whose falsifying test is never run is just an omission.
