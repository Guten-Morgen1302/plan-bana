import pytest

from maa.classify import Reply, classify

CASES = [
    ("haan", Reply.YES),
    ("Haan beta bhej do", Reply.YES),
    ("haan haan theek hai", Reply.YES),
    ("ji haan", Reply.YES),
    ("ok bhej do na", Reply.YES),
    ("हाँ बेटा भेज दो", Reply.YES),
    ("हां ठीक है", Reply.YES),
    ("theek hai", Reply.YES),
    ("yes", Reply.YES),
    ("nahi", Reply.NO),
    ("nahi chahiye", Reply.NO),
    ("mat bhejo", Reply.NO),
    ("नहीं", Reply.NO),
    ("रहने दो", Reply.NO),
    ("cancel", Reply.NO),
    ("haan aur chai patti bhi", Reply.OTHER),
    ("haan par doodh do packet", Reply.OTHER),
    ("bread nahi brown bread chahiye", Reply.OTHER),
    ("हाँ और चीनी भी", Reply.OTHER),
    ("aur ande bhi", Reply.OTHER),
    ("", Reply.OTHER),
    ("kitna hua", Reply.OTHER),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_classify(text, expected):
    assert classify(text) is expected
