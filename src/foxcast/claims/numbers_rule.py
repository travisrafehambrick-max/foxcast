"""The numbers rule: what the model may write in its own words (Stage 1A.4; row 1b).

The model never writes a figure or a quote. A claim's text is a template: the model's own
words, with placeholders such as ``{f_0123456789abcdef}`` (a fact) or ``{p_...}`` (a
passage) where code inserts the rendered value or the archived passage.

The rule denies by default:

1. **Characters.** Outside placeholders the model may use only ASCII letters, spaces and
   ``. , ; : ( ) - & /`` plus an apostrophe inside or closing a word. Every other script,
   look-alike letter, combining mark, quotation mark, digit-like symbol, line break, tab,
   ``$``, ``%`` and ``#`` is refused, so a number cannot be spelled in another alphabet and
   a quote cannot be typed at all.
2. **Digits** appear only inside strict name patterns: full dates (``October 28, 2026``,
   ``2026-10-28``), periods (``Q3``, ``H2``, ``FY2025``, ``Q3 2025``, ``fiscal 2025``, and a
   year after ``in``, ``of``, ``since``, ``during``, ``for``, ``through``, ``until``,
   ``from`` or ``to``), SEC form and item names, index names (``S&P 500``) and a few rule
   names (``10b5-1``). A bare number is never allowed. The words check of task 1A.5 is the
   backstop: every period a claim names must be carried by a cited fact.
3. **Number words** ("twelve", "billion", "several", "a fifth", "double-digit", Roman
   numerals) are refused.
4. **Quantity words** ("doubled", "half", "a third", "twice") must match a derived fact
   the claim places: "doubled" needs placed growth near +100%, "half" a placed ratio near
   0.5 or growth near -50%, and so on (change BC).
5. **Links** of any form, stray braces and over-long text are refused.

Each problem is returned as a sentence. A claim with any problem is withheld, never shown.

Known gaps, accepted after two adversarial reviews (2026-11-11) and left to the words
check of task 1A.5 and the human audit (row 1e): number words split by spaces ("t w o"),
a name token repeated as filler ("S&P 500 S&P 500"), the word "one", and "10-K" read as
ten thousand. Digit-bearing product names ("iPhone 16") are refused, at some cost to row
1c; that cost is measured on the gate dossiers.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from foxcast.facts.derived import Method

MAX_CLAIM_CHARS: Final = 600
PLACEHOLDER: Final = re.compile(r"\{([fp]_[0-9a-f]{16})\}")
ALLOWED_CHARACTERS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;:()-&/'"
)

MONTHS: Final = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
DAY: Final = r"(?:0?[1-9]|[12][0-9]|3[01])"
YEAR: Final = r"(?:199[0-9]|20[0-4][0-9])"
YEAR_CONTEXT: Final = r"(?:in|since|during|fiscal|calendar|year)"
DATE_CONTEXT: Final = r"(?:on|ended|ending|through|as of|since|until|by|before|after)"
FORMS: Final = (
    r"10-K|10-Q|8-K|20-F|40-F|6-K|11-K|S-1|S-3|S-4|S-8|F-1|F-3|13F|SC 13D|SC 13G|"
    r"Schedule 13D|Schedule 13G|DEF 14A|424B[1-8]"
)
ALLOWED_TOKENS: Final = re.compile(
    "|".join(
        [
            rf"\b{YEAR}-(?:0[1-9]|1[0-2])-{DAY}\b",  # 2026-10-28
            rf"\b(?:{MONTHS}) {DAY}, {YEAR}\b",  # October 28, 2026
            rf"\b{DATE_CONTEXT} (?:{MONTHS}) {DAY}(?:, {YEAR})?\b",  # on October 28
            rf"\b(?:Q[1-4]|H[12]) {YEAR}\b",  # Q3 2025
            r"\bQ[1-4]\b",
            r"\bH[12]\b",
            rf"\bFY ?{YEAR}\b",
            rf"\b{YEAR_CONTEXT} {YEAR}\b",  # in 2025, since 2019, fiscal 2025
            rf"\bfrom {YEAR} (?:to|through) {YEAR}\b",  # a whole range only
            rf"\b{YEAR} (?:{FORMS}|annual report|proxy statement|annual meeting)\b",
            rf"(?<![\w-])(?:{FORMS})(?:/A)?(?![\w-])",
            r"\bForm [345]\b",
            r"\bItem (?:1A|1B|1C|7A|9A|9B|9C|[1-9](?:\.0[1-9]|\.[1-9][0-9])?|1[0-6])\b",
            r"\bSection 16\b",
            r"\bS&P (?:400|500|600)\b",
            r"\bRussell (?:1000|2000|3000)\b",
            r"\bNasdaq-100\b",
            r"\b10b5-1\b",
            r"\bRule (?:10b-5|144A?)\b",
            r"\bCOVID-19\b",
        ]
    ),
    re.IGNORECASE,
)
NUMBER_WORDS: Final = re.compile(
    r"\b(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|trillions?|"
    r"dozens?|percent|per cent|percentages?|basis points?|bps|bn|mm|once|several|couple|"
    r"handful|myriad|lakhs?|crores?|tens of|scores of|fifths?|sixths?|sevenths?|eighths?|"
    r"ninths?|tenths?|elevenths?|twelfths?|[a-z]+teenths?|[a-z]+ieths?|hundredths?|"
    r"thousandths?|millionths?|thirds|quarters of|digits?|teens|twenties|thirties|forties|"
    r"fifties|sixties|seventies|eighties|nineties|decades?|century|centuries|pair of|trio|"
    r"duo|umpteen|nil|penny|pennies|nickel|"
    r"third(?![ -](?:quarter|part(?:y|ies)))|fourth(?![ -]quarter))\b",
    re.IGNORECASE,
)
# Only I, V and X: tickers such as MMM, MCD, CI and CL are not Roman numerals here.
ROMAN_NUMERAL: Final = re.compile(r"\b(?=[IVX]{2,}\b)(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\b")
INNER_HYPHEN: Final = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")
# A year used as an amount: "2040 dollars", "2000 employees".
YEAR_AS_AMOUNT: Final = re.compile(
    rf"\b{YEAR} (?:dollars?|cents?|euros?|pounds?|yen|shares?|units?|employees|people|"
    r"stores|customers|users|subscribers|times)\b",
    re.IGNORECASE,
)
YEAR_RANGE: Final = re.compile(rf"\bfrom ({YEAR}) (?:to|through) ({YEAR})\b", re.IGNORECASE)
MAX_RANGE_YEARS: Final = 25
# "first half" and "second half" are periods, not quantities.
PERIOD_HALVES: Final = re.compile(r"\b(?:first|second) half\b", re.IGNORECASE)
# Company names that contain a dot and would otherwise read as addresses.
DOTTED_NAMES: Final = re.compile(r"\b(?:Amazon\.com|Booking\.com|Hotels\.com)\b")
LINK: Final = re.compile(
    r"://|\bwww\b|\[\.\]|\bdot [a-z]{2,}\b|[A-Za-z]{2,}\.[A-Za-z]{2,}|"
    r"\b[a-z]\.[a-z]{2,}\b|\b[a-z][a-z0-9+.-]*:(?=[^\s])",
    re.IGNORECASE,
)
# Apostrophes only in English contractions and possessives: it's, don't, they're,
# company's, companies'.
APOSTROPHE: Final = re.compile(
    r"(?<=[A-Za-z])'(?=(?:s|t|re|ve|ll|d|m)\b)|(?<=s)'(?=[\s.,;:)]|$)", re.IGNORECASE
)
TWO_APOSTROPHES: Final = re.compile(r"[A-Za-z]*'[A-Za-z]*'")


@dataclass(frozen=True)
class QuantityWord:
    pattern: re.Pattern[str]
    methods: frozenset[Method]
    targets: tuple[float, ...]  # the value a placed derived fact must be near


def _q(words: str, methods: set[Method], *targets: float) -> QuantityWord:
    return QuantityWord(re.compile(rf"\b(?:{words})\b", re.I), frozenset(methods), targets)


GROWTH, RATIO = Method.GROWTH, Method.RATIO
QUANTITY_WORDS: Final = (
    _q("doubl(?:e|ed|es|ing)|twofold", {GROWTH}, 1.0),
    _q("tripl(?:e|ed|es|ing)|threefold", {GROWTH}, 2.0),
    _q("quadrupl(?:e|ed|es|ing)|fourfold", {GROWTH}, 3.0),
    _q("halv(?:e|ed|es|ing)", {GROWTH}, -0.5),
    _q("half|halves", {RATIO, GROWTH}, 0.5, -0.5),
    _q("a third of|one-third", {RATIO}, 1 / 3),
    _q("two-thirds", {RATIO}, 2 / 3),
    _q("a quarter of|one-quarter", {RATIO}, 0.25),
    _q("three-quarters", {RATIO}, 0.75),
    _q("twice", {RATIO, GROWTH}, 2.0, 1.0),
    _q("thrice", {RATIO, GROWTH}, 3.0, 2.0),
    _q(r"\w*fold", {GROWTH, RATIO}),  # any other "-fold": never matched by a target
)
QUANTITY_TOLERANCE: Final = 0.10  # relative: "doubled" covers growth of +90% to +110%


def placeholders(text: str) -> list[str]:
    """The fact and passage IDs a template places, in order (repeats kept)."""
    return PLACEHOLDER.findall(text)


def _near(value: float, target: float) -> bool:
    return abs(value - target) <= QUANTITY_TOLERANCE * abs(target)


def _quantity_problems(words: str, placed: Sequence[tuple[Method, float]]) -> tuple[list[str], str]:
    """Problems with quantity words, and the words with those phrases taken out."""
    problems = []
    seen = PERIOD_HALVES.sub(" ", words)
    for quantity in QUANTITY_WORDS:
        match = quantity.pattern.search(seen)
        if match is None:
            continue
        seen = quantity.pattern.sub(" ", seen)
        if not any(
            method in quantity.methods and any(_near(value, t) for t in quantity.targets)
            for method, value in placed
        ):
            problems.append(
                f"the quantity word {match.group(0)!r} needs a placed derived fact that shows it"
            )
    return problems, seen


def _character_problems(words: str) -> list[str]:
    bad = sorted({c for c in words if c not in ALLOWED_CHARACTERS})
    problems = []
    if bad:
        shown = ", ".join(f"U+{ord(c):04X}" for c in bad[:6])
        problems.append(
            f"the claim uses characters outside plain letters and punctuation ({shown}); "
            "numbers, quotes, symbols and line breaks come only from cited IDs"
        )
    if "'" in words and (APOSTROPHE.sub("", words).count("'") or TWO_APOSTROPHES.search(words)):
        problems.append("the claim contains a quote mark; quote a passage by its ID instead")
    return problems


def check_model_text(text: str, *, placed_derived: Sequence[tuple[Method, float]]) -> list[str]:
    """Problems with the model's words; an empty list means the template may be rendered.

    ``placed_derived`` lists the method and value of each derived fact the template places.
    """
    problems = []
    if len(text) > MAX_CLAIM_CHARS:
        problems.append(f"the claim is too long (over {MAX_CLAIM_CHARS} characters)")
    words = PLACEHOLDER.sub(" ", text)
    if "{" in words or "}" in words:
        problems.append("a brace is not a valid placeholder such as {f_0123456789abcdef}")
    problems.extend(_character_problems(words))
    if LINK.search(DOTTED_NAMES.sub(" ", words)):
        problems.append("the claim contains a link or address; sources are cited by ID only")
    if YEAR_AS_AMOUNT.search(words) or any(
        not 0 < int(end) - int(start) <= MAX_RANGE_YEARS for start, end in YEAR_RANGE.findall(words)
    ):
        problems.append("a year is used as an amount; cite a fact ID and let code render it")
    names_removed = ALLOWED_TOKENS.sub(" ", words)
    if any(c.isdigit() for c in names_removed):
        problems.append("the model typed a number; cite a fact ID and let code render it")
    quantity_problems, plain_words = _quantity_problems(names_removed, placed_derived)
    problems.extend(quantity_problems)
    joined = INNER_HYPHEN.sub("", plain_words)  # "twen-ty" is still twenty
    if NUMBER_WORDS.search(plain_words) or NUMBER_WORDS.search(joined):
        problems.append("the claim uses a number word; cite a fact ID instead")
    numerals = [m.group(0) for m in ROMAN_NUMERAL.finditer(plain_words)]
    if any(numerals):
        problems.append("the claim uses a Roman numeral; cite a fact ID instead")
    return problems
