# Citation verifier

Fault injection (`docint ask --inject`) plants a claim that is flatly false against
the corpus:

> Acme Industrial Supply Co. applied a 12% early-payment discount to INV-2026-0117,
> reducing the amount due to $10,982.40.

There is no discount anywhere in the corpus. The verifier kept it, and said why:

> "$12,480.00 x 0.12 = $1,497.60, and $12,480.00 - $1,497.60 = $10,982.40 ... This
> calculation is arithmetically entailed by the figures in the extract."

It checked the arithmetic and took the premise as given.

## Cause

The prompt said arithmetic over values appearing in the extracts counts as entailed.
That sentence was added to fix the opposite failure — a verifier judging each claim
against each cited chunk separately rejected every cross-document claim, since "the
invoiced $156.00 exceeds the contracted $150.00" is supported by neither document
alone. It overshot: it licensed arithmetic without requiring the *inputs* to appear
in the sources, so any fabrication that computes correctly from an invented rate reads
as entailed. The fix for a false negative created a false positive.

## Measurement

The verifier is a Haiku call with no sampling controls, so one run says nothing.
`eval/verifier_probe.py` calls `_verify` in isolation 10 times per claim, using two
claims that pull in opposite directions — a change that fixes one and breaks the other
is not a fix.

| claim | want | before | after |
|---|---|---|---|
| genuine cross-document synthesis ($156 vs $150 → $480 over 80 units) | supported | 8/10 | **10/10** |
| fabricated premise, consistent arithmetic (the 12% discount) | rejected | 3/10 | **10/10** |

The fabricated claim was passing 70% of the time.

## Fix

Separate values from premises. Arithmetic is permitted only over figures that all
appear in the extracts; any quantity, rate, event or relationship the claim introduces
that is absent makes it unsupported, however consistent the arithmetic:

> "Check that the INPUTS are in the extracts before checking that the result follows
> from them."

## Held out

Both claims above were in hand when the prompt was rewritten, so passing them measures
the fix against its own memory. Two more, written afterwards:

| held-out claim | want | after |
|---|---|---|
| single-source restatement of PO-2026-0043's date, supplier and committed amount | supported | 10/10 |
| "Northwind issued a written change order authorising the additional amount" | rejected | 10/10 |

The second is the harder one: the cited page *says* invoices over the committed amount
require a change order, so every noun in the claim appears in the source. Only the
event does not.

## Limits

Four claims is a probe, not a benchmark, and all four were written by the same author
as the corpus. The two failure modes tested — invented numeric premise, invented event
— are the two I thought of.

This component has been wrong in both directions: too strict, then too permissive. It
is calibrated, not solved. The next step is a larger adversarial set written by
someone who did not write the prompt.
