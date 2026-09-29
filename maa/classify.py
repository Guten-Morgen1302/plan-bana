"""Code-side yes/no classifier for Mom's reply to a readback (eng D11). The LLM never decides this.

Words are grouped:
  STRONG_YES  haan / ji / theek / ok / confirm ...   (a yes on their own)
  VERBS       bhej / kar / kardo / place / de ...    (actions: "yes" only if nothing negates them)
  NO          nahi / mat / cancel / ruko ...
  FILLERS     beta / hai / order / abhi / na ...     (ignored)

yes   = (STRONG_YES or VERBS) and no NO word, and no leftover content words
no    = a NO word and no STRONG_YES   ("cancel kar do", "mat bhejo", "abhi nahi", "nahi nahi")
other = anything with real content (items, changes, questions): goes to the agent, never cancels

Sarvam codemix returns Hindi words in Devanagari and English words in Latin, so both are listed.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

STRONG_YES = {
    "haan", "haa", "han", "ha", "haanji", "hanji", "haji", "ji", "jee", "theek", "thik", "thiik", "teek", "tik",
    "yes", "yeah", "yep", "ok", "okay", "okk", "done", "sure", "confirm", "confirmed", "chalega", "chalo",
    "sahi", "bilkul", "zaroor", "jaroor", "pakka", "perfect", "badhiya", "accha", "acha", "achha",
    "हाँ", "हां", "हा", "हाँजी", "हांजी", "जी", "ठीक", "ओके", "ओक", "कन्फर्म", "चलेगा", "चलो", "सही",
    "बिल्कुल", "बिलकुल", "ज़रूर", "जरूर", "पक्का", "परफेक्ट", "बढ़िया", "अच्छा", "यस",
}
VERBS = {
    "bhej", "bhejo", "bhejdo", "bhejdena", "bhejna", "kar", "karo", "kardo", "karde", "kardena", "karna",
    "place", "placed", "de", "do", "dedo", "dijiye", "mangwa", "mangwao", "mangwado", "order",
    "भेज", "भेजो", "भेजदो", "भेजना", "कर", "करो", "करदो", "करदे", "करना", "प्लेस", "दे", "दो", "देदो",
    "दीजिए", "दीजिये", "मंगवा", "मंगवाओ", "मंगवादो", "ऑर्डर", "आर्डर",
}
NO = {
    "nahi", "nahin", "nai", "nahii", "nhi", "mat", "no", "nope", "cancel", "ruko", "ruk", "rehne", "rahne",
    "band", "hatao", "hata",
    "नहीं", "नही", "नई", "मत", "ना", "नो", "कैंसल", "कैन्सल", "रुको", "रुक", "रहने", "बंद", "हटाओ", "हटा",
}
FILLERS = {
    "beta", "bete", "bhai", "please", "pls", "plz", "bas", "toh", "to", "hai", "he", "hain", "wala", "wale",
    "sab", "abhi", "jaldi", "yeh", "ye", "yahi", "wahi", "is", "iss", "ko", "na", "naa", "dena", "chahiye",
    "chaiye", "hmm", "hm", "arre", "are", "achaa", "ab", "phir", "fir", "kuch", "sabhi", "sirf", "bhi",
    "बेटा", "बेटे", "भाई", "प्लीज़", "प्लीज", "बस", "तो", "है", "हैं", "वाला", "वाले", "सब", "अभी", "जल्दी", "यह", "ये",
    "यही", "वही", "इस", "को", "देना", "चाहिए", "चाहिये", "हम्म", "अरे", "अब", "फिर", "कुछ", "सिर्फ", "भी",
}
# Devanagari letters/marks only: excludes the danda "।" (U+0964) and "॥" (U+0965) that Sarvam
# appends as a full stop, which would otherwise glue onto the last word ("नहीं।").
TOKEN_RE = re.compile("[\\wऀ-ॣ०-ॿ]+")
# "कर दो" / "place kar do" are split by Sarvam; join common two-word verbs so they read as one.
JOIN = {("kar", "do"): "kardo", ("kar", "de"): "karde", ("bhej", "do"): "bhejdo", ("de", "do"): "dedo",
        ("कर", "दो"): "करदो", ("भेज", "दो"): "भेजदो", ("दे", "दो"): "देदो"}


class Reply(StrEnum):
    YES = "yes"
    NO = "no"
    OTHER = "other"


def _tokens(text: str) -> list[str]:
    raw = [t.lower() for t in TOKEN_RE.findall(unicodedata.normalize("NFC", text or ""))]
    out: list[str] = []
    i = 0
    while i < len(raw):
        pair = (raw[i], raw[i + 1]) if i + 1 < len(raw) else None
        if pair in JOIN:
            out.append(JOIN[pair])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return out


def classify(text: str) -> Reply:
    tokens = _tokens(text)
    if not tokens:
        return Reply.OTHER
    strong = any(t in STRONG_YES for t in tokens)
    verb = any(t in VERBS for t in tokens)
    # a lone trailing "na" softens a request ("bhej do na"); elsewhere it's a no
    no_words = [t for i, t in enumerate(tokens) if t in NO and not (t in {"na", "ना"} and i == len(tokens) - 1 and (strong or verb))]
    content = [t for t in tokens if t not in STRONG_YES and t not in VERBS and t not in NO and t not in FILLERS]

    if content:
        # "nahi chahiye" style is covered by FILLERS; real words (items, sizes) mean a change request
        return Reply.OTHER
    if no_words and not strong:
        return Reply.NO
    if (strong or verb) and not no_words:
        return Reply.YES
    return Reply.OTHER  # "haan nahi" etc: ask plainly
