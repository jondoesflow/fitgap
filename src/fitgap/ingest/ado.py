"""Pull requirements from Azure DevOps work items via the REST API.

Auth is a Personal Access Token read from the environment variable named in
fitgap.yaml (default AZURE_DEVOPS_PAT) — the PAT itself never lives in config.
The default WIQL pulls all Epics / Features / User Stories, optionally scoped
to an area path; a full WIQL override is supported in config.
"""

from __future__ import annotations

import html
import os
import re

import requests

from fitgap.config import AdoConfig
from fitgap.models import ParsedRequirement, SourceType

API_VERSION = "7.1"
BATCH_SIZE = 200  # work-items API cap per request

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "System.WorkItemType",
    "System.AreaPath",
    "Microsoft.VSTS.Common.Priority",
]


class AdoError(RuntimeError):
    pass


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", value))).strip()


def build_wiql(config: AdoConfig) -> str:
    if config.wiql:
        return config.wiql
    query = (
        "SELECT [System.Id] FROM WorkItems "
        "WHERE [System.TeamProject] = @project "
        "AND [System.WorkItemType] IN ('Epic', 'Feature', 'User Story') "
        "AND [System.State] <> 'Removed'"
    )
    if config.area_path:
        query += f" AND [System.AreaPath] UNDER '{config.area_path}'"
    return query + " ORDER BY [System.Id]"


def _get_pat(config: AdoConfig) -> str:
    pat = os.environ.get(config.pat_env_var, "")
    if not pat:
        raise AdoError(
            f"Azure DevOps PAT not found in environment variable "
            f"'{config.pat_env_var}'. Create a PAT with Work Items (Read) scope "
            "and export it before running ingest."
        )
    return pat


def fetch_ado_requirements(
    config: AdoConfig, session: requests.Session | None = None
) -> list[ParsedRequirement]:
    if not config.organization or not config.project:
        raise AdoError(
            "Azure DevOps organization/project not configured — set ado.organization "
            "and ado.project in fitgap.yaml."
        )
    pat = _get_pat(config)
    session = session or requests.Session()
    auth = ("", pat)
    base = f"https://dev.azure.com/{config.organization}/{config.project}/_apis"

    wiql_response = session.post(
        f"{base}/wit/wiql",
        params={"api-version": API_VERSION},
        json={"query": build_wiql(config)},
        auth=auth,
        timeout=30,
    )
    if wiql_response.status_code != 200:
        raise AdoError(
            f"WIQL query failed (HTTP {wiql_response.status_code}): "
            f"{wiql_response.text[:300]}"
        )
    ids = [item["id"] for item in wiql_response.json().get("workItems", [])]
    if not ids:
        return []

    results: list[ParsedRequirement] = []
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        items_response = session.get(
            f"{base}/wit/workitems",
            params={
                "ids": ",".join(str(i) for i in chunk),
                "fields": ",".join(FIELDS),
                "api-version": API_VERSION,
            },
            auth=auth,
            timeout=30,
        )
        if items_response.status_code != 200:
            raise AdoError(
                f"Work item fetch failed (HTTP {items_response.status_code}): "
                f"{items_response.text[:300]}"
            )
        for item in items_response.json().get("value", []):
            fields = item.get("fields", {})
            title = (fields.get("System.Title") or "").strip()
            description = strip_html(fields.get("System.Description"))
            text = f"{title.rstrip('.')}. {description}" if description else title
            if not text:
                continue
            work_item_type = fields.get("System.WorkItemType", "Work Item")
            priority = fields.get("Microsoft.VSTS.Common.Priority")
            results.append(
                ParsedRequirement(
                    text=text,
                    source=SourceType.ADO,
                    source_ref=f"ado#{item['id']} ({work_item_type})",
                    priority=str(priority) if priority is not None else None,
                )
            )
    return results
