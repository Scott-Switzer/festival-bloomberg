"""Policy gate used by the acquisition router.

The gate maps a source platform to a :class:`RightsProfile` and evaluates it
for the request context. Sources absent from the registry fail closed.
"""

from __future__ import annotations

from ..governance.policy import (
    PolicyDecision,
    PolicyStatus,
    RightsProfile,
    evaluate,
    unknown_profile,
)


def default_policy_profiles() -> dict[str, RightsProfile]:
    """Best-effort profiles for platforms the Signal Fabric may acquire from.

    These are research-stage summaries, not legal opinions. Each profile is
    explicitly marked where a human legal review is still required; in
    commercial mode those sources are denied until their status changes.
    """
    approved = PolicyStatus.APPROVED
    conditional = PolicyStatus.APPROVED_WITH_CONDITIONS
    review = PolicyStatus.LEGAL_REVIEW_REQUIRED
    research = PolicyStatus.RESEARCH_ONLY

    def profile(
        source_id: str,
        *,
        content: PolicyStatus,
        api: PolicyStatus,
        scraping: PolicyStatus = PolicyStatus.PROHIBITED,
        storage: PolicyStatus = PolicyStatus.PROHIBITED,
        derivative: PolicyStatus = PolicyStatus.PROHIBITED,
        redistribution: PolicyStatus = PolicyStatus.PROHIBITED,
        commercial: PolicyStatus = PolicyStatus.PROHIBITED,
        notes: str = "",
    ) -> RightsProfile:
        return RightsProfile(
            source_id=source_id,
            content_license=content,
            api_access_rights=api,
            scraping_rights=scraping,
            storage_rights=storage,
            derivative_analytics_rights=derivative,
            redistribution_rights=redistribution,
            commercial_product_rights=commercial,
            notes=notes,
        )

    return {
        "wikidata": profile(
            "wikidata", content=approved, api=approved, storage=approved,
            derivative=approved, redistribution=approved, commercial=approved,
            notes="CC0; no attribution required",
        ),
        "wikimedia": profile(
            "wikimedia", content=conditional, api=conditional, storage=conditional,
            derivative=conditional, redistribution=conditional, commercial=conditional,
            notes="pageview data; license varies by project; attribution required",
        ),
        "gdelt": profile(
            "gdelt", content=conditional, api=conditional, storage=conditional,
            derivative=conditional, redistribution=conditional, commercial=conditional,
            notes="CC BY 4.0; attribution required",
        ),
        "youtube": profile(
            "youtube", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="YouTube ToS + quota; commercial use requires review",
        ),
        "musicbrainz": profile(
            "musicbrainz", content=conditional, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="web service is free for non-commercial use; commercial requires agreement; DB dump is CC BY-SA",
        ),
        "reddit": profile(
            "reddit", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="content licensing is contested; legal review required",
        ),
        "x": profile(
            "x", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="X ToS restricts scraping; commercial review required",
        ),
        "tiktok": profile(
            "tiktok", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="TikTok ToS restricts scraping; commercial review required",
        ),
        "instagram": profile(
            "instagram", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="Meta ToS restricts scraping; commercial review required",
        ),
        "facebook": profile(
            "facebook", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="Meta ToS restricts scraping; commercial review required",
        ),
        "seatgeek": profile(
            "seatgeek", content=review, api=review, storage=review,
            derivative=review, redistribution=review, commercial=review,
            notes="listing content; scraping restrictions; review required",
        ),
        "ticketmaster": profile(
            "ticketmaster", content=PolicyStatus.COMMERCIAL_AGREEMENT_REQUIRED,
            api=PolicyStatus.COMMERCIAL_AGREEMENT_REQUIRED, storage=PolicyStatus.PROHIBITED,
            derivative=PolicyStatus.PROHIBITED, redistribution=PolicyStatus.PROHIBITED,
            commercial=PolicyStatus.PROHIBITED,
            notes="Discovery API requires commercial agreement",
        ),
        "rss": profile(
            "rss", content=research, api=research, storage=research,
            derivative=research, redistribution=research, commercial=review,
            notes="per-site terms vary; default research-only",
        ),
    }


class PolicyGate:
    """Evaluates acquisition requests against source policy profiles."""

    def __init__(
        self,
        profiles: dict[str, RightsProfile] | None = None,
        *,
        commercial_default: bool = False,
    ) -> None:
        self._profiles = dict(profiles or default_policy_profiles())
        #: default context when a request does not specify one
        self.commercial_default = commercial_default

    def evaluate(
        self,
        platform: str,
        commercial_context: str | None = None,
        mechanism: str = "api",
    ) -> PolicyDecision:
        context = commercial_context or ("commercial" if self.commercial_default else "research")
        profile = self._profiles.get(platform) or unknown_profile(platform)
        return evaluate(platform, profile, commercial_context=context, mechanism=mechanism)
