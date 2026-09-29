"""Code-side yes/no classifier for Mom's reply to a readback (eng D11). The LLM never decides this.

yes  = at least one yes word, no no-word, and no other content words once fillers are removed
no   = at least one no word and no yes word
else = "other" (a change request like "haan aur chai patti bhi" goes back to the agent)

Sarvam codemix returns Hindi words in Devanagari and English in Latin, so both scripts are listed.
"""

from __future__ import annotations

import re
from enum import StrEnum

YES = {
    "haan", "haa", "han", "ha", "hanji", "haanji", "ji", "theek", "thik", "thiik", "bhej", "bhejo", "bhejdo",
    "yes", "ok", "okay", "done", "sure", "kar", "karo", "chalega", "sahi",
    "हाँ", "हां", "हा", "जी", "हांजी", "ठीक", "भेज", "भेजो", "करो", "कर", "चलेगा", "सही", "ओके",
}
NO = {
    "nahi", "nahin", "nai", "na", "mat", "no", "cancel", "ruko", "rehne",
    "नहीं", "नही", "ना", "मत", "रुको", "रहने", "कैंसल",
}
FILLERS = {
    "beta", "bete", "please", "pls", "plz", "bas", "accha", "acha", "achha", "toh", "to", "de", "do", "dijiye",
    "hai", "he", "wala", "wale", "sab", "sabh", "abhi", "jaldi", "order",
    "बेटा", "बेटे", "प्लीज़", "प्लीज", "बस", "अच्छा", "तो", "दे", "दो", "दीजिए", "है", "सब", "अभी", "जल्दी", "ऑर्डर",
}
VERBS = {"bhej", "bhejo", "bhejdo", "kar", "karo", "भेज", "भेजो", "कर", "करो"}
# "na" is a filler at the end of a request ("bhej do na") but a no on its own.
TOKEN_RE = re.compile(r"[\wऀ-ॿ]+")


class Reply(StrEnum):
    YES = "yes"
    NO = "no"
    OTHER = "other"


def classify(text: str) -> Reply:
    tokens = [t.lower() for t in TOKEN_RE.findall(text or "")]
    if not tokens:
        return Reply.OTHER
    # "mat bhejo" / "मत भेजो": a no-word right before a verb negates it, so the verb is not a yes.
    negated = {i + 1 for i, t in enumerate(tokens[:-1]) if t in NO and tokens[i + 1] in VERBS}
    tokens = [t for i, t in enumerate(tokens) if i not in negated]
    yes = [t for t in tokens if t in YES]
    no = [t for t in tokens if t in NO]
    if no and yes and tokens[-1] in {"na", "ना"} and len(no) == 1:
        no = []  # "haan bhej do na": trailing "na" is a softener
    content = [t for t in tokens if t not in YES and t not in NO and t not in FILLERS]
    if no and not yes and not content:
        return Reply.NO
    if yes and not no and not content:
        return Reply.YES
    if no and not yes and len(content) <= 1:
        return Reply.NO  # "nahi chahiye"
    return Reply.OTHER
