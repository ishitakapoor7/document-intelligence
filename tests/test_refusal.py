"""The answered/refused boundary. Getting it wrong in either direction inflates the
answered rate and deflates abstention, so it gets a test rather than a comment."""
from docint.answer import is_refusal
from docint.models import Claim, DraftAnswer

CLAIM = Claim(text="The invoice total is $12,480.00.", cited_chunk_ids=["abc123"])


def test_verified_claims_answering_the_question_are_an_answer():
    draft = DraftAnswer(answer="The total is $12,480.00.", claims=[CLAIM])
    assert not is_refusal([CLAIM], draft)


def test_zero_verified_claims_is_a_refusal_however_confident_the_prose():
    draft = DraftAnswer(answer="Acme billed above contract.", claims=[])
    assert is_refusal([], draft)


def test_true_claims_that_do_not_answer_the_question_are_a_refusal():
    # An auditor holding only the vendor record can make true claims about it while
    # being unable to answer what was asked.
    draft = DraftAnswer(
        answer="The chunks provided don't let me answer this; INV-2026-0117 is absent.",
        claims=[CLAIM], answers_question=False)
    assert is_refusal([CLAIM], draft)


def test_answering_defaults_to_true_so_a_missing_field_cannot_silently_refuse():
    assert DraftAnswer(answer="x", claims=[CLAIM]).answers_question is True
