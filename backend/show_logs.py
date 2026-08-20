"""Print or follow Prosper's structured local logs."""

from __future__ import annotations

import argparse
import json
import time

from observability import read_logs


def render(event: dict) -> str:
    fields = event.get("fields") or {}
    context = " ".join(
        f"{key}={value}"
        for key, value in fields.items()
        if key in {"request_id", "conversation_id", "turn_id", "status_code", "duration_ms"}
    )
    suffix = f" · {context}" if context else ""
    return (
        f"{event.get('timestamp', '')} {event.get('level', ''):<8} "
        f"{event.get('process')}/{event.get('component')} "
        f"{event.get('event')}: {event.get('message')}{suffix}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Prosper JSONL logs")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--process")
    parser.add_argument("--level")
    parser.add_argument("--search")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    seen: set[tuple] = set()
    while True:
        events = list(reversed(read_logs(
            limit=args.limit,
            process=args.process,
            level=args.level,
            search=args.search,
        )))
        for event in events:
            identity = (
                event.get("timestamp"),
                event.get("source_file"),
                event.get("event"),
                event.get("message"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            print(json.dumps(event, ensure_ascii=False) if args.json else render(event), flush=True)
        if not args.follow:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
