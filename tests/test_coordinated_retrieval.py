"""Current audience contract and source selection; no automatic belief profiling."""
import pytest
from src.core.schema import PipelineInput, CulturalPersona
from tests.test_trust_pipeline import harness

@pytest.mark.parametrize('region,persona',[
    ('general','GENERAL_GLOBAL'),('western','WESTERN_SECULAR'),('european','EUROPEAN'),
    ('east_asian','EAST_ASIAN'),('southeast_asian','SOUTHEAST_ASIAN'),
    ('south_asian','SOUTH_ASIAN'),('african','AFRICAN'),('latin_american','LATIN_AMERICAN'),
    ('arab_world','ARAB_WORLD')])
def test_selected_region_is_preserved_and_does_not_guess_belief(region,persona,tmp_path,monkeypatch):
    monkeypatch.setenv('BAYAN_PRIVATE_STORE',str(tmp_path/'private'))
    pipeline,analyzer,sources=harness()
    output=pipeline.run_pipeline(PipelineInput(query='Explain this concept',cultural_context=region))
    assert output.answer_status=='ANSWERED'
    assert output.cultural_persona==CulturalPersona(persona)
    assert output.knowledge_level.value=='unknown'
    assert len(output.citations)==1  # No forced category padding.
    assert any(t.agent_id=='cultural_localizer' and t.status=='completed' for t in output.telemetry)
