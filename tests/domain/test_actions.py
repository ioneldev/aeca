import pytest

from aeca.domain.actions import Action, Verb


def test_an_action_cannot_be_changed_through_its_args() -> None:
    action = Action(verb=Verb.APPLY_LABEL, key="m1:label", args={"label": "TASK"})

    with pytest.raises(TypeError):
        action.args["label"] = "SPAM"  # type: ignore[index]


def test_an_action_does_not_follow_the_dict_it_was_built_from() -> None:
    source = {"label": "TASK"}
    action = Action(verb=Verb.APPLY_LABEL, key="m1:label", args=source)

    source["label"] = "SPAM"

    assert action.args["label"] == "TASK"


def test_actions_with_equal_contents_are_equal() -> None:
    first = Action(verb=Verb.STAR, key="k", args={"a": "1"})
    second = Action(verb=Verb.STAR, key="k", args={"a": "1"})

    assert first == second
