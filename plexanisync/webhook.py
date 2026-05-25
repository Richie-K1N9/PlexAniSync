# coding=utf-8
import logging
from collections import defaultdict
from configparser import SectionProxy
from typing import Dict, List, Optional

import requests

from plexanisync.logger_adapter import PrefixLoggerAdapter

logger = PrefixLoggerAdapter(logging.getLogger("PlexAniSync"), {"prefix": "WEBHOOK"})

NOTIFY_FLAG = "notify"
NOTIFY_CATEGORY = "notify_category"

CATEGORY_YEAR_MISMATCH = "year_mismatch"
CATEGORY_NOT_FOUND = "title_not_found"
CATEGORY_AUTH = "auth_error"
CATEGORY_SHOW_ERROR = "show_error"
CATEGORY_FATAL = "fatal"

_CATEGORY_LABELS = {
    CATEGORY_YEAR_MISMATCH: "Year mismatch (sync skipped)",
    CATEGORY_NOT_FOUND: "Title not found on AniList",
    CATEGORY_AUTH: "Authentication failure",
    CATEGORY_SHOW_ERROR: "Per-show error",
    CATEGORY_FATAL: "Fatal error",
}

_SEVERITY_ORDER = {"warning": 0, "error": 1}


class Notifier:
    """Collects notification events during a sync and flushes them as one webhook POST.

    The intended usage is to instantiate once per sync run, attach via attach_to_logger
    so logger.error(... extra={NOTIFY_FLAG: True, NOTIFY_CATEGORY: "..."}) is captured,
    then call flush() at the end of the run.
    """

    def __init__(self, notification_settings: Optional[SectionProxy]):
        self.events: Dict[str, List[str]] = defaultdict(list)
        self.handler: Optional["WebhookLogHandler"] = None

        if notification_settings is None:
            self.webhook_url = ""
            self.format = "discord"
            self.min_severity = "error"
            return

        self.webhook_url = (notification_settings.get("webhook_url", "") or "").strip()
        self.format = (notification_settings.get("webhook_format", "discord") or "discord").strip().lower()
        self.min_severity = (notification_settings.get("webhook_min_severity", "error") or "error").strip().lower()
        if self.min_severity not in _SEVERITY_ORDER:
            self.min_severity = "error"

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def severity_allowed(self, severity: str) -> bool:
        return _SEVERITY_ORDER.get(severity, 0) >= _SEVERITY_ORDER.get(self.min_severity, 1)

    def add(self, category: str, message: str):
        if not self.enabled:
            return
        self.events[category].append(message)

    def attach_to_logger(self, py_logger: logging.Logger):
        if not self.enabled:
            return
        self.handler = WebhookLogHandler(self)
        self.handler.setLevel(logging.WARNING)
        py_logger.addHandler(self.handler)

    def detach_from_logger(self, py_logger: logging.Logger):
        if self.handler is not None:
            py_logger.removeHandler(self.handler)
            self.handler = None

    def flush(self):
        if not self.enabled or not self.events:
            return

        sections = []
        total = 0
        for category, messages in self.events.items():
            if not messages:
                continue
            label = _CATEGORY_LABELS.get(category, category)
            section_lines = [f"**{label}** ({len(messages)})"]
            displayed = messages[:10]
            for msg in displayed:
                section_lines.append(f"• {msg}")
            if len(messages) > len(displayed):
                section_lines.append(f"• ...and {len(messages) - len(displayed)} more")
            sections.append("\n".join(section_lines))
            total += len(messages)

        if not sections:
            return

        body = f"PlexAniSync sync completed with {total} notable event(s):\n\n" + "\n\n".join(sections)
        # Discord caps content at 2000 chars; keep some headroom.
        if len(body) > 1900:
            body = body[:1897] + "..."

        payload = self._build_payload(body)
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=5)
            if response.status_code >= 400:
                logger.warning(
                    f"Webhook returned HTTP {response.status_code}, retrying once"
                )
                response = requests.post(self.webhook_url, json=payload, timeout=5)
                if response.status_code >= 400:
                    logger.warning(f"Webhook retry also failed with HTTP {response.status_code}")
        except requests.RequestException as exc:
            logger.warning(f"Webhook delivery failed: {exc}")
        finally:
            self.events.clear()

    def _build_payload(self, body: str) -> dict:
        if self.format == "slack":
            return {"text": body}
        # Default to Discord-compatible shape.
        return {"content": body}


class WebhookLogHandler(logging.Handler):
    """Routes tagged log records into a Notifier buffer.

    Records must include extra={"notify": True, "notify_category": "<category>"}.
    Records without the notify flag are ignored — this is deliberate so that 429
    rate-limit warnings (which never carry the flag) never reach the webhook.
    """

    def __init__(self, notifier: Notifier):
        super().__init__()
        self.notifier = notifier

    def emit(self, record: logging.LogRecord):
        if not getattr(record, NOTIFY_FLAG, False):
            return
        category = getattr(record, NOTIFY_CATEGORY, CATEGORY_SHOW_ERROR)
        severity = "error" if record.levelno >= logging.ERROR else "warning"
        if not self.notifier.severity_allowed(severity):
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        self.notifier.add(category, message)
