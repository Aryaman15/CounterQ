"""Compatibility wrapper exposing development fixtures to the Stage 7 evaluator."""

from app.countermap.development_fixtures import (
    DevelopmentCounterMapFixture as CounterMapCorpusFixture,
)
from app.countermap.development_fixtures import load_development_countermap_fixtures


def load_countermap_corpus() -> tuple[CounterMapCorpusFixture, ...]:
    return load_development_countermap_fixtures()
