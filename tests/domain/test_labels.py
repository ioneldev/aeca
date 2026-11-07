from aeca.domain.labels import (
    AWAITING,
    ERROR,
    FINANCE_LABEL,
    LABEL_RECORDS,
    PROCESSED,
    URGENCY_DEFINITION,
    URGENT_LABEL,
    Label,
    ReplyKind,
)


def test_every_label_has_a_record() -> None:
    assert set(LABEL_RECORDS) == set(Label)


def test_each_record_names_its_own_label() -> None:
    assert all(record.label == label for label, record in LABEL_RECORDS.items())


def test_each_record_has_two_to_four_examples_and_a_description() -> None:
    for record in LABEL_RECORDS.values():
        assert record.description
        assert 2 <= len(record.examples) <= 4


def test_the_allowed_replies_follow_the_policy_table() -> None:
    allowed = {label: set(record.allowed_replies) for label, record in LABEL_RECORDS.items()}

    assert allowed == {
        Label.MARKETING: {ReplyKind.ACKNOWLEDGEMENT},
        Label.TASK: {ReplyKind.ACKNOWLEDGEMENT},
        Label.INFO_ONLY: {ReplyKind.THANK_YOU},
        Label.SALES_OUTREACH: {ReplyKind.DECLINE},
        Label.MEETING_REQUEST: set(),
        Label.SPAM: set(),
        Label.UNKNOWN: set(),
    }


def test_unknown_is_for_when_no_other_label_fits() -> None:
    assert "only when no other label fits" in LABEL_RECORDS[Label.UNKNOWN].description


def test_spam_covers_phishing_and_scams() -> None:
    description = LABEL_RECORDS[Label.SPAM].description.lower()

    assert "phishing" in description and "scam" in description


def test_gmail_label_names_are_exact() -> None:
    assert [label.value for label in Label if label is not Label.SPAM] == [
        "MEETING_REQUEST", "TASK", "INFO_ONLY", "SALES_OUTREACH", "MARKETING", "UNKNOWN",
    ]  # fmt: skip
    assert (URGENT_LABEL, FINANCE_LABEL) == ("URGENT", "Finance")
    assert (PROCESSED, ERROR, AWAITING) == ("AECA/processed", "AECA/error", "AECA/awaiting")


def test_the_urgency_definition_holds_both_lists_and_the_evidence_rule() -> None:
    assert "Urgent:" in URGENCY_DEFINITION
    assert "Not urgent:" in URGENCY_DEFINITION
    assert "quote" in URGENCY_DEFINITION
