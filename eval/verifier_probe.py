"""Isolate the citation verifier and measure it directly, N times per claim.

Why this exists: `ask --inject always` kept a fabricated claim that `ask --inject
once` had stripped minutes earlier. The verifier is a Haiku call with no sampling
controls, so a single observation says nothing about whether a claim is caught -
it says what happened once. This probe calls `_verify` in isolation, so a
measurement costs a fraction of a cent instead of a full query, and reports a rate.

Two claims, deliberately chosen to pull in opposite directions:

  SUPPORTED   a genuine cross-document claim. An earlier verifier rejected every
              claim of this shape and destroyed the product; it must keep passing.
  FABRICATED  invents a premise (an early-payment discount) that appears nowhere,
              then computes correctly FROM that invented premise. Must be rejected.

A change that fixes one and breaks the other is not a fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docint.answer import FAULT_CLAIM, _verify
from docint.index import get_chunks

INVOICE_CHUNK = "afbb2080425f05a5"
VENDOR_CHUNK = "42e12b19472785e8"

PO_CHUNK = "7b27e54621ffa38d"

SUPPORTED_CLAIM = ("The invoiced unit price of $156.00 exceeds the contracted unit price "
                   "of $150.00 by $6.00 per unit, which across 80 units amounts to "
                   "$480.00 in excess billing.")

# Held out. Written AFTER the prompt fix and never used to develop it, because a fix
# validated only on the example that exposed it measures the fix against its own
# memory. These two are different shapes: a single-source restatement that must pass,
# and a fabrication with no arithmetic at all that must fail.
SUPPORTED_HELD_OUT = ("PO-2026-0043 was issued on 2026-02-28 to Acme Industrial Supply Co. "
                      "with a total committed amount of $12,000.00.")

# The hard one. The cited extract SAYS that invoices over the committed amount require
# a written change order; this claim asserts that one was issued. Every noun in it
# appears in the source - only the event does not.
FABRICATED_HELD_OUT = ("Northwind Manufacturing LLC issued a written change order "
                       "authorising the additional amount billed above PO-2026-0043.")

N = 10


def sources_for(chunk_ids: list[str]) -> list[tuple[str, str]]:
    chunks = get_chunks(chunk_ids)
    missing = [c for c in chunk_ids if c not in chunks]
    if missing:
        raise SystemExit(f"chunk(s) {missing} not in the index - run `docint ingest corpus/` first")
    return [(f"{chunks[c]['filename']} - {chunks[c]['location']}", chunks[c]["text"])
            for c in chunk_ids]


def probe(label: str, claim: str, chunk_ids: list[str], want_supported: bool) -> None:
    sources = sources_for(chunk_ids)
    supported = 0
    reasons: list[str] = []
    for _ in range(N):
        verdict, _cost = _verify(claim, sources)
        supported += int(verdict.supported)
        if verdict.supported != want_supported:
            reasons.append(verdict.reason)

    correct = supported if want_supported else N - supported
    print(f"\n{label}")
    print(f"  claim        {claim[:96]}")
    print(f"  cites        {', '.join(chunk_ids)}")
    print(f"  want         supported={want_supported}")
    print(f"  got          supported {supported}/{N}  ->  CORRECT {correct}/{N}")
    if reasons:
        print(f"  a wrong verdict's stated reasoning:\n    {reasons[0][:400]}")


if __name__ == "__main__":
    print(f"verifier probe - {N} runs per claim, no sampling controls available on the model")
    probe("SUPPORTED  (genuine cross-document synthesis)", SUPPORTED_CLAIM,
          [INVOICE_CHUNK, VENDOR_CHUNK], want_supported=True)
    probe("FABRICATED (invented premise, internally consistent arithmetic)", FAULT_CLAIM,
          [INVOICE_CHUNK], want_supported=False)
    print("\n--- held out: written after the fix, never used to develop it ---")
    probe("SUPPORTED  (single-source restatement)", SUPPORTED_HELD_OUT,
          [PO_CHUNK], want_supported=True)
    probe("FABRICATED (invented event; every noun in it appears in the source)",
          FABRICATED_HELD_OUT, [PO_CHUNK], want_supported=False)
