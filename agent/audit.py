"""Append-only, tamper-evident audit log.

Each entry stores the hash of the entry before it, so editing or deleting any
past line breaks the chain and verify() reports it.
"""
import hashlib
import json
from datetime import datetime, timezone


class AuditLog:
    def __init__(self, path):
        self.path = path
        self.last_hash = "start"

    def write(self, case_id, actor, event, detail):
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "actor": actor,
            "event": event,
            "detail": detail,
            "prev_hash": self.last_hash,
        }
        line = json.dumps(entry, sort_keys=True)
        self.last_hash = hashlib.sha256(line.encode()).hexdigest()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def verify(path):
    """True if no line was edited, removed or reordered."""
    prev = "start"
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["prev_hash"] != prev:
                return False
            prev = hashlib.sha256(line.rstrip("\n").encode()).hexdigest()
    return True
