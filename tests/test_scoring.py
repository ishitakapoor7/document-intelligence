"""The eval's own comparisons. A harness that measures the wrong thing is worse
than no harness, because its numbers look like evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from harness import entity_present


def test_figures_match_on_value_not_formatting():
    # The vendor sheet stores 150; the answer says "$150"; gold asks for "150.00".
    assert entity_present("150.00", "a contracted unit price of $150 per unit")
    assert entity_present("12,480.00", "total due 12480.00")
    assert entity_present("12480.00", "total due $12,480.00")


def test_a_different_figure_is_still_a_miss():
    assert not entity_present("150.00", "a contracted unit price of $151")
    assert not entity_present("480.00", "no figure of that kind appears")


def test_substrings_of_a_longer_number_do_not_count():
    # "150" must not match inside "1500.00" - the reason this compares parsed
    # numbers rather than doing a substring search on a stripped string.
    assert not entity_present("150.00", "the amount was $1500.00")
