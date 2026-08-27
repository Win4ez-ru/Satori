"""Provider-neutral metadata-only timing and usage metrics."""

from dataclasses import dataclass


def _optional_non_negative(value: int | None, field_name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{field_name} must be a non-negative integer or None")


def _optional_positive(value: int | None, field_name: str) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"{field_name} must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class ProviderExecutionMetrics:
    """Optional provider execution breakdown without request or response content.

    Durations use nanoseconds because that is the precision returned by local Ollama.
    Other adapters may leave unsupported fields unset.
    """

    total_duration_ns: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    client_request_build_duration_ns: int | None = None
    http_roundtrip_duration_ns: int | None = None
    client_response_parse_duration_ns: int | None = None
    requested_output_token_limit: int | None = None
    provider_output_token_limit: int | None = None
    reasoning_output_tokens: int | None = None
    visible_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("total_duration_ns", self.total_duration_ns),
            ("load_duration_ns", self.load_duration_ns),
            ("prompt_eval_duration_ns", self.prompt_eval_duration_ns),
            ("eval_duration_ns", self.eval_duration_ns),
            ("prompt_eval_count", self.prompt_eval_count),
            ("eval_count", self.eval_count),
            ("client_request_build_duration_ns", self.client_request_build_duration_ns),
            ("http_roundtrip_duration_ns", self.http_roundtrip_duration_ns),
            ("client_response_parse_duration_ns", self.client_response_parse_duration_ns),
            ("reasoning_output_tokens", self.reasoning_output_tokens),
            ("visible_output_tokens", self.visible_output_tokens),
        ):
            _optional_non_negative(value, field_name)
        for field_name, value in (
            ("requested_output_token_limit", self.requested_output_token_limit),
            ("provider_output_token_limit", self.provider_output_token_limit),
        ):
            _optional_positive(value, field_name)

    def as_log_fields(self) -> dict[str, int | float | None]:
        """Return only metadata suitable for structured observability."""

        prompt_tokens_per_second = None
        if self.prompt_eval_count is not None and self.prompt_eval_duration_ns:
            prompt_tokens_per_second = round(
                self.prompt_eval_count / (self.prompt_eval_duration_ns / 1_000_000_000), 3
            )
        eval_tokens_per_second = None
        if self.eval_count is not None and self.eval_duration_ns:
            eval_tokens_per_second = round(
                self.eval_count / (self.eval_duration_ns / 1_000_000_000), 3
            )
        return {
            "provider_total_ms": self._milliseconds(self.total_duration_ns),
            "provider_load_ms": self._milliseconds(self.load_duration_ns),
            "provider_prompt_eval_ms": self._milliseconds(self.prompt_eval_duration_ns),
            "provider_eval_ms": self._milliseconds(self.eval_duration_ns),
            "provider_prompt_tokens": self.prompt_eval_count,
            "provider_output_tokens": self.eval_count,
            "provider_prompt_tokens_per_second": prompt_tokens_per_second,
            "provider_output_tokens_per_second": eval_tokens_per_second,
            "client_request_build_ms": self._milliseconds(self.client_request_build_duration_ns),
            "client_http_roundtrip_ms": self._milliseconds(self.http_roundtrip_duration_ns),
            "client_response_parse_ms": self._milliseconds(self.client_response_parse_duration_ns),
            "requested_output_token_limit": self.requested_output_token_limit,
            "provider_output_token_limit": self.provider_output_token_limit,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "visible_output_tokens": self.visible_output_tokens,
        }

    @staticmethod
    def _milliseconds(value: int | None) -> float | None:
        return round(value / 1_000_000, 3) if value is not None else None
