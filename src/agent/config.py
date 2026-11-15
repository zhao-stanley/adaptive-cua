"""Configuration for one browser-agent task."""

import math
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    """Edit these defaults to change the model and agent limits. Only OPENAI_API_KEY comes from the environment."""

    model: str = "gpt-astra"
    max_steps: int = 50
    max_seconds: float = 600
    max_tokens: int = 500_000
    max_output_tokens: int = 4096
    api_timeout: float = 60
    trace_dir: Path = Path("runs")
    observation_mode: str = "adaptive"
    full_refresh_interval: int = 12
    compact_after_tokens: int = 8_000

    def __post_init__(self):
        if type(self.compact_after_tokens) is not int or self.compact_after_tokens < 0:
            raise ConfigurationError("compact_after_tokens must be a nonnegative integer (0 disables compaction).")
        if self.observation_mode not in {"adaptive", "full"}:
            raise ConfigurationError("observation_mode must be adaptive or full.")
        if type(self.full_refresh_interval) is not int or self.full_refresh_interval < 0:
            raise ConfigurationError("full_refresh_interval must be a nonnegative integer (0 disables refreshes).")
        if not self.model.strip():
            raise ConfigurationError(
                "model must be a non-empty model ID (image input and function calling required)."
            )
        for name in ("max_steps", "max_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ConfigurationError(f"{name} must be a positive integer.")
        for name in ("max_seconds", "api_timeout"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ConfigurationError(f"{name} must be positive and finite.")
