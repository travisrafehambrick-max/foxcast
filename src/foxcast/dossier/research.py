"""Build the research-claims section from the claim check (Stage 1B skeleton).

The section is unavailable unless a research step ran. No research backend is wired in
yet, so a dossier built by the CLI says so; claims submitted through the tool server are
accounted for here, each shown (by ID, in its target) or counted as withheld.
"""

from collections.abc import Sequence

from foxcast.claims.checker import DossierCheck
from foxcast.dossier.schema import (
    CLAIM_TARGETS,
    ResearchClaims,
    ResearchStatus,
    TargetClaims,
    WithheldEntry,
    no_research,
)


def research_claims(
    model: str | None, check: DossierCheck | None, withheld: Sequence[WithheldEntry]
) -> ResearchClaims:
    """The research section for a dossier: ``check`` is ``None`` when no research step ran."""
    if check is None:
        return no_research()
    if model is None:
        raise ValueError("claims need the model that wrote them to be declared")
    targets = tuple(
        TargetClaims(
            target,
            # An identical claim submitted twice has one ID; list it once.
            shown=tuple(dict.fromkeys(c.claim_id for c in check.shown if c.section == target)),
            withheld=sum(1 for w in withheld if w.section == target),
        )
        for target in CLAIM_TARGETS
    )
    elsewhere = sum(1 for w in withheld if w.section not in CLAIM_TARGETS)
    return ResearchClaims(
        ResearchStatus.RAN, model=model, targets=targets, withheld_elsewhere=elsewhere
    )
