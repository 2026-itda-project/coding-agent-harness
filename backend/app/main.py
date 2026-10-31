import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from app.m2 import web

from app.pipeline import analyze, clarify_step, review_step
from app.prd import build_prd, draft_overview, validate_prd
from app.llm.errors import AnalysisError
from app.schemas import (
    AnalyzeRequest, AnalyzeResponse, ClarificationStepRequest, PRDRequest, PRDResponse,
    ReviewRequest, ReviewResponse, WorkflowState,
)

app = FastAPI(title="NL-to-PRD M1 input analysis")


@app.post("/api/requirements/analyze", response_model=AnalyzeResponse)
def analyze_requirements(request: AnalyzeRequest) -> AnalyzeResponse:
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="text must not be blank")
    try:
        return analyze(request.text, policy=request.policy)
    except AnalysisError as exc:
        logging.getLogger("uvicorn.error").warning("analysis_failed code=%s", exc.code)
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnalysisError):
        logging.getLogger("uvicorn.error").warning("analysis_failed code=%s", exc.code)
        return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})
    # Workflow rule messages are fixed text; Pydantic messages may contain client input.
    message = "요청 상태가 검증 규칙을 충족하지 않습니다." if isinstance(exc, ValidationError) else str(exc)
    return HTTPException(status_code=422, detail={"code": "invalid_state", "message": message})


@app.post("/api/clarification/step", response_model=WorkflowState)
def clarification_step(request: ClarificationStepRequest) -> WorkflowState:
    try:
        return clarify_step(request.state, request.answers)
    except (AnalysisError, ValueError) as exc:
        raise http_error(exc) from None


@app.post("/api/review", response_model=ReviewResponse)
def review(request: ReviewRequest) -> ReviewResponse:
    try:
        state, reviewed = review_step(request.state, request.action)
    except (AnalysisError, ValueError) as exc:
        raise http_error(exc) from None
    return ReviewResponse(state=state, reviewed=reviewed)


@app.post("/api/prd", response_model=PRDResponse)
def generate_prd(request: PRDRequest) -> PRDResponse:
    prd = build_prd(request.reviewed, draft_overview(request.text))
    return PRDResponse(prd=prd, validation=validate_prd(prd, request.reviewed))

# ===== M2: approved Review -> Sonnet PRD -> MVP run =====
class M2PRDRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: WorkflowState


class M2RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prd_id: str
    confirm_open_issues: bool = False
    retry: bool = False


def m2_call(fn, *args):
    try:
        return fn(*args)
    except web.Conflict as exc:
        raise HTTPException(status_code=409, detail={"code": "conflict", "message": str(exc)}) from None
    except (AnalysisError, ValueError) as exc:
        raise http_error(exc) from None


@app.post("/api/m2/generate")
def m2_generate(request: M2PRDRequest) -> dict:
    return m2_call(web.start_generation, request.state)


@app.post("/api/m2/prd")
def m2_prd(request: M2PRDRequest) -> dict:
    return m2_call(web.generate_prd, request.state)


@app.post("/api/m2/runs")
def m2_start_run(request: M2RunRequest) -> dict:
    return m2_call(web.start_run, request.prd_id, request.confirm_open_issues, request.retry)


@app.get("/api/m2/runs/{run_id}")
def m2_run_status(run_id: str) -> dict:
    return m2_call(web.run_status, run_id)


@app.post("/api/m2/runs/{run_id}/preview/stop")
def m2_stop_preview(run_id: str) -> dict:
    return m2_call(web.stop_preview, run_id)
