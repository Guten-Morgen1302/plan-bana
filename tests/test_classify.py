import pytest

from maa.classify import Reply, classify

YES = [
    "haan", "Haan beta bhej do", "haan haan theek hai", "ji haan", "ok bhej do na", "theek hai", "yes",
    "theek hai order place kardo",          # real bug report 2026-09-29
    "theek hai order place kar do", "order kar do", "haan confirm", "bilkul bhej do", "haanji", "pakka",
    "हाँ।", "हाँ, भेज दो।", "ठीक है।", "ठीक है ऑर्डर प्लेस कर दो।",
    "हाँ बेटा भेज दो", "हां ठीक है", "ठीक है ऑर्डर प्लेस कर दो", "ठीक है ऑर्डर कर दो", "हाँ जी", "अच्छा भेज दो",
]
NO = [
    "nahi", "nahii", "nahi nahi", "nahi chahiye", "abhi nahi", "mat bhejo", "cancel", "cancel kar do",
    "rehne do", "नहीं।", "नहीं, रहने दो।", "Nahi.", "nahi!",
    "नहीं", "नहीं नहीं", "रहने दो", "मत भेजो", "अभी नहीं", "कैंसल कर दो", "नहीं चाहिए",
]
OTHER = [
    "haan aur chai patti bhi", "haan par doodh do packet", "bread nahi brown bread chahiye", "हाँ और चीनी भी",
    "aur ande bhi", "", "kitna hua", "haan nahi", "doodh", "नींबू हटा दो",
]


@pytest.mark.parametrize("text", YES)
def test_yes(text):
    assert classify(text) is Reply.YES


@pytest.mark.parametrize("text", NO)
def test_no(text):
    assert classify(text) is Reply.NO


@pytest.mark.parametrize("text", OTHER)
def test_other(text):
    assert classify(text) is Reply.OTHER
