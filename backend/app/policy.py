"""Kebijakan tindakan yang ditegakkan server, bukan keluaran AI atau klien."""

from dataclasses import dataclass
from typing import Any


class PolicyViolation(ValueError):
    """Usulan tindakan melanggar kebijakan aplikasi."""


@dataclass(frozen=True)
class ActionPolicy:
    risk: str
    required_parameters: frozenset[str]
    optional_parameters: frozenset[str] = frozenset()


ACTION_POLICIES: dict[str, ActionPolicy] = {
    "extract_data": ActionPolicy(
        risk="read",
        required_parameters=frozenset(),
        optional_parameters=frozenset({"date_range", "page"}),
    ),
    "take_screenshot": ActionPolicy(
        risk="read",
        required_parameters=frozenset(),
        optional_parameters=frozenset({"page"}),
    ),
    "test_login": ActionPolicy(risk="read", required_parameters=frozenset()),
    "fill_form": ActionPolicy(
        risk="write_low",
        required_parameters=frozenset({"template", "draft_id"}),
        optional_parameters=frozenset({"values"}),
    ),
    "submit_form": ActionPolicy(
        risk="write_low",
        required_parameters=frozenset({"template", "draft_id"}),
        optional_parameters=frozenset({"values"}),
    ),
}


@dataclass(frozen=True)
class ValidatedAction:
    action: str
    risk_level: str
    requires_approval: bool
    parameters: dict[str, Any]


def validate_action(
    *,
    action: str,
    parameters: dict[str, Any],
    allowed_actions: set[str] | list[str] | tuple[str, ...],
    allow_ai_write_proposals: bool = False,
    source: str = "api",
) -> ValidatedAction:
    """Validate action and derive risk/approval from the server-side policy."""
    policy = ACTION_POLICIES.get(action)
    if policy is None or action not in allowed_actions:
        raise PolicyViolation("ACTION_NOT_ALLOWED")

    if source == "ai" and policy.risk != "read" and not allow_ai_write_proposals:
        raise PolicyViolation("AI_WRITE_PROPOSALS_DISABLED")

    if not isinstance(parameters, dict):
        raise PolicyViolation("INVALID_PARAMETERS")

    accepted = policy.required_parameters | policy.optional_parameters
    if set(parameters) - accepted or policy.required_parameters - set(parameters):
        raise PolicyViolation("INVALID_PARAMETERS")

    for key, value in parameters.items():
        if not isinstance(key, str):
            raise PolicyViolation("INVALID_PARAMETERS")
        if key == "values" and isinstance(value, dict):
            if not value or len(value) > 30 or any(
                not isinstance(name, str)
                or not isinstance(item, (str, int, float, bool))
                or len(str(item)) > 500
                for name, item in value.items()
            ):
                raise PolicyViolation("INVALID_PARAMETERS")
            continue
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise PolicyViolation("INVALID_PARAMETERS")
        if isinstance(value, str) and len(value) > 500:
            raise PolicyViolation("INVALID_PARAMETERS")

    return ValidatedAction(
        action=action,
        risk_level=policy.risk,
        requires_approval=policy.risk != "read",
        parameters=parameters.copy(),
    )