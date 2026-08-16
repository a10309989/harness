"""Test case API routes."""

from fastapi import APIRouter, Depends

from harness.security.dependencies import require_permission

router = APIRouter()


@router.get("")
async def list_test_cases(_: object = Depends(require_permission("test_case:read"))):
    """List all test cases."""
    return {"test_cases": [], "count": 0}


@router.post("")
async def create_test_case(
    body: dict,
    _: object = Depends(require_permission("test_case:write")),
):
    """Create a new test case."""
    return {"message": "Test case created", "test_case": body}
