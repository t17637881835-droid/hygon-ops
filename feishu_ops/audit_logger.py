"""Simple JSONL audit logger."""
import json
from datetime import datetime, timezone
from pathlib import Path


class AuditLogger:
    def __init__(self, log_path: str = ""):
        self.log_path = log_path or "./data/audit.jsonl"
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **kwargs) -> None:
        payload = {
            "event": event,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
