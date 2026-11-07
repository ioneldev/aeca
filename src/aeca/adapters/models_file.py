"""Which model makes the decision, read from the models.yaml that ships with the package."""

from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from aeca.adapters.pricing import PRICES, is_priced
from aeca.domain.errors import ConfigError

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    effort: Effort

    # The cost cap needs a price, so a model without one is refused before any call is made.
    @field_validator("model")
    @classmethod
    def _must_have_a_price(cls, model: str) -> str:
        if not is_priced(model):
            raise ValueError(f"{model!r} has no known price, use one of: {', '.join(PRICES)}")
        return model


class ModelsFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ModelChoice


def load_models(path: Path | None = None) -> ModelsFile:
    source = str(path) if path is not None else "the built-in models.yaml"
    text = path.read_text(encoding="utf-8") if path is not None else _shipped_text()
    try:
        return ModelsFile.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as error:
        raise ConfigError(
            f"Cannot use {source}: {_describe(error)}",
            next_action=(
                f"Fix {source}: it holds `decision: {{model: <priced model>, effort: <level>}}`"
            ),
        ) from error


def _shipped_text() -> str:
    return files("aeca").joinpath("config", "models.yaml").read_text(encoding="utf-8")


def _describe(error: yaml.YAMLError | ValidationError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or 'the file'}: {problem['msg']}"
            for problem in error.errors(include_input=False)
        )
    return "it is not valid YAML"
