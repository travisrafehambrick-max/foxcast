"""The research-claims section: what the research step submitted, or why there is none.

Stage 1B skeleton. Until a research backend exists, a dossier states plainly that no
research step ran (the honest-unavailable default). When claims do reach the builder
(today only through the tool server), the section accounts for every one: shown in which
dossier section, or withheld and listed in section 18. It never shows the model's words.
"""

import json
from dataclasses import replace

import pytest

from foxcast.dossier.output import render_json, render_markdown
from foxcast.dossier.schema import (
    CLAIM_TARGETS,
    NO_RESEARCH,
    ResearchClaims,
    ResearchStatus,
    SilentGapError,
    TargetClaims,
    no_research,
)

from .test_dossier import MODEL, World, no_llm, revenue_claim, with_claims


def all_targets(**changes: TargetClaims) -> tuple[TargetClaims, ...]:
    return tuple(changes.get(target, TargetClaims(target)) for target in CLAIM_TARGETS)


# --- the schema ---


def test_the_default_is_honestly_unavailable() -> None:
    research = no_research()
    assert research.status is ResearchStatus.UNAVAILABLE
    assert research.reason == NO_RESEARCH
    assert research.model is None
    assert research.targets == ()
    assert research.shown_count == research.withheld_count == 0


def test_an_unavailable_section_must_state_a_reason() -> None:
    with pytest.raises(SilentGapError):
        ResearchClaims(ResearchStatus.UNAVAILABLE)
    with pytest.raises(SilentGapError):
        ResearchClaims(ResearchStatus.UNAVAILABLE, reason="   ")


def test_an_unavailable_section_carries_no_claims_and_no_model() -> None:
    with pytest.raises(ValueError, match="unavailable"):
        ResearchClaims(ResearchStatus.UNAVAILABLE, reason="none ran", model="Opus")
    with pytest.raises(ValueError, match="unavailable"):
        ResearchClaims(ResearchStatus.UNAVAILABLE, reason="none ran", targets=all_targets())
    with pytest.raises(ValueError, match="unavailable"):
        ResearchClaims(ResearchStatus.UNAVAILABLE, reason="none ran", withheld_elsewhere=1)


def test_a_run_names_its_model_and_takes_no_reason() -> None:
    with pytest.raises(ValueError, match="model"):
        ResearchClaims(ResearchStatus.RAN, targets=all_targets())
    with pytest.raises(ValueError, match="reason"):
        ResearchClaims(ResearchStatus.RAN, reason="x", model="Opus", targets=all_targets())


def test_a_run_lists_every_claim_target_once_in_order() -> None:
    with pytest.raises(ValueError, match="claim target"):
        ResearchClaims(ResearchStatus.RAN, model="Opus", targets=all_targets()[1:])
    with pytest.raises(ValueError, match="claim target"):
        ResearchClaims(ResearchStatus.RAN, model="Opus", targets=all_targets()[::-1])


def test_a_target_must_be_a_claim_section() -> None:
    with pytest.raises(ValueError, match="claim target"):
        TargetClaims("header")
    with pytest.raises(ValueError, match="negative"):
        TargetClaims("business", withheld=-1)


def test_a_target_knows_its_dossier_section() -> None:
    assert TargetClaims("business").section_number == 3
    assert TargetClaims("bull_case").section_number == 13
    assert TargetClaims("bear_case").section_number == 13


# --- the dossier ---


@pytest.mark.integration
def test_a_dossier_built_without_a_model_says_research_is_unavailable(world: World) -> None:
    dossier = no_llm(world)
    assert dossier.research == no_research()
    markdown = render_markdown(dossier)
    assert markdown.count("**Research claims.**") == 1
    assert "**Research claims.** Unavailable: no research step ran for this dossier" in markdown
    assert markdown.count("\n## ") == 19  # not a numbered section: the brief keeps 19
    data = json.loads(render_json(dossier))["research_claims"]
    assert data == {
        "status": "unavailable",
        "reason": NO_RESEARCH,
        "model": None,
        "model_self_reported": False,
        "targets": [],
        "withheld_elsewhere": 0,
    }


