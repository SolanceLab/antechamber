# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Question handling shared by every shelf: content words, instruction/laughter stripping, list questions."""
import re

STOP = set("a an the and or of to in on at for with is are was were be been did do does what when where who whom which "
           "why how has have had her his their its it this that these those from by as about into after before during than "
           "then there they them she he i you we my your our me us not no yes can could would should will may might "
           "tell know remember said say".split())


def terms(q):
    """The question's content words — letters/digits only, so they are safe inside a tsquery."""
    seen, out = set(), []
    for w in re.findall(r"[a-z0-9]+", q.lower()):
        if w not in STOP and len(w) > 1 and w not in seen:
            seen.add(w); out.append(w)
    return out[:12]


_LIST_Q = re.compile(
    r"\b(?:how many|how often|which (?:\w+ )?(?:\w+s)\b|what (?:are|were) (?:some|all|the)\b|"
    r"what (?:\w+ )?(?:activities|things|places|books|events|hobbies|ways|kinds|types|items|games|songs|countries|cities|"
    r"sports|instruments|pets|projects|gifts|foods|movies|shows|people|friends|names)\b|"
    r"all (?:the|of)\b|so far|ever (?:been|done|had|made|visited|tried|read|played))", re.I)
def is_list_question(q):
    """A question whose answer is several things gathered from several moments."""
    return bool(_LIST_Q.search(q or ""))


# ---------- quiet the meta words ----------
# A question often carries instructions to the assistant ("go check your memory using Antechamber hahaha") that
# are not the thing to find; searched literally they pull notes about the recall system into a question about
# something else. Strip those phrases (and laughter, emoji) before searching; fall back to the original if
# too little is left.
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]+")
META_PATTERNS = [
    r"\b(?:go |please |can you |could you |pls )?(?:check|search|look (?:it )?up|dig (?:into|through)|scan)\b(?: (?:in|into|through))? (?:your |my |our |the )?(?:memory|memories|records?|archives?|journal|history)\b",
    r"\b(?:using|with|via|through|in) (?:the )?(?:antechamber|recall|your memory|memory|the archive)\b",
    r"\b(?:do|did|can|could) you (?:still )?(?:remember|recall)\b",
    r"\bremember (?:when|that|this)\b(?=)",
    r"\b(?=[ha]*h)(?=[ha]*a)[ha]{4,}\b|\bhe(?:he)+\b|\blo+l\b|\blmao+\b|\bwk(?:wk)+\b",   # any keyboard-smash laugh: HAHAHHAHA
]
_META_RE = [re.compile(p, re.I) for p in META_PATTERNS]
def clean_query(q):
    """The question with instructions-to-me and laughter removed; the original if too little is left."""
    c = q
    for r in _META_RE:
        c = r.sub(" ", c)
    c = _EMOJI_RE.sub(" ", c)
    if c == q:
        return q                          # nothing meta found: the question is searched exactly as asked
    c = re.sub(r"\s+", " ", c).strip(" ?.!,")
    return c if len(re.findall(r"[A-Za-z]{3,}", c)) >= 2 else q
