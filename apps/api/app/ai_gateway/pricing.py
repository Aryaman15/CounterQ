from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ai_gateway.provider import ReasoningUsage

PRICING_ASSUMPTION_DATE = "2026-08-24"
TOKENS_PER_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class TextTokenPricing:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"


OPENAI_REASONING_TEXT_PRICING: dict[str, TextTokenPricing] = {
    "gpt-5.6-terra": TextTokenPricing(
        input_per_million=Decimal("2.00"),
        cached_input_per_million=Decimal("0.20"),
        output_per_million=Decimal("12.00"),
    ),
    "gpt-5.6-sol": TextTokenPricing(
        input_per_million=Decimal("4.00"),
        cached_input_per_million=Decimal("0.40"),
        output_per_million=Decimal("20.00"),
    ),
}


def estimate_text_token_cost(model: str, usage: ReasoningUsage) -> tuple[Decimal, str] | None:
    pricing = OPENAI_REASONING_TEXT_PRICING.get(model)
    if pricing is None:
        return None
    if (
        usage.input_tokens is None
        and usage.output_tokens is None
        and usage.cached_input_tokens is None
    ):
        return None

    input_tokens = usage.input_tokens or 0
    cached_input_tokens = min(usage.cached_input_tokens or 0, input_tokens)
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    output_tokens = usage.output_tokens or 0

    cost = (
        (Decimal(uncached_input_tokens) * pricing.input_per_million)
        + (Decimal(cached_input_tokens) * pricing.cached_input_per_million)
        + (Decimal(output_tokens) * pricing.output_per_million)
    ) / TOKENS_PER_MILLION
    return cost.quantize(Decimal("0.000001")), pricing.currency