@pytest.mark.integration
def test_a_run_accounts_for_every_claim_it_submitted(world: World) -> None:
    shown = revenue_claim(world)
    typed = replace(shown, section="business", text="Revenue was 987 billion.")
    misplaced = replace(shown, section="header")
    dossier = with_claims(world, [shown, typed, misplaced])
    research = dossier.research
    assert research.status is ResearchStatus.RAN
    assert research.model == MODEL.name
    by_target = {t.target: t for t in research.targets}
    assert [t.target for t in research.targets] == list(CLAIM_TARGETS)
    assert by_target["financial_health"].shown == tuple(sorted(dossier.verified_claim_ids))
    assert by_target["business"].withheld == 1
    assert research.withheld_elsewhere == 1  # named a section that takes no claims
    assert research.shown_count == 1
    assert research.withheld_count == len(dossier.withheld) == 2

    markdown = render_markdown(dossier)
    assert "987 billion" not in markdown  # a withheld claim's words never reach the dossier
    assert (
        "**Research claims.** Written by the research step\\: model Claude Opus 5\\.5 "
        "\\(self-reported\\); 1 claim shown in its section below, 2 withheld and listed "
        "in section 18\\." in markdown
    )
    (claim_id,) = dossier.verified_claim_ids
    assert f"| financial\\_health | 6\\. Financial health | 1 | 0 | `{claim_id}` |" in markdown
    assert "| business | 3\\. The business | 0 | 1 | none |" in markdown
    assert "1 claim named a section that takes no claims" in markdown

    data = json.loads(render_json(dossier))["research_claims"]
    assert data["status"] == "ran"
    assert data["model"] == MODEL.name and data["model_self_reported"] is True
    assert data["withheld_elsewhere"] == 1
    health = next(t for t in data["targets"] if t["target"] == "financial_health")
    assert health == {"target": "financial_health", "section": 6, "shown": [claim_id],
                      "withheld": 0}  # fmt: skip


@pytest.mark.integration
def test_a_run_that_submitted_nothing_says_so(world: World) -> None:
    dossier = with_claims(world, [])
    assert dossier.research.status is ResearchStatus.RAN
    assert dossier.research.shown_count == dossier.research.withheld_count == 0
    assert "the research step submitted no claims" in render_markdown(dossier)


@pytest.mark.integration
def test_a_dossier_refuses_a_research_section_that_disagrees_with_it(world: World) -> None:
    ran = with_claims(world, [revenue_claim(world)])
    with pytest.raises(ValueError, match="research"):
        replace(ran, research=no_research())  # hides the claims it shows
    with pytest.raises(ValueError, match="research"):
        replace(ran, research=replace(ran.research, model="Some other model"))
    emptied = tuple(replace(t, shown=()) for t in ran.research.targets)
    with pytest.raises(ValueError, match="research"):
        replace(ran, research=replace(ran.research, targets=emptied))
    with pytest.raises(ValueError, match="research"):
        replace(ran, research=replace(ran.research, withheld_elsewhere=3))
    plain = no_llm(world)
    with pytest.raises(ValueError, match="research"):
        replace(plain, research=ran.research)  # claims a run that did not happen


@pytest.mark.integration
def test_a_claim_listed_under_the_wrong_target_is_refused(world: World) -> None:
    ran = with_claims(world, [revenue_claim(world)])
    (claim_id,) = ran.verified_claim_ids
    moved = all_targets(business=TargetClaims("business", shown=(claim_id,)))
    with pytest.raises(ValueError, match="research"):
        replace(ran, research=replace(ran.research, targets=moved))


@pytest.mark.integration
def test_a_claim_submitted_twice_is_listed_once(world: World) -> None:
    claim = revenue_claim(world)
    dossier = with_claims(world, [claim, claim])
    (claim_id,) = dossier.verified_claim_ids
    health = next(t for t in dossier.research.targets if t.target == "financial_health")
    assert health.shown == (claim_id,)


@pytest.mark.integration
def test_bull_and_bear_claims_are_listed_apart_in_section_13(world: World) -> None:
    claim = revenue_claim(world)
    bull, bear = replace(claim, section="bull_case"), replace(claim, section="bear_case")
    elsewhere = [replace(claim, section=s) for s in ("header", "forecast")]
    dossier = with_claims(world, [bull, bear, *elsewhere])
    by_target = {t.target: t for t in dossier.research.targets}
    assert len(by_target["bull_case"].shown) == len(by_target["bear_case"].shown) == 1
    assert by_target["bull_case"].shown != by_target["bear_case"].shown
    markdown = render_markdown(dossier)
    assert "| bull\\_case | 13\\. Bull case, bear case | 1 | 0 |" in markdown
    assert "| bear\\_case | 13\\. Bull case, bear case | 1 | 0 |" in markdown
    assert "2 claims named a section that takes no claims and were withheld" in markdown
