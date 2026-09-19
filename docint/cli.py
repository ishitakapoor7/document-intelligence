"""Command line entry point.  python -m docint.cli <command>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docint.config import ACCESS_PROFILES, CORPUS_DIR
from docint.ingest import ingest_directory
from docint.models import IngestOutcome

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _fmt_conf(value: float | None) -> str:
    return f"{value:5.1f}" if value is not None else "    -"


STATUS_BANNER = {
    "extracted": "",
    "needs_review_missing_fields": f"  {BOLD}** REVIEW: MISSING REQUIRED FIELDS **{RESET}",
    "needs_review_ocr": f"  {BOLD}** REVIEW: RECOGNITION TOO POOR **{RESET}",
    "unknown_type": f"  {BOLD}** UNKNOWN TYPE **{RESET}",
}


def _print_outcome(path: Path, outcome: IngestOutcome) -> None:
    doc = outcome.document

    if outcome.status == "unchanged":
        # Carry the remembered verdict. Without it, idempotence silently emptied the
        # review queue: the second run of a corpus said `unchanged` for a document
        # the first run had flagged for a human.
        remembered = outcome.remembered
        flag = ""
        if remembered is not None and remembered.status != "extracted":
            flag = f"  {BOLD}[{remembered.status}]{RESET}"
        print(f"  {path.name:<30} {DIM}unchanged{RESET}  {DIM}{outcome.detail}{RESET}{flag}")
        return
    if outcome.status == "unsupported_format":
        print(f"  {path.name:<30} unsupported  {outcome.detail}")
        return

    print(f"\n  {BOLD}{doc.filename}{RESET}{STATUS_BANNER.get(outcome.status, '')}")
    print(f"    document_id  {doc.document_id}")
    print(f"    status       {doc.status}")
    print(f"    type         {doc.document_type}  (confidence {doc.classification_confidence:.2f})")
    print(f"    file_type    {doc.file_type}    access_tag  {doc.access_tag}")

    ocr = doc.ocr
    if ocr.tesseract_confidence is None:
        print(f"    recognition  born-digital text layer, no OCR")
    else:
        line = f"    recognition  tesseract {ocr.tesseract_confidence:.1f}"
        if ocr.fallback_used:
            line += (f" -> {ocr.fallback_route} fallback -> {ocr.post_fallback_confidence:.1f}"
                     f" {DIM}(model-reported, not a measured OCR confidence){RESET}")
            line += f"  [${ocr.fallback_cost_usd:.4f}, {ocr.fallback_latency_s:.1f}s]"
        else:
            line += "  (no fallback)"
        print(line)

    if doc.missing_required_fields:
        print(f"    missing      {', '.join(doc.missing_required_fields)}")
    if outcome.detail and outcome.status != "extracted":
        print(f"    note         {outcome.detail}")

    print(f"    chunks       {len(outcome.chunks)}")
    for chunk in outcome.chunks:
        conf = f"ocr {chunk.ocr_confidence:4.1f}" if chunk.ocr_confidence is not None else "digital "
        print(f"      {chunk.chunk_id}  {chunk.source_location.render():<18} "
              f"{conf}  {len(chunk.text):>5} chars")

    if doc.fields:
        print(f"    fields       {len(doc.fields)}")
        for field in doc.fields:
            flag = f"  {BOLD}LOW-CONFIDENCE{RESET}" if field.low_confidence else ""
            print(f"      {field.name:<26} {str(field.value):<32} "
                  f"{DIM}{field.chunk_id}{RESET}{flag}")
    if doc.line_items:
        print(f"    line_items   {len(doc.line_items)}")
        for item in doc.line_items:
            desc = (item.description or "")[:42]
            print(f"      {desc:<44} qty={str(item.quantity):<8} "
                  f"unit={str(item.unit_price):<10} amt={item.amount}")
    print(f"    cost/latency ${doc.extraction_cost_usd + doc.ocr.fallback_cost_usd:.4f}"
          f"  /  {doc.total_latency_s:.1f}s")


def cmd_ingest(args: argparse.Namespace) -> int:
    directory = Path(args.path)
    print(f"\ningesting {directory}/")
    results = ingest_directory(directory, force=args.force,
                               vision_fallback=not args.no_vision_fallback)

    counts: dict[str, int] = {}
    for path, outcome in results:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
        _print_outcome(path, outcome)

    print()
    if set(counts) == {"unchanged"}:
        print(f"  {counts['unchanged']} unchanged")
    else:
        print("  " + ", ".join(f"{n} {status}" for status, n in sorted(counts.items())))
    print()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from docint.answer import answer_question
    from docint.trace import render, trace_path

    tags = ACCESS_PROFILES.get(args.as_profile)
    if tags is None:
        print(f"unknown profile {args.as_profile!r}; known: {', '.join(sorted(ACCESS_PROFILES))}")
        return 2

    trace = answer_question(args.question, args.as_profile, tags, inject=args.inject)
    print(render(trace))
    print(f"{DIM}  audit trace written to {trace_path(trace.trace_id)}{RESET}\n")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Resolve a citation by hand. The ID under an answer is the store's primary key."""
    from docint.index import get_chunks

    tags = ACCESS_PROFILES.get(args.as_profile)
    if tags is None:
        print(f"unknown profile {args.as_profile!r}; known: {', '.join(sorted(ACCESS_PROFILES))}")
        return 2

    found = get_chunks([args.chunk_id])
    chunk = found.get(args.chunk_id)
    if chunk is None:
        print(f"\n  no chunk {args.chunk_id!r} in the index - ingest first, or check the ID\n")
        return 1

    # `show` is a read of the same corpus, so it answers to the same access rule.
    # A citation a principal cannot retrieve is not one they may read out of band.
    if chunk["access_tag"] not in tags:
        print(f"\n  {BOLD}access denied{RESET}  {args.as_profile} is not cleared for this document\n")
        return 3

    conf = (f"ocr {chunk['ocr_confidence']:.1f}" if chunk["ocr_confidence"] >= 0
            else "born-digital text layer")
    print(f"\n  {BOLD}{chunk['filename']} - {chunk['location']}{RESET}")
    print(f"  {DIM}{args.chunk_id}  ·  {chunk['document_type']}  ·  {conf}"
          f"  ·  access_tag {chunk['access_tag']}{RESET}\n")
    print("\n".join(f"    {line}" for line in chunk["text"].splitlines()))
    print()
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """List every ingested document waiting on a human, and why.

    This is the whole of the human-in-the-loop story and it is deliberately not a
    workflow: no assignment, no locking, no resume, no state transitions. Human
    review is an OUTPUT STATE - the pipeline stops, says what it could not do, and
    leaves the document indexed and findable. What this command adds is that the
    state is durable and queryable rather than a line that scrolled past during an
    ingest six hours ago.

    The deferred upgrade, with its trigger, is in the README: when review becomes a
    workflow someone works through - claims an item, resolves it, and the correction
    flows back - that is a durable, resumable process and the right moment to bring
    in a graph runtime. It is not this.
    """
    from docint.ingest import load_manifest

    manifest = load_manifest()
    waiting = sorted((e for e in manifest.values() if e.status != "extracted"),
                     key=lambda e: (e.status, e.filename))

    if not manifest:
        print("\n  nothing ingested yet - run `make ingest`\n")
        return 0
    if not waiting:
        print(f"\n  {len(manifest)} document(s) ingested, none waiting on a human\n")
        return 0

    print(f"\n  {BOLD}{len(waiting)} of {len(manifest)} document(s) need a human{RESET}\n")
    for entry in waiting:
        conf = (f"recognition {entry.ocr_mean_confidence:.1f}"
                if entry.ocr_mean_confidence is not None else "born-digital text")
        print(f"  {BOLD}{entry.filename}{RESET}")
        print(f"    {entry.status}   {DIM}{entry.document_type} · {conf} · "
              f"{entry.document_id}{RESET}")
        if entry.missing_required_fields:
            print(f"    missing: {', '.join(entry.missing_required_fields)}")
        if entry.review_reason:
            print(f"    {DIM}{entry.review_reason}{RESET}")
        print(f"    {DIM}inspect: docint show {entry.chunk_ids[0] if entry.chunk_ids else '<no chunks>'}{RESET}")
        print()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the things a fresh clone gets wrong, before they fail mid-pipeline.

    Every check here corresponds to a failure that actually happened during this
    build: a missing binary, a missing key, an empty index that makes `ask` refuse
    for the wrong reason.
    """
    import os
    import shutil

    from docint.config import CHROMA_DIR, MANIFEST_PATH

    ok = True

    def report(label: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and good
        print(f"  {'OK  ' if good else 'FAIL'}  {label:<22} {detail}")

    print("\nenvironment")
    tess = shutil.which("tesseract")
    report("tesseract", tess is not None,
           tess or "not on PATH - scanned PDFs cannot be read (brew install tesseract)")

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    report("ANTHROPIC_API_KEY", bool(key),
           f"set, {len(key)} chars" if key else "missing - put it in .env at the repo root")

    print("\nstate")
    indexed = 0
    if CHROMA_DIR.exists():
        try:
            from docint.index import open_index
            indexed = open_index().vector_store.client.count()
        except Exception as exc:                      # noqa: BLE001 - diagnostic only
            report("vector store", False, f"unreadable: {exc}")
    report("indexed chunks", indexed > 0,
           f"{indexed} chunks" if indexed else "empty - run `make ingest` first, "
           "or `ask` will refuse because nothing is retrievable")
    report("manifest", MANIFEST_PATH.exists(),
           str(MANIFEST_PATH) if MANIFEST_PATH.exists() else "absent - no ingest has run yet")

    print()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docint", description="Document intelligence layer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="parse, classify and extract a directory of documents")
    p_ingest.add_argument("path", nargs="?", default=str(CORPUS_DIR))
    p_ingest.add_argument("--no-vision-fallback", action="store_true",
                          help="disable the Claude vision OCR escalation")
    p_ingest.add_argument("--force", action="store_true",
                          help="re-ingest even if the content hash is already in the manifest")
    p_ingest.set_defaults(func=cmd_ingest)

    p_ask = sub.add_parser("ask", help="ask a question of the ingested corpus")
    p_ask.add_argument("question")
    p_ask.add_argument("--as", dest="as_profile", default="procurement_analyst",
                       choices=sorted(ACCESS_PROFILES),
                       help="the access profile to answer as (stands in for a JWT claim)")
    p_ask.add_argument("--inject", choices=["none", "once", "always"], default="none",
                       help="plant an unsupported claim to exercise the verifier: "
                            "`once` should be stripped and recovered from, `always` refused")
    p_ask.set_defaults(func=cmd_ask)

    p_show = sub.add_parser("show", help="print the chunk a citation points at")
    p_show.add_argument("chunk_id")
    p_show.add_argument("--as", dest="as_profile", default="procurement_analyst",
                        choices=sorted(ACCESS_PROFILES))
    p_show.set_defaults(func=cmd_show)

    p_review = sub.add_parser("review", help="list documents waiting on a human, and why")
    p_review.set_defaults(func=cmd_review)

    p_doctor = sub.add_parser("doctor", help="check the environment and index state")
    p_doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
