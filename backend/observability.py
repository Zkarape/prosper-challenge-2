"""Structured application logging with safe local inspection.

Production writes JSON events to stdout and optionally to rotated JSONL files.
Development also keeps a compact colored console sink. Patient transcripts are
never added by this module unless LOG_INCLUDE_TRANSCRIPTS is explicitly enabled.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import deque
from pathlib import Path
from threading import RLock
from typing import Any

from loguru import logger


DEFAULT_LOG_DIR = Path(__file__).parent / ".logs"
_CONFIGURE_LOCK = RLock()
_CONFIGURED_PROCESS: str | None = None

_SECRET_PATTERNS = (
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]+=*"), r"\1 [REDACTED]"),
    (
        re.compile(r"(?i)\b(postgresql(?:ql)?://)[^\s@]+@"),
        r"\1[REDACTED]@",
    ),
    (
        re.compile(r"(?i)(api[_-]?key|authorization|password|secret)(\s*[=:]\s*)[^\s,;]+"),
        r"\1\2[REDACTED]",
    ),
)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def log_directory() -> Path:
    configured = os.getenv("LOG_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_LOG_DIR


def redact_text(value: str, *, limit: int = 4000) -> str:
    text = " ".join(str(value).replace("\x00", "").splitlines()).strip()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def transcript_fields(text: str) -> dict[str, Any]:
    """Return useful speech diagnostics without exposing patient wording."""

    fields: dict[str, Any] = {
        "transcript_chars": len(text),
        "transcript_words": len(text.split()),
    }
    if env_flag("LOG_INCLUDE_TRANSCRIPTS", False):
        fields["transcript"] = redact_text(text, limit=2000)
    return fields


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.bind(
            component=record.name,
            event="python_log",
        ).opt(exception=record.exc_info).log(level, redact_text(record.getMessage()))


def configure_logging(process: str) -> Any:
    """Configure Loguru once in a process and return a process-bound logger."""

    global _CONFIGURED_PROCESS
    with _CONFIGURE_LOCK:
        if _CONFIGURED_PROCESS is not None:
            return logger.bind(process=_CONFIGURED_PROCESS, component=process)

        _CONFIGURED_PROCESS = process
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        app_env = os.getenv("APP_ENV", "development").casefold()
        console_json = env_flag("LOG_CONSOLE_JSON", app_env == "production")
        logger.remove()

        if env_flag("LOG_CONSOLE", True):
            if console_json:
                logger.add(
                    sys.stderr,
                    level=level,
                    serialize=True,
                    backtrace=False,
                    diagnose=False,
                    filter=lambda record: _ensure_extras(record, process),
                )
            else:
                logger.add(
                    sys.stderr,
                    level=level,
                    colorize=sys.stderr.isatty(),
                    backtrace=False,
                    diagnose=False,
                    format=(
                        "<green>{time:HH:mm:ss.SSS}</green> "
                        "<level>{level: <8}</level> "
                        "<cyan>{extra[process]}</cyan>/<cyan>{extra[component]}</cyan> "
                        "<level>{message}</level>"
                    ),
                    filter=lambda record: _ensure_extras(record, process),
                )

        if env_flag("LOG_JSON_FILES", True):
            directory = log_directory()
            directory.mkdir(parents=True, exist_ok=True)
            logger.add(
                directory / f"{process}.jsonl",
                level=level,
                serialize=True,
                rotation=os.getenv("LOG_ROTATION", "10 MB"),
                retention=os.getenv("LOG_RETENTION", "7 days"),
                compression="gz",
                backtrace=False,
                diagnose=False,
                filter=lambda record: _ensure_extras(record, process),
            )

        intercept = _InterceptHandler()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
            standard_logger = logging.getLogger(name)
            standard_logger.handlers = [intercept]
            standard_logger.propagate = False

        return logger.bind(process=process, component="system")


def _ensure_extras(record: dict[str, Any], process: str) -> bool:
    extra = record["extra"]
    # Pipecat and its provider adapters include transcript and TTS text in some
    # DEBUG/TRACE messages. Keep our explicitly structured debug events, but do
    # not persist third-party verbose records unless transcript logging was
    # deliberately enabled for a consented local session.
    if (
        not env_flag("LOG_INCLUDE_TRANSCRIPTS", False)
        and record["level"].no < logging.INFO
        and str(record.get("name", "")).startswith("pipecat")
        and "component" not in extra
    ):
        return False
    extra.setdefault("process", process)
    extra.setdefault("component", "system")
    extra.setdefault("event", "log")
    return True


def get_logger(component: str) -> Any:
    process = _CONFIGURED_PROCESS or os.getenv("PROSPER_PROCESS", "application")
    return logger.bind(process=process, component=component)


def debug_log_api_enabled() -> bool:
    default = os.getenv("APP_ENV", "development").casefold() != "production"
    return env_flag("ENABLE_DEBUG_LOG_API", default)


def logging_status() -> dict[str, Any]:
    directory = log_directory()
    files = []
    if directory.exists():
        for path in sorted(directory.glob("*.jsonl")):
            files.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "updated_at": path.stat().st_mtime,
                }
            )
    return {
        "enabled": True,
        "process": _CONFIGURED_PROCESS,
        "level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "directory": str(directory),
        "json_files": env_flag("LOG_JSON_FILES", True),
        "console": env_flag("LOG_CONSOLE", True),
        "transcripts_in_logs": env_flag("LOG_INCLUDE_TRANSCRIPTS", False),
        "debug_api": debug_log_api_enabled(),
        "files": files,
    }


def read_logs(
    *,
    limit: int = 200,
    process: str | None = None,
    level: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent uncompressed JSONL events for the local debug interface."""

    directory = log_directory()
    if not directory.exists():
        return []
    requested_level = level.upper() if level else None
    normalized_search = search.casefold().strip() if search else None
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in deque(_safe_lines(path), maxlen=min(max(limit * 4, 200), 5000)):
            event = _parse_loguru_line(line, path.name)
            if event is None:
                continue
            if process and event.get("process") != process:
                continue
            if requested_level and event.get("level") != requested_level:
                continue
            if normalized_search and normalized_search not in json.dumps(
                event, ensure_ascii=False
            ).casefold():
                continue
            events.append(event)
    events.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return events[:limit]


def _safe_lines(path: Path):
    try:
        with path.open(errors="replace") as handle:
            yield from handle
    except OSError:
        return


def _parse_loguru_line(line: str, source_file: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
        record = payload["record"]
        extra = dict(record.get("extra") or {})
        exception = record.get("exception")
        return {
            "timestamp": record.get("time", {}).get("repr"),
            "level": record.get("level", {}).get("name"),
            "process": extra.pop("process", source_file.removesuffix(".jsonl")),
            "component": extra.pop("component", "system"),
            "event": extra.pop("event", "log"),
            "message": redact_text(record.get("message", "")),
            "source_file": source_file,
            "exception": redact_text(exception.get("value", "")) if exception else None,
            "fields": _json_safe(extra),
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return redact_text(str(value))
