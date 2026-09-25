"""Score the query path against eval/cases.yaml. No model grades anything here: every
judgement is a comparison between a QueryTrace and hand-written gold, recomputable
from the trace files by hand.

The runtime verifier is a model call, so figures derived from it - strip and
regeneration rates - are reported separately and labelled model-graded. Ingestion is
scored in run_ingest_eval.py, so each number attributes to one stage.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docint.answer import FAULT_CLAIM, answer_question
from docint.config import ACCESS_PROFILES, ROOT
from docint.index import get_chunks, open_index
from docint.models import QueryTrace
from docint.trace import trace_path

CASES_PATH = ROOT / "eval" / "cases.yaml"
REPORT_PATH = ROOT / "runs" / "report.md"

SCANNED_SOURCES = {"po_acme_001.pdf"}


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def entity_present(entity: str, answer: str) -> bool:
    """Is this figure stated in the answer, in any formatting?

    Compares values, not strings: gold asks for 150.00, the sheet stores 150 and the
    answer says "$150". A string match would penalise the system for not padding a
    figure the document never padded. Non-numeric entities fall back to substring.
    """
    target = NUMBER.fullmatch(entity.replace(",", ""))
    if not target:
        return entity.lower() in answer.lower()
    want = float(entity.replace(",", ""))
    return any(abs(float(m.group().replace(",", "")) - want) < 0.005
               for m in NUMBER.finditer(answer))


def attribute(trace: QueryTrace, case: dict) -> str:
    """Which stage is responsible for this case not matching gold. Earliest wins, so
    a generation failure caused by a retrieval miss reports as retrieval.

    Classification and extraction cannot appear here - the query path does not run
    them, and the ingest eval is where they get named.
    """
    need = set(case.get("must_retrieve") or [])
    if need - set(trace.retrieved_documents):
        return "retrieve"
    if any(r.invalid_citations for r in trace.rounds):
        return "generate"
    if trace.rounds and trace.rounds[-1].stripped:
        return "verify"
    return "generate"


class Result:
    def __init__(self, case: dict, trace: QueryTrace) -> None:
        self.case, self.trace = case, trace
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]


def score(case: dict, trace: QueryTrace, cited: dict[str, dict]) -> Result:
    r = Result(case, trace)

    r.check("status", trace.final_status == case["expect_status"],
            f"got {trace.final_status}, expected {case['expect_status']}")

    for doc in case.get("must_retrieve") or []:
        r.check(f"retrieved {doc}", doc in trace.retrieved_documents,
                f"retrieved {trace.retrieved_documents}")

    # Must never silently pass: a filter returning nothing at all would satisfy
    # "refused" while proving nothing.
    for doc in case.get("must_not_retrieve") or []:
        r.check(f"withheld {doc}", doc not in trace.retrieved_documents,
                f"LEAKED - {doc} reached a principal not cleared for it")

    if case["expect_status"] == "answered":
        answer = trace.final_answer or ""
        for entity in case.get("expect_entities") or []:
            r.check(f"states {entity}", entity_present(entity, answer))

        # Every chunk a surviving claim cites must resolve and belong to a document
        # gold accepts for this question. An ID resolving to the wrong document is a
        # wrong citation even when the sentence it supports is true.
        allowed = set(case.get("expect_sources") or [])
        if allowed:
            for claim in trace.kept_claims:
                for cid in claim.cited_chunk_ids:
                    meta = cited.get(cid)
                    r.check(f"citation {cid} resolves", meta is not None)
                    if meta:
                        r.check(f"citation {cid} in gold sources",
                                meta["filename"] in allowed,
                                f"cites {meta['filename']}, gold allows {sorted(allowed)}")

        want_types = case.get("expect_min_document_types_cited")
        if want_types:
            types = {c.document_type for c in trace.final_citations}
            r.check(f"cites >= {want_types} document types", len(types) >= want_types,
                    f"cited {sorted(types)}")

        if case.get("expect_cites_scanned_source"):
            files = {c.filename for c in trace.final_citations}
            r.check("cites a scanned source", bool(files & SCANNED_SOURCES),
                    f"cited {sorted(files)}")

    stripped = [s for rnd in trace.rounds for s in rnd.stripped]
    if case.get("expect_stripped_min"):
        r.check(f"stripped >= {case['expect_stripped_min']}",
                len(stripped) >= case["expect_stripped_min"], f"stripped {len(stripped)}")

    if case.get("expect_injected_claim_stripped"):
        r.check("planted claim stripped",
                any(FAULT_CLAIM in s.text for s in stripped),
                "the planted claim survived verification")

    if "expect_regenerations" in case:
        r.check(f"regenerated {case['expect_regenerations']}x",
                trace.regeneration_count == case["expect_regenerations"],
                f"regenerated {trace.regeneration_count}x")

    # Checked on every case, not just where gold thinks to ask.
    r.check("no planted claim in the final answer",
            not any(FAULT_CLAIM in c.text for c in trace.kept_claims))

    # The response layer, as invariants rather than gold: an answered case must produce
    # one, and it may only cite evidence drawn from claims that passed verification.
    # Needs no ground truth, so it runs on every case.
    if trace.final_status == "answered":
        r.check("synthesized a response", trace.response is not None)
        if trace.response is not None:
            grounded = {cid for c in trace.kept_claims for cid in c.cited_chunk_ids}
            for e in trace.response.evidence:
                r.check(f"evidence {e.chunk_id} is a verified claim",
                        e.chunk_id in grounded,
                        "cites a chunk no verified claim rests on")
            r.check("no planted claim reached the response",
                    FAULT_CLAIM not in trace.response.summary
                    and not any(FAULT_CLAIM in e.claim for e in trace.response.evidence))

    return r


def run() -> list[Result]:
    cases = yaml.safe_load(CASES_PATH.read_text())["cases"]
    index = open_index()
    results: list[Result] = []

    for case in cases:
        tags = ACCESS_PROFILES[case["principal"]]
        trace = answer_question(case["question"].strip(), case["principal"], tags,
                                index=index, inject=case.get("inject", "none"))
        cited = get_chunks(sorted({cid for c in trace.kept_claims
                                   for cid in c.cited_chunk_ids}), index=index)
        r = score(case, trace, cited)
        results.append(r)

        mark = "PASS" if r.passed else "FAIL"
        print(f"\n[{mark}] {case['id']}  {case['name']}")
        print(f"       {trace.final_status:<9} {len(trace.retrieved_chunk_ids)} chunks / "
              f"{len(trace.retrieved_documents)} docs  ·  {len(trace.kept_claims)} verified "
              f"claim(s)  ·  ${trace.total_cost_usd:.4f}  ·  {trace.total_latency_s:.1f}s")
        print(f"       trace {trace_path(trace.trace_id)}")
        for name, _, detail in r.failures:
            print(f"       FAILED: {name}  {detail}")
        if not r.passed:
            print(f"       failing stage: {attribute(trace, case)}")

    return results


def report(results: list[Result]) -> str:
    n = len(results)
    passed = sum(r.passed for r in results)
    status_ok = sum(1 for r in results
                    if r.trace.final_status == r.case["expect_status"])

    entity_checks = [c for r in results for c in r.checks if c[0].startswith("states ")]
    cite_checks = [c for r in results for c in r.checks
                   if "in gold sources" in c[0] or "resolves" in c[0]]
    acl_checks = [c for r in results for c in r.checks if c[0].startswith("withheld ")]
    resp_checks = [c for r in results for c in r.checks
                   if c[0].startswith("evidence ") or c[0] == "synthesized a response"]

    total_stripped = sum(len(s.stripped) for r in results for s in r.trace.rounds)
    total_claims = sum(len(s.claims) for r in results for s in r.trace.rounds)
    regens = sum(r.trace.regeneration_count for r in results)
    invalid = sum(len(s.invalid_citations) for r in results for s in r.trace.rounds)
    injected = sum(1 for r in results if r.case.get("inject", "none") != "none")
    cost = sum(r.trace.total_cost_usd for r in results)

    def frac(checks: list) -> str:
        return f"{sum(1 for _, ok, _ in checks if ok)}/{len(checks)}" if checks else "n/a"

    lines = [
        "# Query-path eval",
        "",
        f"`{n}` cases over a 3-document corpus. Ingestion is scored separately in "
        "`eval/run_ingest_eval.py`; nothing below measures classification or extraction.",
        "",
        "## Deterministic (recomputable from the trace files by hand)",
        "",
        "| metric | n | result |",
        "|---|---|---|",
        f"| cases fully passing | {n} | **{passed}/{n}** |",
        f"| final status matches gold | {n} | {status_ok}/{n} |",
        f"| answer states the gold figure | {len(entity_checks)} | {frac(entity_checks)} |",
        f"| citation resolves and lands in a gold source | {len(cite_checks)} | {frac(cite_checks)} |",
        f"| withheld document stayed withheld | {len(acl_checks)} | {frac(acl_checks)} |",
        f"| response cites only verified claims | {len(resp_checks)} | {frac(resp_checks)} |",
        f"| unresolvable citation IDs emitted | {total_claims} claims | {invalid} |",
        f"| planted claim reached the user | {injected} injected | "
        f"{sum(1 for r in results if any(FAULT_CLAIM in c.text for c in r.trace.kept_claims))} |",
        "",
        "## Model-graded (the runtime verifier is a Haiku call)",
        "",
        "| metric | n | result |",
        "|---|---|---|",
        f"| claims stripped | {total_claims} drafted | {total_stripped} |",
        f"| regenerations | {n} cases | {regens} |",
        "",
        f"Total spend for this run: **${cost:.2f}**.",
        "",
        "## Per case",
        "",
        "| case | status | docs retrieved | verified claims | stripped | result | failing stage |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        stripped = sum(len(s.stripped) for s in r.trace.rounds)
        lines.append(
            f"| {r.case['id']} {r.case['name']} | {r.trace.final_status} | "
            f"{len(r.trace.retrieved_documents)} | {len(r.trace.kept_claims)} | {stripped} | "
            f"{'pass' if r.passed else '**fail**'} | "
            f"{'-' if r.passed else attribute(r.trace, r.case)} |")

    lines += [
        "",
        "## Reading this honestly",
        "",
        "- **n is small.** Seven cases over three documents. Every row above is a count, "
        "not a rate, and no figure here should be read as a population estimate.",
        "- **C6 and C7 are fault injection, not observed hallucinations.** The unsupported "
        "claim is planted by the harness and the trace records `injected_claim` to say so. "
        "They measure whether the verifier catches a known fabrication - not how often the "
        "generator produces one, which this corpus is far too small to estimate.",
        "- **The verifier is not deterministic.** Sampling parameters are unavailable on "
        "these models, so re-running moves the model-graded rows. `eval/verifier_probe.py` "
        "measures that component directly at n=10 per claim; see `eval/verifier_findings.md` "
        "for a defect it caught and what the fix does and does not establish.",
        "- **C3 is the falsifier for a deferral.** Dense-only retrieval is weakest on exact "
        "identifier lookup, and hybrid search is deferred in the README on the grounds that "
        "it is not needed yet. C3 is the case that would show otherwise; a deferral whose "
        "falsifying test is never run is just an omission.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    results = run()
    text = report(results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n")
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")
    print(f"\nwritten to {REPORT_PATH}")
    sys.exit(0 if all(r.passed for r in results) else 1)
