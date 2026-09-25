"""Reading, writing and rendering a QueryTrace - one JSON file per query.

The terminal view is a rendering of the exact file the eval harness scores, so there
is no second account of what happened that could disagree.
"""
from __future__ import annotations

import textwrap
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


def describe_recognition(recognition: str, ocr_confidence: float | None) -> str:
    """How this text was read, for display beside a citation. Vision-read text never
    borrows the word "ocr": the only measured number for such a page is the Tesseract
    pass that triggered the escalation."""
    if recognition == "vision":
        measured = f", tesseract read it {ocr_confidence:.1f}" if ocr_confidence is not None else ""
        return f"vision re-read{measured}"
    if recognition == "tesseract" and ocr_confidence is not None:
        return f"ocr {ocr_confidence:.1f}"
    return "digital text layer"


def _wrap(text: str, width: int = 88, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)


def render(trace: QueryTrace, verbose: bool = False) -> str:
    """The terminal view of a trace, rendered entirely from the JSON.

    Concise by default: someone who asked a question wants the answer, not the
    pipeline's working. `verbose` adds what the run did - who asked, what was
    retrievable to them, what was stripped - which is a operator and demo view, not a
    user one. Everything in either view comes from the same saved trace.
    """
    out: list[str] = []
    add = out.append

    if verbose:
        add(f"\n{BOLD}{trace.question}{RESET}")
        add(f"{DIM}  as {trace.principal}  ·  access_tags {trace.access_tags}"
            f"  ·  type filter {trace.type_filter or 'none'}{RESET}")

        add(f"\n  retrieved   {len(trace.retrieved_chunk_ids)} chunks from "
            f"{len(trace.retrieved_documents)} document(s): "
            f"{', '.join(trace.retrieved_documents) or 'nothing accessible'}")
        if trace.structured_records:
            add(f"  records     typed fields for {', '.join(trace.structured_records)}")

        for rnd in trace.rounds:
            verified = sum(1 for _, v in rnd.verdicts if v.supported)
            add(f"  round {rnd.round_index}     {len(rnd.claims)} claim(s) drafted, "
                f"{verified} verified, {len(rnd.stripped)} stripped"
                + (f", {len(rnd.invalid_citations)} unresolvable citation(s)"
                   if rnd.invalid_citations else ""))
            for s in rnd.stripped:
                add(f"    {BOLD}STRIPPED{RESET}  {s.text}")
                reason = " ".join(s.reason.split())
                if len(reason) > 180:
                    reason = reason[:177] + "..."
                add(f"              {DIM}{reason}{RESET}")

        if trace.regeneration_count:
            add(f"  {DIM}regenerated {trace.regeneration_count}x with the rejection "
                f"reasons fed back{RESET}")

    if trace.final_status == "answered":
        # Only what the answer rests on is shown. The full claim record stays in the
        # trace JSON: eight atomic claims are what the verifier needs and the wrong
        # thing to hand someone who asked a yes/no question.
        if trace.response is not None:
            shown = {e.chunk_id for e in trace.response.evidence}
            references = [c for c in trace.final_citations if c.chunk_id in shown]
        else:
            references = trace.final_citations
        number = {c.chunk_id: i for i, c in enumerate(references, start=1)}

        if trace.response is not None:
            # `answer` is the machine-readable verdict and is deliberately not printed:
            # it describes the SHAPE of the question, so "NOT APPLICABLE" above a
            # correct answer reads as a refusal. A yes/no verdict is already the first
            # word of the summary.
            add(f"\n{BOLD}ANSWER{RESET}")
            add(_wrap(trace.response.summary))
            if trace.response.evidence:
                add(f"\n{BOLD}EVIDENCE{RESET}")
                for e in trace.response.evidence:
                    mark = f"({number[e.chunk_id]})" if e.chunk_id in number else ""
                    add(_wrap(f"{e.claim.rstrip()} {mark}", indent="    "))
        else:
            # A trace written before response synthesis existed.
            add(f"\n{BOLD}ANSWER{RESET}")
            add(_wrap(" ".join(
                f"{c.text.rstrip()} "
                + "".join(f"({number[cid]})" for cid in c.cited_chunk_ids if cid in number)
                for c in trace.kept_claims)))

        add(f"\n{BOLD}REFERENCES{RESET}")
        for c in references:
            add(f"  ({number[c.chunk_id]})  {c.filename} · {c.location}"
                f"{DIM}   {c.chunk_id}  ·  {describe_recognition(c.recognition, c.ocr_confidence)}{RESET}")
    else:
        add(f"\n{BOLD}{trace.final_status.upper()}{RESET}")
        for para in (trace.refusal_reason or "").split("\n\n"):
            if para.strip():
                add(_wrap(para.strip()))

    if verbose:
        add(f"\n{DIM}  ${trace.total_cost_usd:.4f}  ·  {trace.total_latency_s:.1f}s  ·  "
            f"trace {trace.trace_id}{RESET}")
    return "\n".join(out)
