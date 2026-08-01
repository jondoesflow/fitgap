from pathlib import Path

from fitgap.evaluate import (
    GoldenEntry,
    GoldenSet,
    golden_workspace,
    load_golden_set,
    score_citations,
    score_classifications,
)
from fitgap.models import (
    Category,
    Classification,
    Confidence,
    Effort,
    Verification,
    VerificationStatus,
)

GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "golden_set.yaml"


def classify_as(workspace, req_id: str, category: Category):
    req = next(r for r in workspace.requirements if r.id == req_id)
    req.classification = Classification(
        category=category,
        proposed_approach="x",
        feature_relied_on="x",
        effort=Effort.M,
        confidence=Confidence.HIGH,
    )
    return req


def small_golden() -> GoldenSet:
    return GoldenSet(
        requirements=[
            GoldenEntry(id="G-1", text="Qualify leads into opportunities.", expected=Category.FIT_OOB),
            GoldenEntry(
                id="G-2",
                text="Warn on duplicate accounts.",
                expected=Category.FIT_CONFIG,
                acceptable=[Category.FIT_OOB],
            ),
            GoldenEntry(id="G-3", text="Proprietary commission engine.", expected=Category.GAP_CUSTOM),
        ]
    )


def test_scoring_with_acceptable_alternatives():
    golden = small_golden()
    workspace = golden_workspace(golden)
    classify_as(workspace, "G-1", Category.FIT_OOB)       # exact
    classify_as(workspace, "G-2", Category.FIT_OOB)       # acceptable alternative
    classify_as(workspace, "G-3", Category.FIT_OOB)       # wrong
    result = score_classifications(golden, workspace)
    assert result.total == 3
    assert result.correct == 2
    assert result.mismatches == [("G-3", Category.GAP_CUSTOM.value, Category.FIT_OOB.value)]
    assert not result.gates_met  # 66% < 90%


def test_unclassified_counts_as_wrong():
    golden = small_golden()
    workspace = golden_workspace(golden)
    classify_as(workspace, "G-1", Category.FIT_OOB)
    result = score_classifications(golden, workspace)
    assert result.correct == 1
    assert "G-2" in result.unclassified


def test_citation_gate_fails_on_dead_url(monkeypatch):
    golden = small_golden()
    workspace = golden_workspace(golden)
    for req_id in ("G-1", "G-2", "G-3"):
        classify_as(workspace, req_id, Category.FIT_OOB)
    workspace.requirements[0].verification = Verification(
        status=VerificationStatus.VERIFIED,
        citation_url="https://learn.microsoft.com/en-us/live-page",
    )
    workspace.requirements[1].verification = Verification(
        status=VerificationStatus.VERIFIED,
        citation_url="https://learn.microsoft.com/en-us/dead-page",
    )

    from fitgap import evaluate
    from fitgap.verify import UrlCheckResult

    monkeypatch.setattr(
        evaluate,
        "check_learn_url",
        lambda url: UrlCheckResult(ok="live-page" in url, final_url=url),
    )
    result = score_classifications(golden, workspace)
    result = score_citations(workspace, result)
    assert result.citations_checked == 2
    assert result.citations_resolving == 1
    assert not result.citations_pass
    assert result.dead_citations[0][0] == "G-2"


def test_perfect_run_meets_gates(monkeypatch):
    golden = small_golden()
    workspace = golden_workspace(golden)
    classify_as(workspace, "G-1", Category.FIT_OOB)
    classify_as(workspace, "G-2", Category.FIT_CONFIG)
    classify_as(workspace, "G-3", Category.GAP_CUSTOM)
    result = score_classifications(golden, workspace)
    result = score_citations(workspace, result)  # no citations asserted
    assert result.accuracy == 1.0
    assert result.citations_pass
    assert result.gates_met


def test_real_golden_set_loads_and_is_well_formed():
    golden = load_golden_set(GOLDEN_PATH)
    assert len(golden.requirements) == 25
    ids = [e.id for e in golden.requirements]
    assert len(set(ids)) == 25
    covered = {e.expected for e in golden.requirements}
    assert Category.FIT_OOB in covered
    assert Category.FIT_CONFIG in covered
    assert Category.EXTEND_PP in covered
    assert Category.GAP_ISV in covered
    assert Category.GAP_CUSTOM in covered
    assert Category.OUT_OF_SCOPE in covered
    workspace = golden_workspace(golden)
    assert len(workspace.requirements) == 25
