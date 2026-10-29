from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Page


class AdapterError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass
class AdapterContext:
    job_id: str
    website_id: str
    page: Page
    config: dict[str, Any]
    credentials: Credentials
    allowed_hosts: set[str]


class SiteAdapter(ABC):
    adapter_id: str
    version: str

    @abstractmethod
    async def ensure_session(self, context: AdapterContext) -> None:
        raise NotImplementedError

    @abstractmethod
    async def execute_read(self, context: AdapterContext, action: str, parameters: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def prepare_form(self, context: AdapterContext, parameters: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def execute_form(self, context: AdapterContext, parameters: dict, preview: dict) -> dict:
        raise NotImplementedError


class ConfiguredSelectorAdapter(SiteAdapter):
    """Adapter deklaratif untuk selector yang dikonfigurasi khusus pada setiap website."""

    adapter_id = "configured_selectors"
    version = "1.0.0"

    async def ensure_session(self, context: AdapterContext) -> None:
        login = context.config.get("login") or {}
        login_url = login.get("url")
        if not isinstance(login_url, str) or urlparse(login_url).hostname not in context.allowed_hosts:
            raise AdapterError("NAVIGATION_BLOCKED")
        await context.page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
        for selector in login.get("manual_action_indicators", []):
            if await context.page.locator(selector).count():
                raise AdapterError("NEEDS_MANUAL_ACTION")
        success_selector = login.get("success_indicator")
        if success_selector and await context.page.locator(success_selector).count():
            return
        selectors = (login.get("username_selector"), login.get("password_selector"), login.get("submit_selector"))
        if not all(isinstance(selector, str) and selector for selector in selectors):
            raise AdapterError("ADAPTER_NOT_CONFIGURED")
        try:
            await context.page.locator(selectors[0]).fill(context.credentials.username, timeout=10_000)
            await context.page.locator(selectors[1]).fill(context.credentials.password, timeout=10_000)
            await context.page.locator(selectors[2]).click(timeout=10_000)
            if success_selector:
                await context.page.locator(success_selector).wait_for(state="visible", timeout=15_000)
            else:
                await context.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception as error:
            failure_selector = login.get("failure_indicator")
            if failure_selector and await context.page.locator(failure_selector).count():
                raise AdapterError("LOGIN_FAILED") from None
            raise AdapterError("LOGIN_UNKNOWN_STATE") from error

    async def execute_read(self, context: AdapterContext, action: str, parameters: dict) -> dict:
        target_url = context.config.get("dashboard_url")
        if parameters.get("page"):
            pages = context.config.get("pages", {})
            target_url = pages.get(parameters["page"])
        if not isinstance(target_url, str) or urlparse(target_url).hostname not in context.allowed_hosts:
            raise AdapterError("NAVIGATION_BLOCKED")
        await context.page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        if action == "take_screenshot":
            selectors = context.config.get("sensitive_selectors", [])
            masks = [context.page.locator(selector) for selector in selectors]
            image = await context.page.screenshot(full_page=True, type="png", mask=masks, mask_color="#000000")
            return {"kind": "screenshot", "content": image}
        extract_config = context.config.get("extract") or {}
        fields = extract_config.get("fields") or {}
        if not fields:
            raise AdapterError("ADAPTER_NOT_CONFIGURED")
        values = {}
        for field_name, selector in fields.items():
            if not isinstance(field_name, str) or not isinstance(selector, str):
                raise AdapterError("ADAPTER_NOT_CONFIGURED")
            locator = context.page.locator(selector).first
            if await locator.count() == 0:
                raise AdapterError("SELECTOR_NOT_FOUND")
            values[field_name[:80]] = (await locator.inner_text(timeout=10_000)).strip()[:1000]
        return {"kind": "extract_json", "content": {"website_id": context.website_id, "data": values, "adapter_version": self.version}}

    async def prepare_form(self, context: AdapterContext, parameters: dict) -> dict:
        form = self._form_config(context, parameters)
        values = parameters.get("values")
        if not isinstance(values, dict) or not values or set(values) - set(form.get("fields", {})):
            raise AdapterError("INVALID_PARAMETERS")
        changes = {}
        for name, new_value in values.items():
            selector = form["fields"].get(name)
            if not isinstance(selector, str) or not isinstance(new_value, (str, int, float, bool)):
                raise AdapterError("INVALID_PARAMETERS")
            locator = context.page.locator(selector).first
            if await locator.count() == 0:
                raise AdapterError("SELECTOR_NOT_FOUND")
            old_value = await locator.input_value(timeout=5000)
            changes[name] = {"old": old_value[:500], "new": str(new_value)[:500]}
            await locator.fill(str(new_value), timeout=5000)
        return changes

    async def execute_form(self, context: AdapterContext, parameters: dict, preview: dict) -> dict:
        form = self._form_config(context, parameters)
        changes = preview.get("changes", {})
        for name, expected in changes.items():
            selector = form.get("fields", {}).get(name)
            if not selector:
                raise AdapterError("PREVIEW_MISMATCH")
            locator = context.page.locator(selector).first
            if await locator.count() == 0 or await locator.input_value(timeout=5000) != expected.get("old"):
                raise AdapterError("PREVIEW_MISMATCH")
            await locator.fill(expected.get("new", ""), timeout=5000)
        submit_selector = form.get("submit_selector")
        if not isinstance(submit_selector, str):
            raise AdapterError("ADAPTER_NOT_CONFIGURED")
        await context.page.locator(submit_selector).click(timeout=10_000)
        for name, expected in changes.items():
            selector = form["fields"][name]
            locator = context.page.locator(selector).first
            if await locator.input_value(timeout=10_000) != expected.get("new"):
                raise AdapterError("VERIFY_FAILED")
        return {"verified_fields": list(changes)}

    @staticmethod
    def _form_config(context: AdapterContext, parameters: dict) -> dict:
        templates = context.config.get("forms") or {}
        template = parameters.get("template")
        form = templates.get(template)
        if not isinstance(form, dict):
            raise AdapterError("ADAPTER_NOT_CONFIGURED")
        return form