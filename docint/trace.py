"""Reading, writing and rendering a QueryTrace.

One JSON file per query under runs/. The trace is the only output format in the
system: `ask` renders one, `eval` scores a list of them, and `show` resolves the
chunk IDs inside one. There is deliberately no second, prettier record of what
happened that could disagree with the audited one - what the reviewer reads on the
terminal is a rendering of the exact file the eval harness scores.
"""
from __future__ import annotations

from pathlib import Path

from docint.config import RUNS_DIR
from docint.models import QueryTrace

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def trace_path(trace_id: str) -> Path:
    return RUNS_DIR / f"{trace_id}.json"


def save(trace: QueryTrace) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = trace_path(trace.trace_id)
    path.write_text(trace.model_dump_json(indent=2))
    return path


def load(trace_id: str) -> QueryTrace:
    return QueryTrace.model_validate_json(trace_path(trace_id).read_text())


def render(trace: QueryTrace) -> str:
    """The terminal view of a trace. Every line here is read back out of the JSON."""
    out: list[str] = []
    add = out.append

    add(f"\n{BOLD}{trace.question}{RESET}")
    add(f"{DIM}  as {trace.principal}  ·  access_tags {trace.access_tags}"
        f"  ·  type filter {trace.type_filter or 'none'}{RESET}")

    add(f"\n  retrieved   {len(trace.retrieved_chunk_ids)} chunks from "
        f"{len(trace.retrieved_documents)} document(s): "
        f"{', '.join(trace.retrieved_documents) or 'nothing accessible'}")

    for rnd in trace.rounds:
        verified = sum(1 for _, v in rnd.verdicts if v.supported)
        add(f"  round {rnd.round_index}     {len(rnd.claims)} claim(s) drafted, "
            f"{verified} verified, {len(rnd.stripped)} stripped"
            + (f", {len(rnd.invalid_citations)} unresolvable citation(s)"
               if rnd.invalid_citations else ""))
        for s in rnd.stripped:
            add(f"    {BOLD}STRIPPED{RESET}  {s.text}")
            add(f"              {DIM}{s.reason}{RESET}")

    if trace.regeneration_count:
        add(f"  {DIM}regenerated {trace.regeneration_count}x with the rejection reasons "
            f"fed back{RESET}")

    if trace.final_status == "answered":
        add(f"\n{BOLD}ANSWER{RESET}\n  {trace.final_answer}")
        add(f"\n  {len(trace.kept_claims)} verified claim(s):")
        for claim in trace.kept_claims:
            add(f"    · {claim.text}")
            add(f"      {DIM}{' '.join(claim.cited_chunk_ids)}{RESET}")
        add(f"\n  citations:")
        for c in trace.final_citations:
            conf = f"  ocr {c.ocr_confidence:.1f}" if c.ocr_confidence is not None else "  digital"
            add(f"    {c.chunk_id}  {c.filename} · {c.location}{DIM}{conf}{RESET}")
    else:
        add(f"\n{BOLD}{trace.final_status.upper()}{RESET}\n  {trace.refusal_reason}")

    add(f"\n{DIM}  ${trace.total_cost_usd:.4f}  ·  {trace.total_latency_s:.1f}s  ·  "
        f"trace {trace.trace_id}{RESET}\n")
    return "\n".join(out)
