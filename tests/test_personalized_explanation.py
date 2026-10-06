"""Prose evidence checks, complete immutable quotes and multilingual contracts."""
import time
from unittest.mock import Mock
import pytest
from src.agents.cultural_localizer import CulturalLocalizerAgent
from src.core.analysis_agent import SafetyStop
from src.core.schema import (PipelineInput, CulturalPersona, ExplanationDraft,
    ExplanationReview, ExplanationVersion, ExplanationSegment, ExplanationCheck)
from tests.test_trust_pipeline import citation, analysis, harness


def draft():
    return ExplanationDraft(versions=[ExplanationVersion(language=l,segments=[
        {'kind':'explanation','text':t,'citation_ids':[0]},
        {'kind':'quote','text':'','citation_ids':[0]}]) for l,t in [
            ('en','This passage states that Allāh is One.'),('ar','يبين هذا النص أن الله أحد.')]])


def review():
    return ExplanationReview(checks=[ExplanationCheck(language=l,segment_id=0,
        supported=True,confidence=.99,reason='Directly supported.') for l in ('en','ar')],
        fully_answers_question=True,versions_agree=True,correct_languages=True,
        preserves_qualifications=True,respectful_without_stereotypes=True,no_unmarked_quotes=True)


def localizer(d=None,r=None):
    agent=CulturalLocalizerAgent(time.monotonic()+60)
    # Single-attempt unit cases inspect each rejection; repair behavior is tested separately.
    agent.policy={**agent.policy,'explanation_attempts':1}
    agent.client.request=Mock(side_effect=[d or draft(),r or review()])
    return agent


def test_writer_and_reviewer_are_separate_requests_and_quotes_are_server_inserted():
    agent=localizer();c=citation()
    versions=agent.localize(PipelineInput(query='Explain this passage'),analysis(),[c],['en','ar'],CulturalPersona.EUROPEAN)
    assert agent.client.request.call_count==2
    assert versions[0].explanation_segments[1].text==c.translations['en']
    assert versions[1].explanation_segments[1].text==c.arabic_text
    assert versions[0].source_text==c.translations['en']
    assert versions[0].text.startswith('This passage states')
    payload=agent.client.request.call_args_list[0].args[1]
    assert payload['audience']=='EUROPEAN' and payload['knowledge_level']=='unknown'
    review_payload=agent.client.request.call_args_list[1].args[1]
    assert 'evidence' not in review_payload
    assert review_payload['required_checks'][0]['cited_evidence']==[
        {'citation_id':0,'published_text':c.translations['en']}]


@pytest.mark.parametrize('field',['fully_answers_question','versions_agree','correct_languages',
    'preserves_qualifications','respectful_without_stereotypes','no_unmarked_quotes'])
def test_each_review_failure_stops_output(field):
    result=review();setattr(result,field,False)
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        localizer(r=result).localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)


@pytest.mark.parametrize('problem',['unsupported','confidence','missing','duplicate'])
def test_every_prose_segment_needs_a_complete_supported_review(problem):
    result=review()
    if problem=='unsupported':result.checks[0].supported=False
    if problem=='confidence':result.checks[0].confidence=.2
    if problem=='missing':result.checks.pop()
    if problem=='duplicate':result.checks[1]=result.checks[0]
    with pytest.raises(SafetyStop):
        localizer(r=result).localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)


@pytest.mark.parametrize('problem',['written_quote','unknown_source','wrong_language','empty_prose'])
def test_invalid_draft_stops_before_review(problem):
    item=draft()
    if problem=='written_quote':item.versions[0].segments[1].text='A rewritten quotation'
    if problem=='unknown_source':item.versions[0].segments[0].citation_ids=[9]
    if problem=='wrong_language':item.versions[0].language='fr'
    if problem=='empty_prose':item.versions[0].segments[0].text=' '
    agent=localizer(d=item)
    with pytest.raises(SafetyStop):agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==1


