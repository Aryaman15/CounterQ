"""Typed, deterministic authoring contracts for curated problems and ontology."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

SemanticType = Literal["int", "bool", "string", "int[]", "string[]", "int[][]", "string[][]"]
Language = Literal["cpp", "python", "java"]
ReviewStatus = Literal["REVIEWED", "DRAFT"]

SUPPORTED_LANGUAGES = frozenset({"cpp", "python", "java"})
SUPPORTED_PROBE_STRATEGIES = frozenset(
    {
        "WHY",
        "PROVE",
        "ASSUMPTION_CHALLENGE",
        "COUNTEREXAMPLE",
        "COMPLEXITY",
        "EDGE_CASE",
        "TRADE_OFF",
        "ALTERNATIVE",
        "IMPLEMENTATION_CHOICE",
        "CONSTRAINT_MUTATION",
        "FAILURE_MODE",
        "TRANSFER",
    }
)
SUPPORTED_STAGES = frozenset(
    {
        "SETUP",
        "INTRODUCTION",
        "PROBLEM_UNDERSTANDING",
        "APPROACH_DISCOVERY",
        "APPROACH_DEFENSE",
        "IMPLEMENTATION",
        "TESTING_DEBUGGING",
        "COMPLEXITY_EDGE_CASES",
        "CONSTRAINT_MUTATION",
        "FINAL_DEFENSE",
        "WRAP_UP",
        "COMPLETED",
    }
)
SUPPORTED_LEVELS = frozenset({"INTERN", "NEW_GRAD", "EARLY_CAREER"})
KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class ContentValidationError(ValueError):
    """Authored content is invalid and includes file-level context."""


class StrictContent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthoredConcept(StrictContent):
    canonical_key: str = Field(pattern=KEY_PATTERN)
    display_name: str = Field(min_length=1)
    category: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str = Field(min_length=1)
    status: Literal["ACTIVE", "RETIRED"]
    parent_concept_key: str | None = Field(default=None, pattern=KEY_PATTERN)


class AuthoredConceptAlias(StrictContent):
    concept_key: str = Field(pattern=KEY_PATTERN)
    alias: str = Field(min_length=1)
    alias_type: Literal["TERM", "LANGUAGE", "LIBRARY", "ABBREVIATION"]

    @property
    def normalized_alias(self) -> str:
        return normalize_alias(self.alias)


class AuthoredConceptRelationship(StrictContent):
    from_concept_key: str = Field(pattern=KEY_PATTERN)
    to_concept_key: str = Field(pattern=KEY_PATTERN)
    relationship_type: Literal["IS_A", "RELATED_TO", "PREREQUISITE_OF", "USES", "VARIANT_OF"]


class ConceptOntology(StrictContent):
    schema_version: Literal["concept-ontology.v1"]
    concepts: list[AuthoredConcept] = Field(min_length=1)
    aliases: list[AuthoredConceptAlias] = []
    relationships: list[AuthoredConceptRelationship] = []

    @model_validator(mode="after")
    def validate_graph(self) -> ConceptOntology:
        keys = [item.canonical_key for item in self.concepts]
        if len(keys) != len(set(keys)):
            raise ValueError("canonical concept keys must be unique")
        known = set(keys)
        for concept in self.concepts:
            if concept.parent_concept_key not in known | {None}:
                raise ValueError(f"concept {concept.canonical_key} has dangling parent")
            if concept.parent_concept_key == concept.canonical_key:
                raise ValueError(f"concept {concept.canonical_key} cannot parent itself")
        normalized = [alias.normalized_alias for alias in self.aliases]
        if len(normalized) != len(set(normalized)):
            raise ValueError("normalized aliases must be globally unique")
        for alias in self.aliases:
            if alias.concept_key not in known:
                raise ValueError(f"alias {alias.alias!r} references an unknown concept")
        triples = [
            (item.from_concept_key, item.to_concept_key, item.relationship_type)
            for item in self.relationships
        ]
        if len(triples) != len(set(triples)):
            raise ValueError("concept relationship triples must be unique")
        for relationship in self.relationships:
            if (
                relationship.from_concept_key not in known
                or relationship.to_concept_key not in known
            ):
                raise ValueError("concept relationship has a dangling endpoint")
            if relationship.from_concept_key == relationship.to_concept_key:
                raise ValueError("concept relationship cannot reference itself")
        return self


class LanguageDefinition(StrictContent):
    display_signature: str = Field(min_length=1)
    starter_code: str = Field(min_length=1)


class ArgumentDefinition(StrictContent):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: SemanticType


class VisibleCase(StrictContent):
    arguments: dict[str, JsonValue]
    expected_output: JsonValue


class ExecutionDefinition(StrictContent):
    method_name: str = Field(min_length=1)
    arguments: list[ArgumentDefinition] = Field(min_length=1)
    return_type: SemanticType
    comparator: Literal["EXACT", "UNORDERED_LIST"] = "EXACT"
    visible_cases: list[VisibleCase] = Field(min_length=1)
    custom_test_supported: bool = True


class ProblemConceptDefinition(StrictContent):
    canonical_key: str = Field(pattern=KEY_PATTERN)
    role: Literal["PRIMARY", "SECONDARY", "OPTIONAL"]
    relevance: Literal["HIGH", "MEDIUM", "LOW"]
    expected_importance: Literal["HIGH", "MEDIUM", "LOW"] | None = None


class ProblemExample(StrictContent):
    input: str
    output: str
    explanation: str


class ProblemContent(StrictContent):
    schema_version: Literal["problem.v1"]
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    catalog_order: int = Field(ge=1)
    title: str = Field(min_length=1)
    review_status: ReviewStatus
    statement: str = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    examples: list[ProblemExample] = Field(min_length=1)
    execution: ExecutionDefinition
    languages: dict[Language, LanguageDefinition]
    problem_concepts: list[ProblemConceptDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_problem(self) -> ProblemContent:
        argument_types = {argument.name: argument.type for argument in self.execution.arguments}
        if len(argument_types) != len(self.execution.arguments):
            raise ValueError("execution argument names must be unique")
        for index, case in enumerate(self.execution.visible_cases, start=1):
            if set(case.arguments) != set(argument_types):
                raise ValueError(
                    f"visible case {index} arguments must exactly match execution arguments"
                )
            for name, value in case.arguments.items():
                if not validate_semantic_value(value, argument_types[name]):
                    raise ValueError(f"visible case {index} has invalid value for {name}")
            if not validate_semantic_value(case.expected_output, self.execution.return_type):
                raise ValueError(f"visible case {index} expected output has invalid type")
        if set(self.languages) != SUPPORTED_LANGUAGES:
            raise ValueError("problem must define cpp, python, and java")
        concept_keys = [mapping.canonical_key for mapping in self.problem_concepts]
        if len(concept_keys) != len(set(concept_keys)):
            raise ValueError("problem concept mappings must be unique")
        return self


class Approach(StrictContent):
    approach_id: str = Field(pattern=KEY_PATTERN)
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


class ReferenceSolution(StrictContent):
    approach_id: str = Field(pattern=KEY_PATTERN)
    language: Language
    source_code: str = Field(min_length=1)
    review_status: Literal["REVIEWED"]
    implementation_notes: str | None = None


class TechnicalItem(StrictContent):
    id: str = Field(pattern=KEY_PATTERN)
    concept_keys: list[str] = Field(min_length=1)
    diagnostic_goal: str = Field(min_length=1)
    counterexample_id: str | None = Field(default=None, pattern=KEY_PATTERN)
    approach_id: str | None = Field(default=None, pattern=KEY_PATTERN)


class Invariant(TechnicalItem):
    pass


class ComplexityExpectation(TechnicalItem):
    pass


class CommonMisconception(TechnicalItem):
    pass


class FailureMode(TechnicalItem):
    pass


class EdgeCase(TechnicalItem):
    pass


class ConstraintMutation(TechnicalItem):
    pass


class ProbeOpportunity(TechnicalItem):
    relevant_strategies: list[str] = Field(min_length=1)


class Counterexample(StrictContent):
    id: str = Field(pattern=KEY_PATTERN)
    input: JsonValue
    purpose: str = Field(min_length=1)


class CommonFollowup(StrictContent):
    id: str = Field(pattern=KEY_PATTERN)
    target_concepts: list[str] = Field(min_length=1)
    target_approach_id: str | None = Field(default=None, pattern=KEY_PATTERN)
    trigger_cues: list[str] = []
    diagnostic_goal: str = Field(min_length=1)
    relevant_strategies: list[str] = Field(min_length=1)
    expected_good_signals: list[str] = []
    weak_or_misconception_signals: list[str] = []
    counterexample_id: str | None = Field(default=None, pattern=KEY_PATTERN)
    applicable_levels: list[str] = []
    applicable_stages: list[str] = []
    sample_phrasings: list[str] = []


class LevelConsideration(StrictContent):
    level: str
    guidance: str = Field(min_length=1)


class InterviewPackContent(StrictContent):
    schema_version: Literal["interview-pack.v1"]
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    review_status: ReviewStatus
    expected_approaches: list[Approach] = Field(min_length=1)
    alternative_approaches: list[Approach] = []
    reference_solutions: list[ReferenceSolution] = Field(min_length=1)
    concepts: list[str] = Field(min_length=1)
    invariants: list[Invariant] = []
    complexity_expectations: list[ComplexityExpectation] = []
    common_misconceptions: list[CommonMisconception] = []
    failure_modes: list[FailureMode] = []
    edge_cases: list[EdgeCase] = []
    counterexamples: list[Counterexample] = []
    constraint_mutations: list[ConstraintMutation] = []
    probe_opportunities: list[ProbeOpportunity] = []
    common_followups: list[CommonFollowup] = []
    level_considerations: list[LevelConsideration] = []
    reference_reasoning: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pack(self) -> InterviewPackContent:
        expected_ids = [item.approach_id for item in self.expected_approaches]
        alternative_ids = [item.approach_id for item in self.alternative_approaches]
        if len(expected_ids) != len(set(expected_ids)):
            raise ValueError("expected approach IDs must be unique")
        if len(alternative_ids) != len(set(alternative_ids)):
            raise ValueError("alternative approach IDs must be unique")
        if set(expected_ids) & set(alternative_ids):
            raise ValueError("expected and alternative approach IDs must not collide")
        approach_ids = set(expected_ids + alternative_ids)
        pairs = [(item.approach_id, item.language) for item in self.reference_solutions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("reference solution approach/language pairs must be unique")
        if any(item.approach_id not in approach_ids for item in self.reference_solutions):
            raise ValueError("reference solution has dangling approach")
        primary_id = self.expected_approaches[0].approach_id
        primary_languages = {
            item.language for item in self.reference_solutions if item.approach_id == primary_id
        }
        if self.review_status == "REVIEWED" and primary_languages != SUPPORTED_LANGUAGES:
            raise ValueError("reviewed pack requires cpp, python, and java for primary approach")
        self._validate_references(approach_ids)
        return self

    def _validate_references(self, approach_ids: set[str]) -> None:
        known_concepts = set(self.concepts)
        counterexample_ids = [item.id for item in self.counterexamples]
        if len(counterexample_ids) != len(set(counterexample_ids)):
            raise ValueError("counterexample IDs must be unique")
        known_counterexamples = set(counterexample_ids)
        technical = [
            *self.invariants,
            *self.complexity_expectations,
            *self.common_misconceptions,
            *self.failure_modes,
            *self.edge_cases,
            *self.constraint_mutations,
            *self.probe_opportunities,
        ]
        ids = [item.id for item in technical]
        if len(ids) != len(set(ids)):
            raise ValueError("technical item IDs must be unique")
        for approach in [*self.expected_approaches, *self.alternative_approaches]:
            if not set(approach.concept_keys).issubset(known_concepts):
                raise ValueError(f"approach {approach.approach_id} has dangling concept")
        for item in technical:
            _validate_item_reference(item, known_concepts, approach_ids, known_counterexamples)
            if isinstance(item, ProbeOpportunity):
                _validate_strategies(item.relevant_strategies)
        followup_ids = [item.id for item in self.common_followups]
        if len(followup_ids) != len(set(followup_ids)):
            raise ValueError("common followup IDs must be unique")
        for followup in self.common_followups:
            if not set(followup.target_concepts).issubset(known_concepts):
                raise ValueError(f"followup {followup.id} has dangling concept")
            if followup.target_approach_id and followup.target_approach_id not in approach_ids:
                raise ValueError(f"followup {followup.id} has dangling approach")
            if (
                followup.counterexample_id
                and followup.counterexample_id not in known_counterexamples
            ):
                raise ValueError(f"followup {followup.id} has dangling counterexample")
            _validate_strategies(followup.relevant_strategies)
            if not set(followup.applicable_levels).issubset(SUPPORTED_LEVELS):
                raise ValueError(f"followup {followup.id} has invalid interview level")
            if not set(followup.applicable_stages).issubset(SUPPORTED_STAGES):
                raise ValueError(f"followup {followup.id} has invalid interview stage")
        if any(item.level not in SUPPORTED_LEVELS for item in self.level_considerations):
            raise ValueError("level consideration has invalid interview level")


class CuratedContent(StrictContent):
    problem: ProblemContent
    interview_pack: InterviewPackContent

    @model_validator(mode="after")
    def validate_unit(self) -> CuratedContent:
        mapped = {item.canonical_key for item in self.problem.problem_concepts}
        if not set(self.interview_pack.concepts).issubset(mapped):
            raise ValueError("pack concepts must be mapped through ProblemConcept")
        if (
            self.problem.review_status == "REVIEWED"
            and self.interview_pack.review_status != "REVIEWED"
        ):
            raise ValueError("reviewed problem requires a reviewed Interview Pack")
        return self


def validate_semantic_value(value: object, semantic_type: str) -> bool:
    if semantic_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if semantic_type == "bool":
        return isinstance(value, bool)
    if semantic_type == "string":
        return isinstance(value, str)
    if semantic_type in {"int[]", "string[]"}:
        return isinstance(value, list) and all(
            validate_semantic_value(item, semantic_type[:-2]) for item in value
        )
    if semantic_type in {"int[][]", "string[][]"}:
        return isinstance(value, list) and all(
            validate_semantic_value(item, semantic_type[:-2]) for item in value
        )
    return False


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        _normalize_newlines(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def repository_content_root() -> Path:
    return Path(__file__).resolve().parents[4] / "content"


def content_root() -> Path:
    return repository_content_root() / "problems"


def ontology_path() -> Path:
    return repository_content_root() / "concepts" / "concepts.json"


def load_ontology(path: Path | None = None) -> ConceptOntology:
    return _load_model(path or ontology_path(), ConceptOntology)


def load_curated_content(root: Path | None = None) -> list[CuratedContent]:
    directory = root or content_root()
    entries: list[CuratedContent] = []
    for problem_file in sorted(directory.glob("*/problem.json")):
        pack_file = problem_file.with_name("interview-pack.json")
        if not pack_file.exists():
            raise ContentValidationError(f"{problem_file.parent}: missing interview-pack.json")
        problem = _load_model(problem_file, ProblemContent)
        pack = _load_model(pack_file, InterviewPackContent)
        try:
            entries.append(CuratedContent(problem=problem, interview_pack=pack))
        except ValidationError as exc:
            raise ContentValidationError(f"{problem_file.parent}: {exc}") from exc
    if not entries:
        raise ContentValidationError(f"{directory}: no curated problem content exists")
    slugs = [entry.problem.slug for entry in entries]
    orders = [entry.problem.catalog_order for entry in entries]
    if len(slugs) != len(set(slugs)):
        raise ContentValidationError(f"{directory}: curated problem slugs must be unique")
    if len(orders) != len(set(orders)):
        raise ContentValidationError(f"{directory}: catalog_order must be unique")
    return sorted(entries, key=lambda entry: entry.problem.catalog_order)


def validate_authored_content(
    ontology: ConceptOntology | None = None, entries: list[CuratedContent] | None = None
) -> tuple[ConceptOntology, list[CuratedContent]]:
    validated_ontology = ontology or load_ontology()
    validated_entries = entries or load_curated_content()
    known = {item.canonical_key for item in validated_ontology.concepts}
    for entry in validated_entries:
        problem_keys = {item.canonical_key for item in entry.problem.problem_concepts}
        if not problem_keys.issubset(known):
            raise ContentValidationError(
                f"{entry.problem.slug}: unknown ProblemConcept keys {sorted(problem_keys - known)}"
            )
        if not set(entry.interview_pack.concepts).issubset(known):
            raise ContentValidationError(
                f"{entry.problem.slug}: Interview Pack references unknown concepts"
            )
    return validated_ontology, validated_entries


def _validate_item_reference(
    item: TechnicalItem, concepts: set[str], approaches: set[str], counterexamples: set[str]
) -> None:
    if not set(item.concept_keys).issubset(concepts):
        raise ValueError(f"pack item {item.id} has dangling concept")
    if item.approach_id and item.approach_id not in approaches:
        raise ValueError(f"pack item {item.id} has dangling approach")
    if item.counterexample_id and item.counterexample_id not in counterexamples:
        raise ValueError(f"pack item {item.id} has dangling counterexample")


def _validate_strategies(strategies: list[str]) -> None:
    if not set(strategies).issubset(SUPPORTED_PROBE_STRATEGIES):
        raise ValueError("pack item uses an unsupported ProbeStrategy")


def _normalize_newlines(value: object) -> object:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_normalize_newlines(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_newlines(item) for key, item in value.items()}
    return value


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ContentValidationError(f"{path}: {exc}") from exc
