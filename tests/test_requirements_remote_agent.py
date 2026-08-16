import json
from pathlib import Path

import httpx

from harness.a2a.example_agents import RequirementsRemoteAgent, create_example_agents
from harness.a2a.requirements_analysis import HarnessRequirementsAnalyzer
from harness.a2a.server import create_a2a_agent_app
from harness.models.a2a import A2AInvokeParams
from harness.models.agent_contracts import RequirementAnalysisPackage


def test_requirements_analyzer_extracts_harness_docs_strategy():
    package = HarnessRequirementsAnalyzer().analyze(
        message="Analyze Harness enterprise architecture requirements",
        input_data={
            "documents": [
                "docs/workflow-platform-design.md",
                "docs/gateway-evaluation-design.md",
                "docs/enterprise-ops-runbook.md",
            ]
        },
        workspace_root=Path.cwd(),
    )

    assert package.requirement_spec.requirements
    assert package.non_functional_requirements
    assert package.test_strategy.levels == ["unit", "contract", "integration", "e2e", "regression"]
    assert any("A2A" in item or "Agent Card" in item for item in package.test_strategy.regression_scope)
    assert package.mind_map is not None


async def test_requirements_remote_agent_returns_analysis_package():
    agent = RequirementsRemoteAgent()

    result = await agent.invoke(
        A2AInvokeParams(
            agent_id="remote.requirements",
            message="需要通过 A2A 接入远程 Agent，并保证 Gateway、Temporal、Runner 可观测。",
            session_id="session-1",
            input_data={
                "content": """
                # Remote Agent Access
                A2A Gateway must discover Agent Card and invoke remote agents.
                Temporal workflow should orchestrate requirements, test cases, scripts, execution and diagnosis.
                Runner isolation, OTel observability and backup restore drills are required.
                """
            },
        )
    )

    package = RequirementAnalysisPackage.model_validate_json(result.message)

    assert result.metadata["contract"] == "RequirementAnalysisPackage"
    assert package.requirement_spec.requirements
    assert package.test_strategy.objective.startswith("验证企业级")
    assert any(nfr.category == "observability" for nfr in package.non_functional_requirements)
    assert package.mind_map is not None
    assert package.mind_map.children  # 思维导图树包含一级分支


async def test_requirements_remote_agent_over_a2a_service():
    app = create_a2a_agent_app(create_example_agents())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://remote") as client:
        response = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "req-analysis",
                "method": "agent.invoke",
                "params": {
                    "agent_id": "remote.requirements",
                    "message": "拆解 Harness 远程 Agent 需求并生成测试策略",
                    "session_id": "session-1",
                    "input_data": {
                        "documents": [
                            {
                                "path": "inline.md",
                                "content": "# Harness A2A\nA2A agent workflow requires PostgreSQL, Temporal, Redis, MinIO, Gateway and Runner isolation.",
                            }
                        ]
                    },
                },
            },
        )

    payload = response.json()
    package = RequirementAnalysisPackage.model_validate_json(payload["result"]["message"])

    assert payload["error"] is None
    assert package.traceability
    assert package.requirement_spec.risks
