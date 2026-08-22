import json
import sys
from pathlib import Path

from app.main import create_app


def write_openapi_schema(output_path: Path) -> None:
    app = create_app()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = json.dumps(app.openapi(), indent=2, sort_keys=True)
    output_path.write_text(f"{schema}\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.contracts.openapi <output-path>", file=sys.stderr)
        return 2
    write_openapi_schema(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
