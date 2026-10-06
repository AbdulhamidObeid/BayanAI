"""Book provenance contracts; original end-to-end quality expectations retained."""
import time
from unittest.mock import Mock
import pytest
from src.core.official_sources import OfficialSources
from src.core.schema import PipelineInput, CulturalPersona, ResponseLevel
from src.core.orchestrator import run_pipeline

@pytest.mark.parametrize('kind',['library','book_excerpt','article','metadata'])
def test_unextracted_book_metadata_is_never_published_as_an_excerpt(kind,tmp_path,monkeypatch):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    sources=OfficialSources(time.monotonic()+30)
    sources.call=Mock(side_effect=AssertionError('Metadata is not full evidence'))
    assert sources.get(kind,'57678',['en','ar']) is None
    sources.call.assert_not_called()

class TestDawaRAGOrchestratorIntegration:
    """Tests end-to-end pipeline execution with Dawa RAG knowledge retrieval."""

    def test_end_to_end_problem_of_evil(self):
        pipe_input = PipelineInput(
            query="Why does suffering and evil exist in the world?",
            cultural_persona=CulturalPersona.ACADEMIC_SEEKER,
            target_language="en",
        )
        output = run_pipeline(pipe_input)
        assert output is not None
        assert output.response_level in [ResponseLevel.LEVEL_B, ResponseLevel.LEVEL_A]
        assert len(output.citations) >= 1
        assert any("dawa.center" in c.source_url for c in output.citations), "Expected dawa.center citation in coordinated results"
        assert any(w in output.localized_text.lower() for w in ["suffering", "test", "trial", "wisdom", "allah", "god"])

    def test_end_to_end_new_muslim_journey(self):
        pipe_input = PipelineInput(
            query="I just became a Muslim, what are my first practical steps?",
            cultural_persona=CulturalPersona.NEW_MUSLIM,
            target_language="en",
        )
        output = run_pipeline(pipe_input)
        assert output is not None
        assert len(output.citations) >= 1
        assert any("dawa.center" in c.source_url for c in output.citations), "Expected dawa.center citation in coordinated results"
        assert any(w in output.localized_text.lower() for w in ["welcome", "step", "prayer", "ease", "family", "allah"])
