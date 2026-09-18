# The verifier accepted a fabricated claim, and the permission was mine

## How it surfaced

M7 added fault injection (`docint ask --inject`) so the verifier could be exercised
on demand rather than waited on. Two consecutive runs of the same question with the
same planted claim disagreed:

```
ask --inject once     round 0: 13 claims drafted, 12 verified, 1 stripped   <- caught
ask --inject always   round 0: 10 claims drafted, 10 verified, 0 stripped   <- kept
```

The planted claim is flatly false against this corpus:

> Acme Industrial Supply Co. applied a 12% early-payment discount to INV-2026-0117,
> reducing the amount due to $10,982.40.

There is no discount anywhere in the corpus. The verifier kept it and wrote down why:

> "$12,480.00 x 0.12 = $1,497.60, and $12,480.00 - $1,497.60 = $10,982.40, which
> matches the amount stated in the claim. **This calculation is arithmetically
> entailed by the figures in the extract.**"

It verified the arithmetic and took the *premise* as given.

## The cause

The previous verifier prompt said:

> "simple arithmetic over values that appear in them (a difference, a sum, a
> comparison) counts as entailed"

I added that sentence to fix an earlier, opposite failure: a verifier that judged each
claim against each cited chunk separately rejected every cross-document claim, because
"the invoiced $156.00 exceeds the contracted $150.00" is not supported by the invoice
alone or by the vendor record alone. Both fixes are about the same boundary and the
first one overshot: it licensed arithmetic without requiring the *inputs* to that
arithmetic to appear in the sources. A fabrication that computes correctly from an
invented rate then reads as entailed.

Two failures with one shape: **the fix for a false negative created a false positive.**

## Measurement, not anecdote

One run tells you what happened once. The verifier is a Haiku call and sampling
parameters are unavailable, so `eval/verifier_probe.py` calls `_verify` in isolation
**10 times per claim** - a fraction of a cent per measurement instead of a whole query.

Two claims pulling in opposite directions, because a change that fixes one and breaks
the other is not a fix:

| claim | want | before | after |
|---|---|---|---|
| genuine cross-document synthesis ($156 vs $150 -> $480 over 80 units) | supported | **8/10** | **10/10** |
| fabricated premise, internally consistent arithmetic (the 12% discount) | rejected | **3/10** | **10/10** |

n = 10 per cell. The fabricated claim was passing **70% of the time**.

## The fix

Separate *values* from *premises*. Arithmetic is permitted only over figures that all
appear in the extracts; any quantity, rate, event, action or relationship the claim
introduces that is not in the extracts makes it unsupported, however accurate the rest
of it is and however consistent the arithmetic:

> "Check that the INPUTS are in the extracts before checking that the result follows
> from them: a correct calculation performed on an invented input is not support, it
> is a fabrication wearing arithmetic."

## Held out, because fixing against the example that exposed it proves nothing

Both claims above were in hand when the prompt was rewritten, so passing them measures
the fix against its own memory. Two more were written afterwards and never used to
develop it:

| held-out claim | want | after |
|---|---|---|
| single-source restatement of PO-2026-0043's date, supplier and committed amount | supported | **10/10** |
| "Northwind issued a written change order authorising the additional amount" | rejected | **10/10** |

The second is the interesting one. The cited page *says* that invoices over the
committed amount require a written change order, so every noun in the claim appears in
the source - only the event does not. That is the shape the old prompt was weakest
against, and it is now caught 10/10.

## What this does and does not establish

**Does:** the verifier catches two structurally different fabrications at 10/10 while
still passing genuine cross-document synthesis at 10/10, on four claims over three
documents, one of which is a real scan.

**Does not:** four claims is a probe, not a benchmark. All four were written by the
same author as the corpus. The two failure modes tested - invented numeric premise,
invented event - are the two I thought of; a verifier prompt tuned by hand against
hand-written adversarial claims will be strongest exactly where its author's
imagination reached and no further.

**Standing risk:** this component has now been wrong in both directions within one
day - too strict (rejecting all cross-document claims), then too permissive (accepting
arithmetic on invented premises). The boundary it is policing is genuinely narrow, and
the honest position is that it is calibrated, not solved. The next thing to build is
not another prompt revision but a larger set of adversarial claims written by someone
who did not write the prompt.
