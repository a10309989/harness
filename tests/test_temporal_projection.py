import pytest


@pytest.mark.asyncio
async def test_workflow_runs_include_temporal_projection_columns(db):
    columns = await db.fetch_all(
        """SELECT column_name AS name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'workflow_runs'"""
    )

    names = {column["name"] for column in columns}
    assert {
        "temporal_workflow_id",
        "temporal_run_id",
        "temporal_state",
        "temporal_updated_at",
        "execution_engine",
    } <= names