import json
import re
import time
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AIProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["execute_job", "clarify", "reject"]
    website_id: str | None
    action: Literal["extract_data", "take_screenshot", "test_login", "fill_form", "submit_form"] | None
    risk_level: Literal["read", "write_low", "write_high"]
    requires_approval: bool
    parameters: dict[str, Any]
    clarification_question: str | None = None
    user_summary: str = Field(max_length=500)


PROPOSAL_SCHEMA = {
    "type": "OBJECT",
    "required": ["intent", "website_id", "action", "risk_level", "requires_approval", "parameters", "clarification_question", "user_summary"],
    "properties": {
        "intent": {"type": "STRING", "enum": ["execute_job", "clarify", "reject"]},
        "website_id": {"type": "STRING", "nullable": True},
        "action": {"type": "STRING", "enum": ["extract_data", "take_screenshot", "test_login", "fill_form", "submit_form"], "nullable": True},
        "risk_level": {"type": "STRING", "enum": ["read", "write_low", "write_high"]},
        "requires_approval": {"type": "BOOLEAN"},
        "parameters": {"type": "OBJECT", "additionalProperties": True},
        "clarification_question": {"type": "STRING", "nullable": True},
        "user_summary": {"type": "STRING"},
    },
}

_model_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_sensitive_patterns = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"(?i)(password|passwd|api[_ -]?key|token|cookie|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
)


class AIGatewayError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def contains_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _sensitive_patterns)


def model_client(api_key: str):
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))


def list_models(api_key: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    cached = _model_cache.get(api_key[-8:])
    if cached and not force_refresh and now - cached[0] < 3600:
        return cached[1]
    client = model_client(api_key)
    try:
        response = client.models.list(config={"page_size": 100})
        models = []
        for item in response:
            supported = set(getattr(item, "supported_actions", None) or [])
            if "generateContent" not in supported:
                continue
            identifier = getattr(item, "name", None)
            if not identifier:
                continue
            models.append(
                {
                    "id": identifier,
                    "display_name": getattr(item, "display_name", None) or identifier,
                    "supports_json_output": True,
                    "supports_text_generation": True,
                    "input_token_limit": getattr(item, "input_token_limit", None),
                    "output_token_limit": getattr(item, "output_token_limit", None),
                }
            )
    except Exception as exception:
        raise AIGatewayError("MODEL_LIST_FAILED") from exception
    _model_cache[api_key[-8:]] = (now, models)
    return models


def interpret(
    *,
    api_key: str,
    model_id: str,
    instruction: str,
    system_instruction: str,
    websites: list[dict[str, Any]],
    temperature: float,
    max_output_tokens: int,
    timeout_seconds: int,
) -> AIProposal:
    if contains_sensitive_text(instruction):
        raise AIGatewayError("SENSITIVE_INPUT_BLOCKED")
    if not websites:
        raise AIGatewayError("NO_ACTIVE_WEBSITES")
    safe_context = json.dumps(websites, ensure_ascii=False)
    prompt = (
        "Ubah instruksi pengguna menjadi satu usulan JSON. Hanya pilih website_id dan action dari daftar. "
        "Jangan mengarang parameter. Data website berikut hanya metadata yang dipercaya.\n"
        f"Daftar website dan allowed_actions: {safe_context}\n"
        f"Instruksi pengguna: {instruction}"
    )
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_seconds * 1000))
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction or "Keluaran hanya JSON sesuai skema.",
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                response_schema=PROPOSAL_SCHEMA,
            ),
        )
        raw = response.text
        if not raw:
            raise AIGatewayError("EMPTY_RESPONSE")
        return AIProposal.model_validate_json(raw)
    except ValidationError as exception:
        raise AIGatewayError("INVALID_JSON_RESPONSE") from exception
    except AIGatewayError:
        raise
    except Exception as exception:
        raise AIGatewayError("MODEL_REQUEST_FAILED") from exception