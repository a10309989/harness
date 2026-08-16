import pytest

from harness.pipeline.service import PipelineService


@pytest.mark.asyncio
async def test_test_case_draft_state_accepts_planner_generated_node_id():
    service = PipelineService.__new__(PipelineService)

    async def load_state(_: str) -> dict:
        return {
            "status": "waiting_approval",
            "current_node": "test_cases_2",
            "node_outputs": {"test_cases_2": "artifact-version"},
            "plan": {
                "objective": "Generate UI test cases",
                "nodes": [
                    {
                        "id": "test_cases_2",
                        "node_type": "agent",
                        "description": "Generate test cases",
                        "agent_id": "test_case_generator",
                    }
                ],
            },
        }

    service._load_state = load_state

    state, node = await service._test_case_draft_state("workflow-id")

    assert state["current_node"] == "test_cases_2"
    assert node.id == "test_cases_2"
    assert node.agent_id == "test_case_generator"
