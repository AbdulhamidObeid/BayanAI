"""Keep publisher maintenance off the network during deterministic tests."""
import asyncio
import pytest


@pytest.fixture(autouse=True)
def idle_source_maintenance(monkeypatch, tmp_path):
    monkeypatch.setenv("BAYAN_PRIVATE_STORE",str(tmp_path/"private"))
    monkeypatch.setenv("GEMINI_QUOTA_PROJECT","isolated-test-project")
    async def idle():
        await asyncio.Event().wait()
    monkeypatch.setattr('src.core.source_refresh.refresh_sources',idle)
