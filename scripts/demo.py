"""The two-minute walkthrough. Every step is a real run - nothing here is canned.

The arc is deliberate: messy input -> a useful cross-document answer -> verify it
yourself -> watch it catch itself -> watch it refuse. Each step proves one of the
five things this system claims, in the order they depend on each other.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"

QUESTION = ("Did Acme bill us above their contracted rate on INV-2026-0117, and does "
            "the invoice stay within what PO-2026-0043 committed?")


def step(n: int, title: str, why: str, args: list[str]) -> str:
    print(f"\n\n{BOLD}{'=' * 78}\n{n}. {title}\n{'=' * 78}{RESET}")
    print(f"{DIM}{why}{RESET}")
    print(f"\n{BOLD}$ docint {' '.join(a if ' ' not in a else repr(a) for a in args)}{RESET}")
    started = time.perf_counter()
    out = subprocess.run([PY, "-m", "docint.cli", *args], capture_output=True, text=True,
                         cwd=ROOT).stdout
    print(out)
    print(f"{DIM}({time.perf_counter() - started:.1f}s){RESET}")
    return out


def main() -> int:
    step(1, "Mixed-format ingestion",
         "A digital PDF, a real 300 DPI scan read with Tesseract, and a spreadsheet.\n"
         "Each becomes typed, field-extracted, location-anchored, content-addressed chunks.",
         ["ingest", "corpus/"])

    step(2, "The same command again",
         "Content-addressed idempotence. Identical chunk IDs, zero model calls, no cost -\n"
         "the manifest is keyed on the file's hash, so unchanged bytes short-circuit\n"
         "parsing, OCR, classification and extraction before any of them run.",
         ["ingest", "corpus/"])

    out = step(3, "A cross-document answer",
               "Answering needs all three documents and both a digital and a scanned source.\n"
               "Both overages are exactly $480.00, so the answer is checkable by hand.",
               ["ask", QUESTION, "--as", "procurement_analyst"])

    # Pull a scanned-PO citation straight out of what step 3 printed, rather than
    # hardcoding an ID: the demo should break if the citation stops resolving.
    chunk_id = next((line.split()[0] for line in out.splitlines()
                     if "po_acme_001.pdf" in line and len(line.split()[0]) == 16), None)
    if chunk_id:
        step(4, "Verify a citation by hand",
             "The ID under the answer is the store's primary key, so checking a claim is a\n"
             "lookup, not a search. This is the SCANNED page - what you see is Tesseract's\n"
             "actual output, artefacts included.",
             ["show", chunk_id])

    step(5, "Watch it catch itself",
         "A false claim is planted into the draft and cited to a real chunk. The verifier\n"
         "sees only the claim and its cited text - never the question - strips it, and the\n"
         "system regenerates once. This is fault injection, not an observed hallucination.",
         ["ask", QUESTION, "--as", "procurement_analyst", "--inject", "once"])

    step(6, "Watch it refuse",
         "The same question as an external auditor. The interesting kind of refusal: the\n"
         "vendor record IS retrieved - the auditor may see it - while the invoice and PO\n"
         "are filtered out inside the vector store. It declines rather than answering from\n"
         "the half it can see.",
         ["ask", QUESTION, "--as", "external_auditor"])

    print(f"\n\n{BOLD}{'=' * 78}\n7. See it measured\n{'=' * 78}{RESET}")
    print(f"{DIM}Seven cases scored deterministically against hand-written gold.{RESET}")
    print(f"\n{BOLD}$ make eval{RESET}    -> runs/report.md")
    print(f"{BOLD}$ make eval-ingest{RESET}    classification, OCR routing and extraction")
    print(f"{BOLD}$ make probe{RESET}          the verifier alone, 10 runs per claim\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
