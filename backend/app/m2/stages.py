"""E1-E3 generation: PRD (Sonnet), Spec (Opus), Plan (Opus). The LLM writes content; this
module only sends context, validates structure and references, and records usage."""

from pathlib import Path

from pydantic import ValidationError

from app.llm import claude
from app.m2.contracts import (PRD, Plan, ReviewedPRD, Spec, check_generated_prd, check_plan, check_reviewed_prd,
                              check_spec, output_schema)

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class ContractError(Exception):
    def __init__(self, stage, errors, data):
        super().__init__(f"{stage}: {len(errors)} contract errors: {errors[:5]}")
        self.stage, self.errors, self.data = stage, errors, data


def prompt(name):
    return (PROMPTS / f"m2_{name}.md").read_text()


def stack_contract(policy="legacy"):
    return prompt("stack_contract_single_role" if policy == "single_round_v1" else "stack_contract")


def review_prompt(policy):
    return (prompt("reviewed_prd_generation") + "\n\n" + stack_contract(policy)
            + ("\n\n" + prompt("single_role_policy") if policy == "single_round_v1" else "\nPRD.policy must be legacy."))


# Opus spends most of a high-effort reply on thinking (26K of 32K in a rich-PRD Spec), so Spec and Plan
# get a larger cap; `run.Budget` reserves the same amount before each call.
OPUS_MAX_TOKENS = 64000


def generate(stage, role, system, payload, model_cls, checker, log, effort, constrained=True, max_tokens=32000):
    calls = len(log)
    try:
        data = claude.complete(claude.model_for(role), system, payload, output_schema(model_cls), log,
                               effort=effort, constrained=constrained, max_tokens=max_tokens)
    finally:
        if len(log) > calls:  # label the billed call even when it failed
            log[-1]["stage"] = stage
    try:
        obj = model_cls.model_validate(data)
    except ValidationError as exc:
        raise ContractError(stage, [e["msg"] + " at " + ".".join(map(str, e["loc"])) for e in exc.errors()], data) from None
    errors = checker(obj)
    if errors:
        raise ContractError(stage, errors, data)
    return obj


def generate_prd(user_input, log):
    system = prompt("prd_generation") + "\n\n" + prompt("stack_contract") + "\nPRD.policy must be legacy."
    return generate("prd", "sonnet", system, {"user_input": user_input}, PRD, check_generated_prd, log, effort=None)


def generate_reviewed_prd(text, history, reviewed, log):
    """PRD from an approved Review handoff (`schemas.ReviewedRequirements`); the review wins over the original text."""
    clarifications = [{"question": q.question, "answer": a.answer,
                       **({"ai_proposal": q.ai_proposal, "decision": a.decision} if reviewed.policy == "single_round_v1" else {})}
                      for r in history for q in r.questions for a in r.answers if a.gap_id == q.gap_id]
    payload = {"original_text": text, "clarifications": clarifications, "reviewed": reviewed.model_dump()}
    system = review_prompt(reviewed.policy)
    # Unconstrained like Spec: this schema is larger than the PRD one that fits the structured-output grammar.
    return generate("prd", "sonnet", system, payload, ReviewedPRD,
                    lambda prd: check_reviewed_prd(prd, reviewed), log, effort=None, constrained=False)


def generate_spec(prd, log):
    payload = {"prd": prd.model_dump(), "stack_contract": stack_contract(prd.policy)}
    return generate("spec", "opus", prompt("spec_generation") + ("\n" + prompt("single_role_policy") if prd.policy == "single_round_v1" else ""), payload, Spec,
                    lambda spec: check_spec(spec, prd), log, effort="high", constrained=False,
                    max_tokens=OPUS_MAX_TOKENS)


def generate_plan(prd, spec, log):
    payload = {"prd": prd.model_dump(), "spec": spec.model_dump(), "stack_contract": stack_contract(prd.policy)}
    return generate("plan", "opus", prompt("coding_plan"), payload, Plan,
                    lambda plan: check_plan(plan, spec), log, effort="high", max_tokens=OPUS_MAX_TOKENS)
