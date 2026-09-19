"""Measure the citation verifier in isolation, N times per claim.

It is a Haiku call with no sampling controls, so one observation says what happened
once, not whether a claim is caught. Calling `_verify` directly costs a fraction of a
cent per measurement instead of a whole query.

The claims pull in opposite directions - genuine cross-document synthesis that must
pass, and fabrications that must not - because a change that fixes one and breaks the
other is not a fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docint.answer import FAULT_CLAIM, _verify
from docint.index import get_chunks, open_index

def chunk_at(filename: str, location: str) -> str:
    """Resolve a chunk by where it lives rather than by a pasted id.

    Chunk ids are derived from source, content hash and location, so they move
    whenever the corpus is re-authored. Looking them up keeps this probe working
    across that.
    """
    store = open_index().vector_store.client
    got = store.get(where={"$and": [{"filename": {"$eq": filename}},
                                    {"location": {"$eq": location}}]})
    if not got["ids"]:
        raise SystemExit(f"no chunk for {filename} {location} - run `docint ingest corpus/` first")
    return got["ids"][0]


INVOICE_CHUNK = chunk_at("invoice_acme_001.pdf", "p. 1")
VENDOR_CHUNK = chunk_at("vendor_records.xlsx", "Vendors!A4:B9")

PO_CHUNK = chunk_at("po_acme_001.pdf", "p. 1")

SUPPORTED_CLAIM = ("The invoiced unit price of $156.00 exceeds the contracted unit price "
                   "of $150.00 by $6.00 per unit, which across 80 units amounts to "
                   "$480.00 in excess billing.")

# Held out: written after the prompt fix and never used to develop it. Two different
# shapes - a single-source restatement that must pass, and a fabrication with no
# arithmetic at all that must fail.
SUPPORTED_HELD_OUT = ("PO-2026-0043 was issued on 2026-02-28 to Acme Industrial Supply Co. "
                      "with a total committed amount of $12,000.00.")

# The hard one: the cited extract says invoices over the committed amount require a
# change order, so every noun appears in the source. Only the event does not.
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
