from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_create_app_registers_all_orm_mappers_in_clean_runtime_process() -> None:
    script = textwrap.dedent(
        """
        from app.main import create_app
        from sqlalchemy.orm import configure_mappers

        create_app()
        configure_mappers()

        from app.interviews.models import InterviewerPromptDelivery

        print(InterviewerPromptDelivery.ai_invocation.property.mapper.class_.__name__)
        """,
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AIInvocation"
