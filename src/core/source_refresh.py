"""Periodic publisher refresh outside user requests, with truthful persisted reports."""
import asyncio
import json
import time
from scripts.build_source_index import main as refresh
from src.core.integrity import AuthenticatedStore
from src.core.source_policy import load_policy


async def refresh_sources():
    interval=load_policy()['local_source_index']['refresh_interval_seconds']
    path=AuthenticatedStore().directory/'source_ingestion_report.json'
    while True:
        last=0
        if path.exists():
            try:last=json.loads(path.read_text()).get('finished_at',0)
            except (ValueError,OSError):pass
        delay=max(0,last+interval-time.time())
        if delay:await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(refresh)
        except asyncio.CancelledError:raise
        except Exception:
            # Keep the last successful signed corpus. Never claim failed refresh
            # means no evidence exists. The next maintenance run retries.
            await asyncio.sleep(interval)
