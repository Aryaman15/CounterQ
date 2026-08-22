from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_configuration_has_stage0_head() -> None:
    config = Config(str(Path("alembic.ini")))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "202608230001"

