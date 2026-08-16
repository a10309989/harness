"""Evaluation-suite and regression-run API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.security.dependencies import require_permission

router = APIRouter()


@router.post("/suites")
async def create_suite(
    body: dict,
    request: Request,
    _: object = Depends(require_permission("evaluation:write")),
):
    try:
        return await request.app.state.evaluation_service.create_suite(
            name=body.get("name", ""),
            category=body.get("category", "regression"),
            cases=body.get("cases", []),
            pass_threshold=body.get("pass_threshold", 1.0),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/suites/{suite_id}/runs")
async def start_run(
    suite_id: str,
    request: Request,
    _: object = Depends(require_permission("evaluation:write")),
):
    try:
        return await request.app.state.evaluation_service.start_run(suite_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Evaluation suite not found") from exc


@router.post("/runs/{run_id}/results")
async def record_results(
    run_id: str,
    body: dict,
    request: Request,
    _: object = Depends(require_permission("evaluation:write")),
):
    try:
        return await request.app.state.evaluation_service.record_results(
            run_id,
            body.get("results", []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
