from harness.temporal.models import ApprovalDecision, approval_temporal_workflow_id
from harness.artifacts.storage import MinIOStorage


def test_approval_temporal_workflow_id_is_stable():
    assert approval_temporal_workflow_id("workflow-123") == "harness-approval-workflow-123"


def test_approval_decision_preserves_immutable_signal_values():
    decision = ApprovalDecision(
        decision="approved",
        task_id="task-1",
        actor_id="reviewer-1",
        grant_id="grant-1",
    )

    assert decision.decision == "approved"
    assert decision.grant_id == "grant-1"


def test_minio_storage_keys_are_content_addressed():
    digest = "a" * 64

    assert MinIOStorage.key_for_digest(digest) == f"sha256/aa/aa/{digest}"
