from __future__ import annotations

from sqlalchemy.orm import configure_mappers


def register_orm_models(*, validate: bool = True) -> None:
    """Import every ORM model and optionally validate mapper relationships."""
    import app.db.models  # noqa: F401

    if validate:
        configure_mappers()