def test_localizer_failure_never_publishes_unreviewed_draft():
    pipe,a,s=harness()
    broken=Mock();broken.localize.side_effect=SafetyStop('unsupported_personalized_explanation')
    pipe.localizer_factory=lambda _:broken
    output=pipe.run_pipeline(PipelineInput(query='Explain a concept'))
    assert output.answer_status=='ABSTAINED' and not output.citations
    assert output.stop_reason=='unsupported_personalized_explanation'
    assert 'required checks' in output.localized_text
    assert 'No authenticated source was found' not in output.localized_text
    assert not any(t.agent_id=='provenance_verifier' for t in output.telemetry)


def test_localizer_cannot_modify_original_source_block():
    pipe,a,s=harness();service=pipe.localizer_factory(0)
    valid=service.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    valid[0].source_text='A rewritten original'
    service.localize.side_effect=None;service.localize.return_value=valid
    result=pipe.run_pipeline(PipelineInput(query='Explain a concept'))
    assert result.answer_status=='ABSTAINED' and result.stop_reason=='published_text_changed'


def test_personal_ruling_never_calls_writer():
    pipe,a,s=harness();writer=Mock();pipe.localizer_factory=writer
    assert pipe.run_pipeline(PipelineInput(query='Is my prayer valid?')).answer_status=='REFERRED'
    writer.assert_not_called()


@pytest.mark.parametrize('prose',[
    'Treat parents kindly [0].',
    'Treat parents kindly [0, 1].',
    'A concept known as ',
    ', children must not obey that demand.',
    'هذا مفهوم يعرف باسم ',
])
def test_internal_citation_numbers_and_split_sentences_stop_before_review(prose):
    item=draft();item.versions[0].segments[0].text=prose
    agent=localizer(d=item)
    with pytest.raises(SafetyStop,match='malformed_explanation_prose'):
        agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==1


def test_malformed_draft_is_repaired_then_fully_reviewed():
    bad=draft();bad.versions[0].segments[0].text='An unfinished sentence'
    agent=localizer();agent.policy={**agent.policy,'explanation_attempts':2}
    agent.client.request=Mock(side_effect=[bad,draft(),review()])
    result=agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert '[1]' in result[0].text
    assert agent.client.request.call_count==3
    assert agent.client.request.call_args_list[1].args[1]['rejection']=='malformed_explanation_prose'


def test_repeated_unsupported_claim_never_passes_after_repair():
    rejected=review();rejected.checks[0].supported=False
    agent=localizer();agent.policy={**agent.policy,'explanation_attempts':2}
    agent.client.request=Mock(side_effect=[draft(),rejected,draft(),rejected])
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==4


