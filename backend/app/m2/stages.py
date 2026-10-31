"""E1-E3 generation: PRD (Sonnet), Spec (Opus), Plan (Opus). The LLM writes content; this
module only sends context, validates structure and references, and records usage."""

from pathlib import Path

from pydantic import ValidationError

from app.llm import claude
from app.m2.contracts import (PRD, Diagnosis, Plan, ReviewedPRD, Spec, check_diagnosis, check_generated_prd, check_plan,
                              check_reviewed_prd, check_spec, output_schema)

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class ContractError(Exception):
    def __init__(self, stage, errors, data):
        super().__init__(f"{stage}: {len(errors)} contract errors: {errors[:5]}")
        self.stage, self.errors, self.data = stage, errors, data


def prompt(name):
    return (PROMPTS / f"m2_{name}.md").read_text()


# Opus spends most of a high-effort reply on thinking (26K of 32K in a rich-PRD Spec), so Spec and Plan
# get a larger cap; `run.Budget` reserves the same amount before each call.
OPUS_MAX_TOKENS = 64000
# A diagnosis has a far smaller input and output than a Spec; `run.Budget` reserves the same amount.
DIAGNOSIS_MAX_TOKENS = 16000


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
    system = prompt("prd_generation") + "\n\n" + prompt("stack_contract")
    return generate("prd", "sonnet", system, {"user_input": user_input}, PRD, check_generated_prd, log, effort=None)


def generate_reviewed_prd(text, history, reviewed, log):
    """PRD from an approved Review handoff (`schemas.ReviewedRequirements`); the review wins over the original text."""
    clarifications = [{"question": q.question, "answer": a.answer}
                      for r in history for q in r.questions for a in r.answers if a.gap_id == q.gap_id]
    payload = {"original_text": text, "clarifications": clarifications, "reviewed": reviewed.model_dump()}
    system = prompt("reviewed_prd_generation") + "\n\n" + prompt("stack_contract")
    # Unconstrained like Spec: this schema is larger than the PRD one that fits the structured-output grammar.
    return generate("prd", "sonnet", system, payload, ReviewedPRD,
                    lambda prd: check_reviewed_prd(prd, reviewed), log, effort=None, constrained=False)


def generate_spec(prd, log):
    payload = {"prd": prd.model_dump(), "stack_contract": prompt("stack_contract")}
    return generate("spec", "opus", prompt("spec_generation"), payload, Spec,
                    lambda spec: check_spec(spec, prd), log, effort="high", constrained=False,
                    max_tokens=OPUS_MAX_TOKENS)


def generate_plan(prd, spec, log):
    payload = {"prd": prd.model_dump(), "spec": spec.model_dump(), "stack_contract": prompt("stack_contract")}
    return generate("plan", "opus", prompt("coding_plan"), payload, Plan,
                    lambda plan: check_plan(plan, spec), log, effort="high", max_tokens=OPUS_MAX_TOKENS)


def generate_diagnosis(context, log):
    """Cause analysis of a verdict already made by code (`acceptance`). Nothing here can change it."""
    return generate("diagnosis", "opus", prompt("verification_diagnosis"), context, Diagnosis,
                    lambda diagnosis: check_diagnosis(diagnosis, context), log, effort="high",
                    max_tokens=DIAGNOSIS_MAX_TOKENS)
