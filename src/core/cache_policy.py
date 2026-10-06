"""Exact-answer invalidation and anonymous, request-local cache measurements.

Source law: docs/guides/10_immutability_constitution_and_source_lock.md.
Similarity may discover evidence, but never authorizes answer reuse.
"""
import json
import hashlib
from threading import Lock
from src.core.integrity import fingerprint
from src.core.source_policy import ROOT


def cache_config():
    return json.loads((ROOT / 'configs/cache_policy.json').read_text(encoding='utf-8'))


def runtime_fingerprint(root=ROOT):
    """Invalidate approvals after code, prompt, model or configuration changes.

Conservatively hash all core/agent Python and configuration JSON/Markdown.
Only the resulting digest enters the key; no environment secrets are read.
Recompute per request so an edited configuration cannot retain old approvals.
"""
    files = sorted(set(root.glob('src/core/*.py')) | set(root.glob('src/agents/*.py'))
        | set(root.glob('configs/**/*.json')) | set(root.glob('configs/**/*.md')))
    return fingerprint({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files})


class CacheMeasurements:
    """Thread-safe counters without questions, keys, source text or answer text."""
    def __init__(self):
        self.lock = Lock()
        self.lookups = {}

    def record(self, kind, outcome, duration_ms):
        with self.lock:
            row = self.lookups.setdefault(kind, {'hits': 0, 'misses': 0, 'errors': 0, 'duration_ms': 0.0})
            row[outcome] += 1
            row['duration_ms'] += duration_ms

    def snapshot(self):
        with self.lock:
            return {kind: {**row, 'duration_ms': round(row['duration_ms'], 2)}
                for kind, row in self.lookups.items()}
