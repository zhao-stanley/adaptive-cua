"""Configuration for one browser-agent task."""

import math
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    model: str
    max_steps: int = 30
    max_seconds: float = 300
    max_tokens: int = 100_000
    max_output_tokens: int = 4096
    api_timeout: float = 60
    trace_dir: Path = Path("runs")
    observation_mode: str = "adaptive"
    full_refresh_interval: int = 12

    def __post_init__(self):
        if self.observation_mode not in {"adaptive", "full"}:
            raise ConfigurationError("observation_mode must be adaptive or full.")
        if type(self.full_refresh_interval) is not int or self.full_refresh_interval < 0:
            raise ConfigurationError("full_refresh_interval must be a nonnegative integer (0 disables refreshes).")
        if not self.model.strip():
            raise ConfigurationError(
                "Set OPENAI_MODEL in .env or pass --model (image input and function calling required)."
            )
        for name in ("max_steps", "max_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ConfigurationError(f"{name} must be a positive integer.")
        for name in ("max_seconds", "api_timeout"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ConfigurationError(f"{name} must be positive and finite.")

    @classmethod
    def from_env(cls):
        return cls(model=os.environ.get("OPENAI_MODEL", ""))
