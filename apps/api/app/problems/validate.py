"""Pure authored-content validation CLI; no database or provider access."""

from app.problems.content import validate_authored_content


def main() -> None:
    ontology, entries = validate_authored_content()
    reviewed = sum(
        entry.problem.review_status == "REVIEWED"
        and entry.interview_pack.review_status == "REVIEWED"
        for entry in entries
    )
    print(
        "Validated "
        f"{len(ontology.concepts)} concepts, {len(ontology.aliases)} aliases, "
        f"{len(ontology.relationships)} relationships, and "
        f"{reviewed} reviewed curated problems."
    )


if __name__ == "__main__":
    main()
