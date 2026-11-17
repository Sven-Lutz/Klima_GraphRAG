from __future__ import annotations

import re


FINANCIAL_SUPPORT_PATTERNS = (
    r"\bzuschuss(?:es|e|en)?\b",
    r"\bfinanzielle[nrs]? förderung\b",
    r"\bfinanziell gefördert\b",
    r"\bwird\b[.!?]{0,180}\bgefördert\b",
    r"\bwerden\b[.!?]{0,180}\bgefördert\b",
    r"\bförderung (?:für|von|der)\b",
)

ENDED_PATTERNS = (
    r"\bnicht mehr\b",
    r"\bkeinen? zuschuss mehr\b",
    r"\bförderung (?:wurde|ist) (?:beendet|eingestellt|ausgelaufen)\b",
    r"\bförderprogramm (?:wurde|ist) (?:beendet|eingestellt|ausgelaufen)\b",
    r"\bausgelaufen\b",
    r"\beingestellt\b",
    r"\bbeendet\b",
)


def passage_has_financial_support(passage: str) -> bool:
    text = " ".join(passage.lower().split())
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in FINANCIAL_SUPPORT_PATTERNS)


def passage_explicitly_ended(passage: str) -> bool:
    text = " ".join(passage.lower().split())
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in ENDED_PATTERNS)
