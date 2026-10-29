import pytest

from app.policy import PolicyViolation, validate_action


def test_read_action_does_not_require_approval():
    result = validate_action(
        action="extract_data",
        parameters={"date_range": "today"},
        allowed_actions={"extract_data"},
    )

    assert result.risk_level == "read"
    assert result.requires_approval is False


def test_write_action_always_requires_approval():
    result = validate_action(
        action="fill_form",
        parameters={"template": "profile", "draft_id": "draft-1"},
        allowed_actions={"fill_form"},
    )

    assert result.risk_level == "write_low"
    assert result.requires_approval is True


def test_ai_cannot_propose_write_when_disabled():
    with pytest.raises(PolicyViolation, match="AI_WRITE_PROPOSALS_DISABLED"):
        validate_action(
            action="fill_form",
            parameters={"template": "profile", "draft_id": "draft-1"},
            allowed_actions={"fill_form"},
            source="ai",
        )


def test_ai_claimed_risk_never_overrides_server_policy():
    result = validate_action(
        action="submit_form",
        parameters={"template": "profile", "draft_id": "draft-1"},
        allowed_actions={"submit_form"},
        allow_ai_write_proposals=True,
        source="ai",
    )

    assert result.risk_level == "write_low"
    assert result.requires_approval is True


def test_action_outside_website_allowlist_is_rejected():
    with pytest.raises(PolicyViolation, match="ACTION_NOT_ALLOWED"):
        validate_action(
            action="take_screenshot",
            parameters={},
            allowed_actions={"extract_data"},
        )


def test_unknown_or_missing_parameters_are_rejected():
    with pytest.raises(PolicyViolation, match="INVALID_PARAMETERS"):
        validate_action(
            action="fill_form",
            parameters={"template": "profile", "draft_id": "draft-1", "url": "https://bad"},
            allowed_actions={"fill_form"},
        )