def test_bad_generated_gloss_is_repaired_before_separate_review():
    bad=draft();bad.versions[0].segments[0].text='Tawheed means monotheism.'
    agent=localizer();agent.policy={**agent.policy,'explanation_attempts':2}
    original=citation();before=original.model_dump()
    agent.client.request=Mock(side_effect=[bad,draft(),review()])
    result=agent.localize(PipelineInput(query='Explain'),analysis(),[original],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==3
    repair=agent.client.request.call_args_list[1].args[1]
    assert repair['rejection']=='draft_terminology_review_required'
    assert repair['terminology_feedback']
    assert agent.client.request.call_args_list[2].args[2] is ExplanationReview
    assert result[0].explanation_segments[1].text==original.translations['en']
    assert original.model_dump()==before


def test_repeated_bad_generated_gloss_cannot_reach_review():
    bad=draft();bad.versions[0].segments[0].text='Tawheed means monotheism.'
    agent=localizer();agent.policy={**agent.policy,'explanation_attempts':2}
    agent.client.request=Mock(side_effect=[bad,bad])
    with pytest.raises(SafetyStop,match='draft_terminology_review_required'):
        agent.localize(PipelineInput(query='Explain'),analysis(),[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    assert agent.client.request.call_count==2


def test_beginner_meets_plain_concept_before_requested_technical_term():
    a=analysis();a.knowledge_level='beginner'
    # Assign through validation to retain the enum used by production.
    a=type(a).model_validate(a.model_dump())
    inp=PipelineInput(query='Explain Tawheed to someone who has never heard it.')
    agent=localizer();payload=agent.draft_payload(inp,a,[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL)
    versions=agent.render_draft(draft(),[citation()],['en','ar'])
    versions[0].explanation_segments[0].text='At its core, Tawḥīd means a technical concept.'
    with pytest.raises(SafetyStop,match='beginner_opening_requires_repair'):
        agent.audit_beginner_opening(inp,a,versions,payload)
    versions[0].explanation_segments[0].text='Start with a plain supported concept. Then introduce Tawḥīd.'
    agent.audit_beginner_opening(inp,a,versions,payload)
    assert versions[0].explanation_segments[1].text==citation().translations['en']


def test_advanced_explanation_may_begin_with_requested_technical_term():
    a=analysis();agent=localizer();inp=PipelineInput(query='Explain Tawheed.')
    versions=agent.render_draft(draft(),[citation()],['en','ar'])
    versions[0].explanation_segments[0].text='Tawḥīd is the requested term.'
    agent.audit_beginner_opening(inp,a,versions,
        agent.draft_payload(inp,a,[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL))


def test_beginner_repair_identifies_arabic_opening_after_english_is_fixed():
    a=analysis();a.knowledge_level='beginner';a=type(a).model_validate(a.model_dump())
    agent=localizer();inp=PipelineInput(query='Explain Tawheed to a beginner.')
    versions=agent.render_draft(draft(),[citation()],['en','ar'])
    versions[0].explanation_segments[0].text='God is one and has no equal. This is called Tawḥīd.'
    versions[1].explanation_segments[0].text='الله واحد لا شريك له، وهذا هو التوحيد.'
    with pytest.raises(SafetyStop,match='beginner_opening_requires_repair'):
        agent.audit_beginner_opening(inp,a,versions,agent.draft_payload(inp,a,[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL))
    assert agent.review_feedback['language']=='ar'
    assert 'التوحيد' in agent.review_feedback['offending_sentence']


def test_generated_commentary_translation_cannot_escape_independent_review():
    from src.core.schema import SourcePassage
    agent=localizer();versions=agent.render_draft(draft(),[citation()],['en','ar'])
    translation='An unsupported generated translation. '*220
    versions[0].translated_passages={'0':[SourcePassage(kind='commentary',text=translation,source_url=citation().source_url,reference_id=citation().reference_id)]}
    checked=review();checked.checks.append(ExplanationCheck(language='en',segment_id=2,supported=False,confidence=.1,reason='Translation is not faithful.'))
    agent.client.request=Mock(return_value=checked)
    with pytest.raises(SafetyStop,match='unsupported_personalized_explanation'):
        agent.review(PipelineInput(query='Explain'),analysis(),versions,['en','ar'],[citation()],CulturalPersona.GENERAL_GLOBAL)
    payload=agent.client.request.call_args.args[1]
    assert any(c.get('generated_commentary_translation') and c['text']==translation for c in payload['required_checks'])
    assert len(payload['draft']['versions'][0]['segments'])==2


def test_beginner_opaque_labels_receive_specific_repair_without_changing_quotes():
    a=analysis();a.knowledge_level='beginner';a=type(a).model_validate(a.model_dump())
    agent=localizer();inp=PipelineInput(query='Explain a concept for a beginner.')
    versions=agent.render_draft(draft(),[citation()],['en','ar'])
    versions[0].explanation_segments[0].text='The fundamental belief concerns supreme attributes.'
    with pytest.raises(SafetyStop,match='beginner_opening_requires_repair'):
        agent.audit_beginner_opening(inp,a,versions,agent.draft_payload(inp,a,[citation()],['en','ar'],CulturalPersona.GENERAL_GLOBAL))
    assert agent.review_feedback['language']=='en'
    assert versions[0].explanation_segments[1].text==citation().translations['en']
