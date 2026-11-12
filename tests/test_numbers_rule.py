"""The numbers rule on the model's own words (Stage 1A.4; brief changes BC, R; row 1b).

The refused cases include every bypass the independent review of 1A.4 reproduced.
"""

import pytest

from foxcast.claims.numbers_rule import MAX_CLAIM_CHARS, check_model_text, placeholders
from foxcast.facts.derived import Method

F1 = "{f_0123456789abcdef}"
P1 = "{p_fedcba9876543210}"


def problems_with(text: str, placed: list[tuple[Method, float]] | None = None) -> list[str]:
    return check_model_text(text, placed_derived=placed or [])


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        f"Revenue grew in Q3 to {F1}.",
        f"The 10-K for FY2025 reports {F1}; the 10-Q and 8-K/A agree.",
        f"In 2025 the company filed Form 4 reports; see Item 1A and Item 2.02, {P1}.",
        f"It is a member of the S&P 500 and reported on October 28, 2026 ({F1}).",
        f"Sales on 2026-10-28 rose under a Rule 10b5-1 plan in H2 {F1}.",
        f"Revenue in Q3 2025 and since 2019 rose; fiscal 2025 closed at {F1}.",
        "The company's auditor's opinion was unqualified.",
        "The companies' auditors agreed.",
        f"One of its segments reported {F1}.",
        f"The U.S. market and MD&A in Item 7 of the 10-K cover {F1}.",
        f"Revenue in the first half and the third quarter rose {F1}.",
        "Alphabet Inc. reported results.",
    ],
)
def test_ordinary_claims_pass(text: str) -> None:
    assert problems_with(text) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "why"),
    [
        # digits and symbols
        ("Revenue was 357 billion dollars.", "number"),
        ("Margins rose 3%.", "number"),
        ("Revenue was $1.2bn.", "characters"),
        ("Revenue was $2000.", "characters"),
        ("We had 2000 employees.", "number"),
        ("Revenue rose 2000% this year.", "characters"),
        ("Sales of $10-K in a year.", "characters"),
        ("Up $5G this year.", "characters"),
        ("May 12 units shipped.", "number"),
        ("Item 99 shows it.", "number"),
        ("Q35 revenue rose.", "number"),
        ("The 10-K12 says so.", "number"),
        ("In 20255 it rose.", "number"),
        ("Item 99.999 applies.", "number"),
        # other scripts, look-alikes and marks
        ("Revenue grew " + chr(0x0663) + " percent.", "characters"),
        ("Revenue grew " + chr(0xFF13) + "x.", "characters"),
        ("Revenue grew " + chr(0x00BD) + ".", "characters"),
        ("Revenue grew " + chr(0x4E09) + chr(0x767E) + chr(0x4E07) + ".", "characters"),
        ("Revenue grew " + chr(0x0442) + chr(0x0440) + chr(0x0438) + " times.", "characters"),
        ("Revenue grew " + chr(0xFF54) + chr(0xFF57) + chr(0xFF4F) + " times.", "characters"),
        ("Revenue grew tw" + chr(0x043E) + " times.", "characters"),  # Cyrillic o
        ("Revenue grew t" + chr(0x0301) + "wo times.", "characters"),  # combining mark
        ("Revenue grew two" + chr(0x00B2) + ".", "characters"),  # superscript
        ("Line one" + chr(10) + "# heading", "characters"),
        ("a" + chr(9) + "b", "characters"),
        ("Hidden" + chr(0x200B) + "text.", "characters"),
        # number words, ordinals, fractions and Roman numerals
        ("Revenue grew by twelve percent.", "number word"),
        ("Revenue was about a billion.", "number word"),
        ("It rose fifty basis points.", "number word"),
        ("Revenue grew twen-ty times.", "number word"),
        ("It happened once.", "number word"),
        ("Several plants closed.", "number word"),
        ("A couple of plants closed.", "number word"),
        ("A fifth of sales came from cloud.", "number word"),
        ("It is the tenth year in a row.", "number word"),
        ("The third plant closed.", "number word"),
        ("Growth was double-digit.", "number word"),
        ("Growth was high single-digits.", "number word"),
        ("The Apple III sold well.", "Roman"),
        ("Revenue grew XII times.", "Roman"),
        # quotes
        ('Management said "demand is strong".', "characters"),
        ("Management said " + chr(0x201C) + "demand" + chr(0x201D) + ".", "characters"),
        ("Management said " + chr(0x201F) + "demand.", "characters"),
        ("Management said " + chr(0xFF02) + "demand.", "characters"),
        ("Management said " + chr(0x300C) + "demand" + chr(0x300D) + ".", "characters"),
        ("Management said, 'demand is strong'.", "quote"),
        ("Management said:'hi there'.", "quote"),
        (F1 + "'hi'", "quote"),
        # links
        ("See https://example.com for more.", "link"),
        ("See www.example.org for more.", "link"),
        ("See example.com for more.", "link"),
        ("See evil.ru for more.", "link"),
        ("See ftp://x for more.", "link"),
        ("Run javascript:alert now.", "link"),
        ("See example[.]com now.", "link"),
        ("See example dot com now.", "link"),
        ("See t.me now.", "link"),
        # structure
        ("Revenue {f_0123} rose.", "placeholder"),
        ("Revenue {revenue} rose.", "placeholder"),
        ("x" * (MAX_CLAIM_CHARS + 1), "long"),
    ],
)
def test_model_written_numbers_quotes_and_links_are_refused(text: str, why: str) -> None:
    problems = problems_with(text)
    assert problems, text
    assert any(why in p for p in problems), problems


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "right", "wrong"),
    [
        (f"Revenue doubled to {F1}.", (Method.GROWTH, 1.02), (Method.GROWTH, 0.05)),
        (f"Costs halved {F1}.", (Method.GROWTH, -0.5), (Method.GROWTH, -0.1)),
        (f"Cloud was half of revenue {F1}.", (Method.RATIO, 0.49), (Method.RATIO, 0.3)),
        (f"A third of revenue came from cloud {F1}.", (Method.RATIO, 0.34), (Method.RATIO, 0.5)),
        (f"Two-thirds came from ads {F1}.", (Method.RATIO, 0.66), (Method.RATIO, 0.4)),
        (f"Profit was twice the prior year {F1}.", (Method.GROWTH, 1.0), (Method.GROWTH, 0.3)),
        (f"Bookings tripled {F1}.", (Method.GROWTH, 2.05), (Method.GROWTH, 1.0)),
        (f"Ads were three-quarters of sales {F1}.", (Method.RATIO, 0.75), (Method.RATIO, 0.5)),
    ],
)
def test_quantity_words_must_match_a_placed_derived_fact(
    text: str, right: tuple[Method, float], wrong: tuple[Method, float]
) -> None:
    assert any("quantity word" in p for p in problems_with(text))
    assert any("quantity word" in p for p in problems_with(text, [wrong]))
    assert problems_with(text, [right]) == []


