from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RequirementCategory = Literal[
    "role", "functional", "flow", "permission", "state", "business_rule",
    "exception", "scope", "integration", "nfr",
]
GapCategory = Literal[
    "user_role", "core_flow", "permission_approval", "state_change",
    "modify_cancel", "exception_handling", "service_scope", "external_integration",
    "business_rule", "nfr",
]


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str
    id: str
    category: RequirementCategory
    description: str
    status: Literal["confirmed", "needs_clarification", "proposed"]
    source: str
    blocking: bool
    acceptance_criteria: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocking_requires_clarification(self):
        if self.blocking and self.status != "needs_clarification":
            raise ValueError("blocking Requirement must need clarification")
        return self


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    related_requirement_ids: list[str]
    category: GapCategory
    description: str
    blocking: bool


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1)


class AnalyzeResponse(BaseModel):
    project_id: str
    run_id: str
    clarification_round: int = 0
    requirements: list[Requirement]
    gaps: list[Gap]
    clarification_needed: bool
    analysis_mode: Literal["mock", "snowchat"] = "mock"
    usage: list[dict] = Field(default_factory=list)


class InitialRequirement(Requirement):
    status: Literal["confirmed", "needs_clarification"]
    source: Literal["initial_input"]
    acceptance_criteria: list[str]


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirements: list[InitialRequirement]


class GapOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gaps: list[Gap]


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gap_id: str
    question: str
    ai_proposal: str


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gap_id: str
    answer: str


class ClarificationRound(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    round: int
    gaps: list[Gap]
    questions: list[ClarificationQuestion]
    answers: list[Answer]


class WorkflowState(AnalyzeResponse):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    phase: Literal["clarifying", "review", "approved"] = "clarifying"
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    history: list[ClarificationRound] = Field(default_factory=list)
    edit_count: int = 0


class QuestionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    questions: list[ClarificationQuestion]


class UpdatedRequirement(Requirement):
    status: Literal["confirmed", "needs_clarification"]
    source: Literal["clarification_answer"]
    acceptance_criteria: list[str]


class RequirementUpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirements: list[UpdatedRequirement]


class AcceptAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["accept"]
    ids: list[str]


class EditAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["edit"]
    id: str
    description: str | None = None
    category: RequirementCategory | None = None
    acceptance_criteria: list[str] | None = None
    confirm: bool = False


class AddAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["add"]
    category: RequirementCategory
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class ResolveGapAction(BaseModel):
    """Review에서 사용자가 남은 Gap의 결정을 직접 입력한다. 결정은 새 confirmed Requirement가 된다."""
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["resolve_gap"]
    gap_id: str
    description: str
    category: RequirementCategory | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)


class ApproveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["approve"]


ReviewAction = Annotated[AcceptAction | EditAction | AddAction | ResolveGapAction | ApproveAction,
                         Field(discriminator="type")]


class ReviewedRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str
    run_id: str
    requirements: list[Requirement]
    # 승인 시점에 남은 non-blocking Gap. PRD Open Issues로 전달한다. 기존 인계 JSON 호환을 위해 기본값은 빈 배열.
    open_gaps: list[Gap] = Field(default_factory=list)
    metrics: dict


class ClarificationStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: WorkflowState
    answers: list[Answer] | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: WorkflowState
    action: ReviewAction


class ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: WorkflowState
    reviewed: ReviewedRequirements | None = None

# ===== PRD 단계 (남윤아) =====
class PRDItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    description: str
    source_requirement_ids: list[str]
    source_gap_ids: list[str] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    description: str
    target_id: str
    source_requirement_ids: list[str]


class ProductOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str
    problem: str
    goals: list[str]


class PRD(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str
    run_id: str
    overview: ProductOverview
    in_scope: list[PRDItem]
    out_of_scope: list[PRDItem]
    roles: list[PRDItem]
    user_flows: list[PRDItem]
    functional_requirements: list[PRDItem]
    business_rules: list[PRDItem]
    nfrs: list[PRDItem]
    acceptance_criteria: list[AcceptanceCriterion]
    assumptions: list[PRDItem]
    open_issues: list[PRDItem]


class PRDRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    reviewed: ReviewedRequirements


class PRDResponse(BaseModel):
    prd: PRD
    validation: dict