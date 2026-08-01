import json
from types import SimpleNamespace

import pytest

from fitgap.config import AdoConfig
from fitgap.ingest.ado import (
    AdoError,
    build_wiql,
    fetch_ado_requirements,
    strip_html,
)
from fitgap.models import SourceType


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, wiql_payload: dict, items_payloads: list[dict]):
        self.wiql_payload = wiql_payload
        self.items_payloads = list(items_payloads)
        self.requests: list[SimpleNamespace] = []

    def post(self, url, **kwargs):
        self.requests.append(SimpleNamespace(method="POST", url=url, kwargs=kwargs))
        return FakeResponse(200, self.wiql_payload)

    def get(self, url, **kwargs):
        self.requests.append(SimpleNamespace(method="GET", url=url, kwargs=kwargs))
        return FakeResponse(200, self.items_payloads.pop(0))


CONFIG = AdoConfig(organization="contoso", project="CRM", pat_env_var="TEST_ADO_PAT")


def work_item(item_id: int, title: str, description: str | None = None, wtype="User Story", priority=2):
    fields = {
        "System.Title": title,
        "System.WorkItemType": wtype,
        "Microsoft.VSTS.Common.Priority": priority,
    }
    if description is not None:
        fields["System.Description"] = description
    return {"id": item_id, "fields": fields}


def test_fetch_work_items(monkeypatch):
    monkeypatch.setenv("TEST_ADO_PAT", "secret-pat")
    session = FakeSession(
        wiql_payload={"workItems": [{"id": 101}, {"id": 102}]},
        items_payloads=[
            {
                "value": [
                    work_item(
                        101,
                        "Track opportunities",
                        "<div>The system must <b>track</b> opportunities.&nbsp;</div>",
                    ),
                    work_item(102, "Offline mobile access for technicians", None, "Feature", 1),
                ]
            }
        ],
    )
    reqs = fetch_ado_requirements(CONFIG, session=session)
    assert len(reqs) == 2
    first = reqs[0]
    assert first.text == "Track opportunities. The system must track opportunities."
    assert first.source == SourceType.ADO
    assert first.source_ref == "ado#101 (User Story)"
    assert first.priority == "2"
    assert reqs[1].source_ref == "ado#102 (Feature)"

    wiql_call = session.requests[0]
    assert "contoso/CRM" in wiql_call.url
    assert wiql_call.kwargs["auth"] == ("", "secret-pat")


def test_batching_over_200_ids(monkeypatch):
    monkeypatch.setenv("TEST_ADO_PAT", "pat")
    ids = list(range(1, 251))
    session = FakeSession(
        wiql_payload={"workItems": [{"id": i} for i in ids]},
        items_payloads=[
            {"value": [work_item(i, f"Requirement {i}") for i in ids[:200]]},
            {"value": [work_item(i, f"Requirement {i}") for i in ids[200:]]},
        ],
    )
    reqs = fetch_ado_requirements(CONFIG, session=session)
    assert len(reqs) == 250
    assert len([r for r in session.requests if r.method == "GET"]) == 2


def test_missing_pat_fails_loudly(monkeypatch):
    monkeypatch.delenv("TEST_ADO_PAT", raising=False)
    with pytest.raises(AdoError, match="TEST_ADO_PAT"):
        fetch_ado_requirements(CONFIG, session=FakeSession({}, []))


def test_missing_org_fails_loudly():
    with pytest.raises(AdoError, match="not configured"):
        fetch_ado_requirements(AdoConfig(), session=FakeSession({}, []))


def test_default_wiql_and_area_path_scoping():
    wiql = build_wiql(CONFIG)
    assert "'Epic', 'Feature', 'User Story'" in wiql
    assert "AreaPath" not in wiql

    scoped = AdoConfig(
        organization="o", project="p", area_path="CRM\\Requirements"
    )
    assert "UNDER 'CRM\\Requirements'" in build_wiql(scoped)

    override = AdoConfig(organization="o", project="p", wiql="SELECT [System.Id] FROM WorkItems")
    assert build_wiql(override) == "SELECT [System.Id] FROM WorkItems"


def test_strip_html():
    assert strip_html("<p>Hello&amp;<br/>world</p>") == "Hello& world"
    assert strip_html(None) == ""