@pytest.mark.unit
def test_an_unknown_fold_word_never_passes() -> None:
    assert problems_with(f"Sales rose manifold {F1}.", [(Method.GROWTH, 1.0)])


@pytest.mark.unit
def test_placeholders_are_listed_in_order() -> None:
    assert placeholders(f"a {F1} b {P1} c {F1}") == [
        "f_0123456789abcdef",
        "p_fedcba9876543210",
        "f_0123456789abcdef",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "Shares rose to 2045.",
        "The price fell from 1999 to 2040 dollars.",
        "Revenue of 2000 was reported.",
        "Margins in the mid-teens held.",
        "Growth in the low twenties held.",
        "It sold sixteenths of its stake.",
        "It took over a decade.",
        "A pair of plants closed.",
        "The fund said it'sgood'growth.",
        "See evil dot ru now.",
    ],
)
def test_second_review_bypasses_are_refused(text: str) -> None:
    assert problems_with(text), text


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        f"Third-party revenue grew {F1}.",
        f"A third party audits it {F1}.",
        f"Fourth-quarter results were {F1}.",
        f"MMM, MCD, CI and CL reported {F1}.",
        f"The quarter ended September 27 with {F1}.",
        f"It reported on October 28 that sales were {F1}.",
        f"The 2024 10-K and the 2025 annual report show {F1}.",
        f"An SC 13G and a Schedule 13D were filed; Section 16 filers sold {F1}.",
        f"Amazon.com grew {F1}.",
        f"From 2019 to 2025 revenue rose {F1}.",
        "It's the company's view; they're sure, and the companies' boards agree.",
    ],
)
def test_ordinary_filing_language_is_not_refused(text: str) -> None:
    assert problems_with(text) == [], text
