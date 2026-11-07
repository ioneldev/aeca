from pathlib import Path

import pytest

from aeca.adapters.models_file import load_models
from aeca.domain.errors import ConfigError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(text)
    return path


def test_the_shipped_file_names_opus_5_5_at_low_effort() -> None:
    choice = load_models().decision
    assert (choice.model, choice.effort) == ("claude-opus-5-5", "low")


def test_a_custom_file_is_read(tmp_path: Path) -> None:
    path = write(tmp_path, "decision: {model: claude-sonnet-5-5, effort: high}\n")
    assert load_models(path).decision.model == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    "text",
    [
        "decision: {model: claude-nope-1, effort: low}\n",
        "decision: {effort: low}\n",
        "{}\n",
        "decision: {model: claude-opus-5-5, effort: turbo}\n",
        "decision: {model: claude-opus-5-5, effort: low, temperature: 0}\n",
        "decision: [not, a, mapping]\n",
        "decision: {model: claude-opus-5-5, effort: low\n",
    ],
)
def test_a_bad_file_is_a_config_error_that_says_what_to_do(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError) as raised:
        load_models(write(tmp_path, text))
    assert raised.value.next_action
