"""Does every number in an answer come from something the model saw? (M7 plan 5.2.)

The chat model sees the tools' summaries, the questions, its system prompt and the tool
definitions, so those are the evidence. Every number in the answer is looked up among the
numbers in the evidence:

- **grounded**: it is one of them;
- **rounded**: one of them rounds to it at the answer's precision (0.79 -> 0.8, half up or half
  to even), or is a fraction written as a percentage (0.44 -> 44%);
- **derived**: it is the sum or difference of two numbers from the same tool summary or question,
  within rounding (a gap worked out from two lap times; a number doubles only when the text has
  it twice). Listed for a human look, not a failure;
- **ungrounded**: none of these. One ungrounded number fails the question.

Numbers are found by one tokenizer, run alike on the evidence and the answers: digits with an
optional decimal part, or groups of three after commas ("13,945"), with an optional "%". A sign
(-, +, U+2212) belongs to the number only when the character before it isn't a letter or
digit, so "2026-07-05" gives 2026, 7 and 5. A letter prefix is dropped ("T5" -> 5, "P3" -> 3,
"Q2" -> 2). Numbers are compared by absolute value (a source's "+0.3" grounds "0.3 s slower")
and as decimals (05 = 5, 0.80 = 0.8), and are looked up as whole tokens, never as substrings,
so "0.37" doesn't ground "3" or "37". A list marker at the start of a line ("4. ") is not a
number. Numbers written as words ("five corners") aren't checked. The integers 0 to 3 are
always allowed.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Literal

Kind = Literal["grounded", "rounded", "derived", "ungrounded"]
KINDS: tuple[Kind, ...] = ("grounded", "rounded", "derived", "ungrounded")

MINUS = "\u2212"
NUMBER = re.compile(
    rf"(?P<sign>[-+{MINUS}])?"
    r"(?P<num>[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
    r"(?P<pct>\s?%)?"
)
LIST_MARKER = re.compile(r"^[ \t]*([0-9]{1,2})[.)][ \t]", re.MULTILINE)
ALWAYS = tuple(Decimal(n) for n in range(4))  # 0-3: "two drivers", "one lap", "Q3"
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")


@dataclass(frozen=True)
class Number:
    """One number as written: `value` is its absolute value, `places` its decimals."""

    text: str
    value: Decimal
    places: int
    percent: bool
    start: int
    end: int


def numbers(text: str) -> list[Number]:
    """The numbers in `text`, in order (see the module docstring for the rules)."""
    markers = {m.start(1) for m in LIST_MARKER.finditer(text)}
    found = []
    for m in NUMBER.finditer(text):
        if m.start("num") in markers:
            continue
        start = m.start()
        sign = m.group("sign") or ""
        if sign and start > 0 and text[start - 1].isalnum():  # "2026-07", "T-5": not a sign
            start, sign = m.start("num"), ""
        digits = m.group("num")
        places = len(digits.split(".")[1]) if "." in digits else 0
        found.append(
            Number(
                text=text[start : m.end()],
                value=Decimal(digits.replace(",", "")),
                places=places,
                percent=m.group("pct") is not None,
                start=start,
                end=m.end(),
            )
        )
    return found


def _rounds(x: Decimal, places: int, target: Decimal) -> bool:
    step = Decimal(1).scaleb(-places)
    return any(
        x.quantize(step, rounding=mode) == target for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN)
    )


def _between(values: list[Decimal], lo: Decimal, hi: Decimal) -> list[Decimal]:
    return values[bisect_left(values, lo) : bisect_right(values, hi)]


@dataclass
class Evidence:
    """The numbers the model could have read. `add` records a text's numbers; texts added with
    `derive=True` (tool summaries and questions) also serve sums and differences, pair by pair
    within one text."""

    values: set[Decimal] = field(default_factory=lambda: set(ALWAYS))
    # Per text, sorted, repeats kept (for derived: 0.65 + 0.65 needs 0.65 written twice).
    texts: list[list[Decimal]] = field(default_factory=list)
    _sorted: list[Decimal] | None = field(default=None, repr=False)

    def add(self, text: str, *, derive: bool = True) -> None:
        found = sorted(n.value for n in numbers(text))
        if not found:
            return
        self.values.update(found)
        self._sorted = None
        if derive:
            self.texts.append(found)

    def copy(self) -> Evidence:
        return Evidence(set(self.values), list(self.texts))

    @property
    def sorted(self) -> list[Decimal]:
        if self._sorted is None:
            self._sorted = sorted(self.values)
        return self._sorted

    def classify(self, number: Number) -> tuple[Kind, str]:
        """How `number` is accounted for, and from what ("0.79 rounded", "1.2 + 0.3")."""
        v, places = number.value, number.places
        if v in self.values:
            return "grounded", ""
        half = Decimal(5).scaleb(-(places + 1))
        for s in _between(self.sorted, v - half, v + half):
            if _rounds(s, places, v):
                return "rounded", f"from {s}"
        if number.percent:
            for s in _between(self.sorted, (v - half) / 100, (v + half) / 100):
                if _rounds(s * 100, places, v):
                    return "rounded", f"from {s} as a percentage"
        for values in self.texts:
            via = _derived(values, v, places, half)
            if via:
                return "derived", via
        return "ungrounded", ""


def _derived(values: list[Decimal], v: Decimal, places: int, half: Decimal) -> str:
    """How two entries of `values` (zeros left out; never one entry twice) make `v` within
    rounding, as "a + b" or "a - b"; "" when none do."""
    for i, a in enumerate(values):
        if a == 0:
            continue
        for lo, hi, op in (
            (v - a - half, v - a + half, "+"),  # a + b = v
            (a - v - half, a - v + half, "-"),  # a - b = v
            (a + v - half, a + v + half, "-r"),  # b - a = v
        ):
            for j in range(bisect_left(values, lo), bisect_right(values, hi)):
                b = values[j]
                if j == i or b == 0:
                    continue
                if op == "+" and _rounds(a + b, places, v):
                    return f"{a} + {b}"
                if op != "+" and _rounds(abs(a - b), places, v):
                    return f"{a} - {b}" if op == "-" else f"{b} - {a}"
    return ""


@dataclass(frozen=True)
class Verdict:
    number: Number
    kind: Kind
    via: str
    sentence: str


@dataclass(frozen=True)
class Grounding:
    """Every number of an answer with its verdict."""

    verdicts: tuple[Verdict, ...]

    def count(self, kind: Kind) -> int:
        return sum(1 for v in self.verdicts if v.kind == kind)

    @property
    def passed(self) -> bool:
        return self.count("ungrounded") == 0

    def flagged(self) -> Iterator[Verdict]:
        """The ungrounded and derived numbers, for the report."""
        return (v for v in self.verdicts if v.kind in ("ungrounded", "derived"))


def sentence_at(text: str, start: int, end: int, limit: int = 240) -> str:
    """The sentence of `text` around [start, end), on one line, cut to about `limit`
    characters around the number."""
    bounds = [0, *(m.end() for m in SENTENCE_END.finditer(text)), len(text)]
    left = max(b for b in bounds if b <= start)
    right = min((b for b in bounds if b >= end), default=len(text))
    sentence = " ".join(text[left:right].split())
    if len(sentence) <= limit:
        return sentence
    at = sentence.find(text[start:end])
    lo = max(0, at - limit // 2)
    return ("…" if lo else "") + sentence[lo : lo + limit].strip() + "…"


def ground(answer: str, evidence: Evidence) -> Grounding:
    """Each number of `answer` looked up in `evidence`."""
    verdicts = []
    for n in numbers(answer):
        kind, via = evidence.classify(n)
        verdicts.append(Verdict(n, kind, via, sentence_at(answer, n.start, n.end)))
    return Grounding(tuple(verdicts))


def evidence_from(texts: Iterable[str], *, derive: bool = False) -> Evidence:
    """Evidence from fixed texts (the system prompt, the tool definitions)."""
    evidence = Evidence()
    for text in texts:
        evidence.add(text, derive=derive)
    return evidence
