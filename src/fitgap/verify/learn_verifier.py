"""Verify capability claims against live Microsoft Learn documentation.

Every Fit — OOB / Fit — Configuration / Extend row must earn a live
learn.microsoft.com citation retrieved at analysis time. The model searches
Learn (via the Microsoft Learn MCP server, or the API web-search tool
restricted to learn.microsoft.com) — it must never cite from memory.

Defence in depth: whatever URL the model returns is HTTP-checked by plain code
before it is stored. A URL that is not on learn.microsoft.com, does not
resolve, or was never returned at all downgrades the row to
UNCONFIRMED — validate manually. A fabricated citation can therefore never
reach the register.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import requests

from fitgap.config import Config
from fitgap.llm import UnsupportedFeatureError, as_llm_client
from fitgap.llm.anthropic_client import LEARN_MCP_URL, MCP_BETA  # noqa: F401 (re-export)
from fitgap.models import (
    CATEGORIES_REQUIRING_VERIFICATION,
    Requirement,
    Verification,
    VerificationStatus,
    Workspace,
)
from fitgap.redact import RedactionRules, anonymise

VERIFY_SYSTEM_PROMPT = """\
You verify claims about Microsoft Dynamics 365 and Power Platform capabilities \
against Microsoft Learn documentation. You MUST search Microsoft Learn using \
the tools available to you in this conversation — never answer from memory, \
and never construct a documentation URL you have not retrieved.

A claim is confirmed ONLY if a learn.microsoft.com page you retrieved in this \
conversation explicitly documents the capability as described. If you cannot \
find such a page, say so — an honest "not confirmed" is far more valuable than \
a guessed citation.

Also report whether the page indicates the feature is in PREVIEW or is \
DEPRECATED/being retired.

After searching, respond with ONLY a JSON object (no prose, no code fences):
{
  "confirmed": true/false,
  "citation_url": "https://learn.microsoft.com/..." or null,
  "page_title": "..." or null,
  "preview": true/false,
  "deprecated": true/false,
  "notes": "one sentence on what the page confirms, or why unconfirmed"
}
"""

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass
class UrlCheckResult:
    ok: bool
    final_url: str | None = None
    page_title: str | None = None
    reason: str | None = None


def _is_learn_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "learn.microsoft.com"


def check_learn_url(url: str, timeout: int = 20) -> UrlCheckResult:
    """Liveness guard: the citation must resolve on learn.microsoft.com, now."""
    if not _is_learn_host(url):
        return UrlCheckResult(ok=False, reason=f"not a learn.microsoft.com URL: {url}")
    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "fitgap-citation-check"},
        )
    except requests.RequestException as exc:
        return UrlCheckResult(ok=False, reason=f"citation fetch failed: {exc}")
    if response.status_code != 200:
        return UrlCheckResult(
            ok=False, reason=f"citation returned HTTP {response.status_code}"
        )
    if not _is_learn_host(response.url):
        return UrlCheckResult(
            ok=False, reason=f"citation redirected off learn.microsoft.com: {response.url}"
        )
    title_match = TITLE_RE.search(response.text[:100_000])
    title = title_match.group(1).strip() if title_match else None
    return UrlCheckResult(ok=True, final_url=response.url, page_title=title)


class Verifier:
    def __init__(
        self,
        config: Config,
        rules: RedactionRules,
        client,  # fitgap.llm.LLMClient, anthropic.Anthropic, or a test double
        url_checker: Callable[[str], UrlCheckResult] = check_learn_url,
        usage_tracker=None,  # fitgap.usage.UsageTracker
    ) -> None:
        self.config = config
        self.rules = rules
        self.llm = as_llm_client(client, model=config.model)
        self.url_checker = url_checker
        self.usage_tracker = usage_tracker

    # ------------------------------------------------------------------ API

    def verify_workspace(
        self,
        workspace: Workspace,
        force: bool = False,
        on_progress=None,  # callable(done, total) for progress display
    ) -> dict[str, int]:
        """Verify capability claims in place; returns counts by outcome."""
        counts = {"verified": 0, "unconfirmed": 0, "not_required": 0, "skipped": 0}
        total = len(workspace.requirements)
        if on_progress:
            on_progress(0, total)
        for done, req in enumerate(workspace.requirements, start=1):
            try:
                if req.classification is None:
                    counts["skipped"] += 1
                    continue
                if (
                    req.classification.category
                    not in CATEGORIES_REQUIRING_VERIFICATION
                ):
                    req.verification = Verification(
                        status=VerificationStatus.NOT_REQUIRED
                    )
                    counts["not_required"] += 1
                    continue
                already_verified = (
                    req.verification is not None
                    and req.verification.status == VerificationStatus.VERIFIED
                )
                if already_verified and not force:
                    counts["verified"] += 1
                    continue
                req.verification = self._verify_requirement(req, workspace)
                counts[
                    "verified"
                    if req.verification.status == VerificationStatus.VERIFIED
                    else "unconfirmed"
                ] += 1
            finally:
                if on_progress:
                    on_progress(done, total)
        return counts

    # ------------------------------------------------------------- internals

    def _verify_requirement(
        self, req: Requirement, workspace: Workspace
    ) -> Verification:
        redacted_text, events = anonymise(
            req.text, self.rules, requirement_id=req.id
        )
        workspace.redaction_log.extend(events)
        classification = req.classification
        user_prompt = (
            "Verify this capability claim against Microsoft Learn:\n\n"
            f"Claimed product/feature: {classification.feature_relied_on}\n"
            f"Proposed approach: {classification.proposed_approach}\n"
            f"Requirement it must satisfy: {redacted_text}\n"
        )
        try:
            raw = self.llm.learn_search(
                system=VERIFY_SYSTEM_PROMPT,
                user=user_prompt,
                mode=self.config.verify.mode,
                max_tokens=2048,
                stage="verify",
                tracker=self.usage_tracker,
            )
        except UnsupportedFeatureError:
            # The provider simply cannot do live Learn search — surface it
            # instead of silently marking every row UNCONFIRMED.
            raise
        except Exception as exc:  # API failure must downgrade, never fabricate
            return Verification(
                status=VerificationStatus.UNCONFIRMED,
                notes=f"Verification call failed: {exc}",
            )

        result = self._parse_result(raw)
        if result is None:
            return Verification(
                status=VerificationStatus.UNCONFIRMED,
                notes="Model returned no parseable verification result.",
            )
        if not result.get("confirmed") or not result.get("citation_url"):
            return Verification(
                status=VerificationStatus.UNCONFIRMED,
                notes=result.get("notes") or "Model could not confirm the capability.",
            )

        check = self.url_checker(result["citation_url"])
        if not check.ok:
            # The model *claimed* a citation but it failed the liveness guard —
            # exactly the failure mode the register must surface loudly.
            return Verification(
                status=VerificationStatus.UNCONFIRMED,
                notes=f"Model citation rejected: {check.reason}",
            )

        page_title = check.page_title or result.get("page_title")
        title_lower = (page_title or "").lower()
        return Verification(
            status=VerificationStatus.VERIFIED,
            citation_url=check.final_url,
            page_title=page_title,
            retrieved_at=datetime.now(timezone.utc),
            preview_flag=bool(result.get("preview")) or "preview" in title_lower,
            deprecated_flag=bool(result.get("deprecated"))
            or "deprecated" in title_lower,
            notes=result.get("notes"),
        )

    @staticmethod
    def _parse_result(text: str) -> dict | None:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
