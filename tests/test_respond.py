"""The response layer's guardrails. The synthesizer is a model call, so what is tested
here is what surrounds it: which claims reach it, and what is refused on the way out."""
import pytest

from docint.models import Evidence, SynthesizedAnswer
from docint.respond import FALLBACK_SUMMARY, _fallback, _valid


def test_evidence_must_come_from_the_supplied_claims():
    # The failure this catches: a citation the synthesizer recalled from the question
    # rather than one it was given. It would resolve, and it would be unearned.
    response = SynthesizedAnswer(
        answer="yes", summary="Yes, Acme billed above contract.",
        evidence=[Evidence(document_id="doc_1", chunk_id="never_supplied",
                           claim="The invoiced unit price exceeds the contracted rate.")])
    assert not _valid(response, {"chunk_a", "chunk_b"})


def test_evidence_drawn_from_supplied_claims_is_valid():
    response = SynthesizedAnswer(
        answer="yes", summary="Yes, Acme billed above contract.",
        evidence=[Evidence(document_id="doc_1", chunk_id="chunk_a",
                           claim="The invoiced unit price exceeds the contracted rate.")])
    assert _valid(response, {"chunk_a", "chunk_b"})


def test_an_answer_with_no_evidence_is_valid():
    # `unknown` legitimately cites nothing; requiring evidence would push the model to
    # manufacture some in exactly the case where it has none.
    assert _valid(_fallback(), set())


def test_schema_rejects_more_than_two_evidence_items():
    e = Evidence(document_id="d", chunk_id="chunk_a", claim="x")
    with pytest.raises(ValueError):
        SynthesizedAnswer(answer="yes", summary="Yes.", evidence=[e, e, e])


def test_schema_rejects_a_verdict_outside_the_enum():
    with pytest.raises(ValueError):
        SynthesizedAnswer(answer="probably", summary="Maybe.")


def test_fallback_is_an_abstention_not_a_verdict():
    assert _fallback().answer == "unknown"
    assert _fallback().summary == FALLBACK_SUMMARY
    assert _fallback().evidence == []


def test_summary_is_never_empty_on_the_fallback_path():
    # The fallback is what a user reads when synthesis fails twice; an empty string
    # there would render as a blank answer rather than an abstention.
    assert _fallback().summary.strip()
