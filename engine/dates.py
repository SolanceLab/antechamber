# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Dates as a person reads them: day labels and relative-time notes ("yesterday = Sunday 7 May 2023")."""
import re


_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "couple of": 2}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_REL = re.compile(
    r"\b(yesterday|today|tonight|this morning|this afternoon|this evening|last night|tomorrow|"
    r"(?:last|this|next) (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|month|year)|"
    r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple of) (?:day|week|month|year)s? ago|"
    r"(?:in|after) (?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple of) (?:day|week|month|year)s?)\b", re.I)
def resolve_relative(text, said):
    """['yesterday = Sunday 7 May 2023', …] for the clear relative times in `text`, counted from `said`
    (a date or 'YYYY-MM-DD…'). Vague phrases are never resolved — no note beats a guessed date."""
    import datetime
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(said or ""))
    if not m or not text:
        return []
    try:
        d0 = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return []
    fmt = lambda d: d.strftime("%A %-d %B %Y")
    month_of = lambda d: d.strftime("%B %Y")
    def add_months(d, k):
        y, mo = divmod(d.month - 1 + k, 12)
        return d.replace(year=d.year + y, month=mo + 1, day=1)
    notes, seen = [], set()
    for hit in _REL.finditer(text):
        p = hit.group(1).lower()
        if p in seen:
            continue
        seen.add(p)
        val = None
        if p in ("today", "tonight", "this morning", "this afternoon", "this evening"):
            val = fmt(d0)
        elif p in ("yesterday", "last night"):
            val = fmt(d0 - datetime.timedelta(days=1))
        elif p == "tomorrow":
            val = fmt(d0 + datetime.timedelta(days=1))
        else:
            w = p.split()
            if w[0] in ("last", "this", "next") and w[1] in _WEEKDAYS:
                t, cur = _WEEKDAYS.index(w[1]), d0.weekday()
                if w[0] == "last":
                    back = (cur - t) % 7 or 7
                    val = fmt(d0 - datetime.timedelta(days=back))
                elif w[0] == "next":
                    fwd = (t - cur) % 7 or 7
                    val = fmt(d0 + datetime.timedelta(days=fwd))
                else:
                    val = fmt(d0 + datetime.timedelta(days=t - cur))
            elif w[0] in ("last", "this", "next") and w[1] in ("week", "weekend", "month", "year"):
                k = {"last": -1, "this": 0, "next": 1}[w[0]]
                if w[1] == "week":
                    start = d0 - datetime.timedelta(days=d0.weekday()) + datetime.timedelta(weeks=k)
                    val = "the week of " + fmt(start)
                elif w[1] == "weekend":
                    sat = d0 + datetime.timedelta(days=(5 - d0.weekday())) + datetime.timedelta(weeks=k)
                    val = "the weekend of " + fmt(sat)
                elif w[1] == "month":
                    val = month_of(add_months(d0, k))
                else:
                    val = str(d0.year + k)
            else:
                mm = re.match(r"(?:in|after) (.+?) (day|week|month|year)s?$", p) or re.match(r"(.+?) (day|week|month|year)s? ago$", p)
                if mm:
                    num = mm.group(1)
                    k = int(num) if num.isdigit() else _NUM.get(num)
                    if k:
                        sign = 1 if p.startswith(("in ", "after ")) else -1
                        unit = mm.group(2)
                        if unit == "day":
                            val = fmt(d0 + datetime.timedelta(days=sign * k))
                        elif unit == "week":
                            val = "around " + fmt(d0 + datetime.timedelta(weeks=sign * k))
                        elif unit == "month":
                            val = "around " + month_of(add_months(d0, sign * k))
                        else:
                            val = "around " + str(d0.year + sign * k)
        if val:
            notes.append(f"{hit.group(1)} = {val}")
    return notes


def day_label(d):
    """'2026-10-02...' -> 'Friday 2 October 2026'; anything else -> None."""
    import datetime
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m[1]), int(m[2]), int(m[3])).strftime("%A %-d %B %Y")
    except ValueError:
        return None


_day = day_label
