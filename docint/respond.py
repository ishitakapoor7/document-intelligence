"""Response synthesis: verified claims -> the answer a user reads.

Chooses claims according to relevance of question and returns a verdict with at most two pieces of evidence.
"""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from docint.config import MODEL_RESPOND, RESPONSE_MAX_EVIDENCE, usd_cost
from docint.models import Claim, Evidence, SynthesizedAnswer

FALLBACK_SUMMARY = "I couldn't determine this from the verified document evidence."


def _fallback() -> SynthesizedAnswer:
    return SynthesizedAnswer(answer="unknown", summary=FALLBACK_SUMMARY, evidence=[])

PROMPT = """\
Answer the question using ONLY the verified claims below. Each has already been checked
against the document text it cites; you are deciding what they add up to, not whether
they are true.

Rules for `summary` - this is the whole of what the user reads:
- Answer only what was asked. Do not explain values you did not use, do not contrast
  the answer with a similar record, and do not pre-empt a confusion the question did
  not raise.
- ONE sentence. Use a second only when the question has two distinct parts.
- Begin with "Yes" or "No" when the question is a yes/no one.
- Introduce no value, figure, date or fact that is not in the claims below.

`answer` is a verdict for a downstream system and is never shown to the user, so do
not hedge the summary to match it: "yes"/"no" for a yes/no question, "not_applicable"
when the question asks for a value rather than a verdict, "unknown" when the claims do
not settle it - and in that one case say in summary what is missing.

`evidence` holds at most {max_evidence} items, each copied from the claims below -
reuse their document_id and chunk_id exactly. Include only the claim or claims that
carry the answer; a claim that is true but does not bear on the question is not
evidence, and one is usually enough.

<question>{question}</question>

<verified_claims>
{claims}
</verified_claims>"""


def _render(claims: list[Claim], chunk_meta: dict[str, dict]) -> str:
    lines = []
    for claim in claims:
        for cid in claim.cited_chunk_ids:
            meta = chunk_meta.get(cid, {})
            lines.append(f"[document_id: {meta.get('document_id', '?')}] "
                         f"[chunk_id: {cid}] {claim.text}")
    return "\n".join(lines)


def _valid(response: SynthesizedAnswer, allowed_chunks: set[str]) -> bool:
    """Every cited chunk must come from the claims we supplied.

    The schema is enforced by pydantic; this is the part it cannot check - that the
    synthesizer cited evidence it was actually given rather than one it recalled from
    the question.
    """
    return all(e.chunk_id in allowed_chunks for e in response.evidence)


def synthesize(question: str, kept: list[Claim],
               chunk_meta: dict[str, dict]) -> tuple[SynthesizedAnswer, float]:
    """Verified claims -> one user-facing answer. Returns (response, cost in USD).

    Retries once on an invalid response, then falls back rather than showing something
    unvalidated: a wrong answer with a real citation beside it is worse than no answer.
    """
    if not kept:
        return _fallback(), 0.0

    allowed = {cid for c in kept for cid in c.cited_chunk_ids}
    prompt = PROMPT.format(question=question, max_evidence=RESPONSE_MAX_EVIDENCE,
                           claims=_render(kept, chunk_meta))

    llm = ChatAnthropic(model=MODEL_RESPOND, max_tokens=1024)
    cost = 0.0
    for _ in range(2):
        result = llm.with_structured_output(SynthesizedAnswer, include_raw=True).invoke(prompt)
        raw = result.get("raw")
        usage = (raw.usage_metadata or {}) if raw is not None else {}
        cost += usd_cost(MODEL_RESPOND, usage.get("input_tokens", 0),
                         usage.get("output_tokens", 0))
        response = result["parsed"]
        if response is not None and _valid(response, allowed):
            return response, cost

    return _fallback(), cost
