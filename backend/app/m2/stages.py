"""E1-E3 generation: PRD (Sonnet), Spec (Opus), Plan (Opus). The LLM writes content; this
module only sends context, validates structure and references, and records usage."""

from pathlib import Path

from pydantic import ValidationError

from app.llm import claude
from app.m2.contracts import PRD, Plan, Spec, check_generated_prd, check_plan, check_spec, output_schema

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class ContractError(Exception):
    def __init__(self, stage, errors, data):
        super().__init__(f"{stage}: {len(errors)} contract errors: {errors[:5]}")
        self.stage, self.errors, self.data = stage, errors, data


def prompt(name):
    return (PROMPTS / f"m2_{name}.md").read_text()


def generate(stage, role, system, payload, model_cls, checker, log, effort, constrained=True):
    data = claude.complete(claude.model_for(role), system, payload, output_schema(model_cls), log,
                           effort=effort, constrained=constrained)
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


def generate_spec(prd, log):
    payload = {"prd": prd.model_dump(), "stack_contract": prompt("stack_contract")}
    return generate("spec", "opus", prompt("spec_generation"), payload, Spec,
                    lambda spec: check_spec(spec, prd), log, effort="high", constrained=False)


def generate_plan(prd, spec, log):
    payload = {"prd": prd.model_dump(), "spec": spec.model_dump(), "stack_contract": prompt("stack_contract")}
    return generate("plan", "opus", prompt("coding_plan"), payload, Plan,
                    lambda plan: check_plan(plan, spec), log, effort="high")
