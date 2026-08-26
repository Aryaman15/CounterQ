"""Validated, version-controlled authored content for curated problems."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_LANGUAGES = frozenset({"cpp", "python", "java"})
SUPPORTED_PROBE_STRATEGIES = frozenset({
    "WHY", "PROVE", "ASSUMPTION_CHALLENGE", "COUNTEREXAMPLE", "COMPLEXITY", "EDGE_CASE",
    "TRADE_OFF", "ALTERNATIVE", "IMPLEMENTATION_CHOICE", "CONSTRAINT_MUTATION", "FAILURE_MODE", "TRANSFER",
})


class StrictContent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LanguageDefinition(StrictContent):
    display_signature: str = Field(min_length=1)
    starter_code: str = Field(min_length=1)


class ArgumentDefinition(StrictContent):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: Literal["int", "bool", "string", "int[]", "string[]", "int[][]", "string[][]"]


class VisibleCase(StrictContent):
    arguments: dict[str, Any]
    expected_output: Any


class ExecutionDefinition(StrictContent):
    method_name: str = Field(min_length=1)
    arguments: list[ArgumentDefinition] = Field(min_length=1)
    return_type: Literal["int", "bool", "string", "int[]", "string[]", "int[][]", "string[][]"]
    comparator: Literal["EXACT", "UNORDERED_LIST"] = "EXACT"
    visible_cases: list[VisibleCase] = Field(min_length=1)
    custom_test_supported: bool = True


class ProblemConceptDefinition(StrictContent):
    canonical_key: str = Field(pattern=r"^[a-z0-9_]+$")
    role: Literal["PRIMARY", "SECONDARY", "OPTIONAL"]
    relevance: Literal["HIGH", "MEDIUM", "LOW"]
    expected_importance: Literal["HIGH", "MEDIUM", "LOW"] | None = None


class ProblemContent(StrictContent):
    schema_version: Literal["problem.v1"] = "problem.v1"
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    version: str = Field(min_length=1)
    catalog_order: int = Field(ge=1)
    title: str = Field(min_length=1)
    review_status: Literal["REVIEWED", "DRAFT"]
    statement: str = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    examples: list[dict[str, str]] = Field(min_length=1)
    execution: ExecutionDefinition
    languages: dict[Literal["cpp", "python", "java"], LanguageDefinition]
    problem_concepts: list[ProblemConceptDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> ProblemContent:
        names = {argument.name for argument in self.execution.arguments}
        if set(self.execution.visible_cases[0].arguments) != names:
            raise ValueError("visible case arguments must exactly match execution arguments")
        if set(self.languages) != SUPPORTED_LANGUAGES:
            raise ValueError("reviewed curated problem must define cpp, python, and java")
        return self


class ReferenceSolution(StrictContent):
    approach_id: str = Field(min_length=1)
    language: Literal["cpp", "python", "java"]
    source_code: str = Field(min_length=1)
    review_status: Literal["REVIEWED"]
    implementation_notes: str | None = None


class Approach(StrictContent):
    approach_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    concept_keys: list[str] = Field(min_length=1)
    applicability: str = Field(min_length=1)
    assumptions: list[str] = []
    key_invariants: list[str] = []
    time_complexity: str = Field(min_length=1)
    space_complexity: str = Field(min_length=1)
    tradeoffs: list[str] = []
    common_implementation_variants: list[str] = []
    common_failure_modes: list[str] = []


class CommonFollowup(StrictContent):
    id: str = Field(min_length=1)
    target_concepts: list[str] = Field(min_length=1)
    target_approach_id: str | None = None
    trigger_cues: list[str] = []
    diagnostic_goal: str = Field(min_length=1)
    relevant_strategies: list[str] = Field(min_length=1)
    expected_good_signals: list[str] = []
    weak_or_misconception_signals: list[str] = []
    counterexample_id: str | None = None
    applicable_levels: list[str] = []
    applicable_stages: list[str] = []
    sample_phrasings: list[str] = []

    @model_validator(mode="after")
    def strategies_are_frozen(self) -> CommonFollowup:
        if not set(self.relevant_strategies).issubset(SUPPORTED_PROBE_STRATEGIES):
            raise ValueError("common followup uses an unsupported ProbeStrategy")
        return self


class InterviewPackContent(StrictContent):
    schema_version: Literal["interview-pack.v1"] = "interview-pack.v1"
    version: str = Field(min_length=1)
    review_status: Literal["REVIEWED", "DRAFT"]
    expected_approaches: list[Approach] = Field(min_length=1)
    alternative_approaches: list[Approach] = []
    reference_solutions: list[ReferenceSolution] = Field(min_length=3)
    concepts: list[str] = Field(min_length=1)
    invariants: list[str] = []
    complexity_expectations: list[str] = []
    common_misconceptions: list[dict[str, Any]] = []
    failure_modes: list[dict[str, Any]] = []
    edge_cases: list[dict[str, Any]] = []
    counterexamples: list[dict[str, Any]] = []
    constraint_mutations: list[dict[str, Any]] = []
    probe_opportunities: list[dict[str, Any]] = []
    common_followups: list[CommonFollowup] = []
    level_considerations: list[dict[str, Any]] = []
    reference_reasoning: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> InterviewPackContent:
        approach_ids = [approach.approach_id for approach in self.expected_approaches]
        if len(approach_ids) != len(set(approach_ids)):
            raise ValueError("expected approach IDs must be unique")
        alternative_ids = [approach.approach_id for approach in self.alternative_approaches]
        if len(alternative_ids) != len(set(alternative_ids)):
            raise ValueError("alternative approach IDs must be unique")
        all_ids = set(approach_ids + alternative_ids)
        pairs = {(solution.approach_id, solution.language) for solution in self.reference_solutions}
        if len(pairs) != len(self.reference_solutions) or not all(solution.approach_id in all_ids for solution in self.reference_solutions):
            raise ValueError("reference solutions must uniquely resolve an approach")
        if {solution.language for solution in self.reference_solutions} != SUPPORTED_LANGUAGES:
            raise ValueError("reviewed pack must include cpp, python, and java references")
        followup_ids = [followup.id for followup in self.common_followups]
        if len(followup_ids) != len(set(followup_ids)):
            raise ValueError("common followup IDs must be unique")
        return self


class CuratedContent(StrictContent):
    problem: ProblemContent
    interview_pack: InterviewPackContent

    @model_validator(mode="after")
    def pack_matches_problem(self) -> CuratedContent:
        if not set(self.interview_pack.concepts).issubset({item.canonical_key for item in self.problem.problem_concepts}):
            raise ValueError("pack concepts must be mapped by ProblemConcept")
        return self


def content_root() -> Path:
    return Path(__file__).resolve().parents[4] / "content" / "problems"


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_curated_content(root: Path | None = None) -> list[CuratedContent]:
    directory = root or content_root()
    entries: list[CuratedContent] = []
    for problem_file in sorted(directory.glob("*/problem.json")):
        pack_file = problem_file.with_name("interview-pack.json")
        if not pack_file.exists():
            raise ValueError(f"Missing Interview Pack for {problem_file.parent.name}")
        entries.append(CuratedContent.model_validate({"problem": json.loads(problem_file.read_text()), "interview_pack": json.loads(pack_file.read_text())}))
    if not entries:
        raise ValueError("No curated problem content exists")
    if len({entry.problem.slug for entry in entries}) != len(entries):
        raise ValueError("Curated problem slugs must be unique")
    if len({entry.problem.catalog_order for entry in entries}) != len(entries):
        raise ValueError("Curated catalog order must be unique")
    return sorted(entries, key=lambda entry: entry.problem.catalog_order)
