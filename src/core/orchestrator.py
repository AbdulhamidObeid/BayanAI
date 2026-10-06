"""Gated retrieval, immutable quotations and separately reviewed explanations."""
import re
import json
import time
import math
from typing import Optional
from src.core.analysis_agent import AnalysisAgent, SafetyStop, normalized, personal_ruling, requires_primary_text
from src.core.integrity import fingerprint, AuthenticatedStore
from src.core.cache_policy import CacheMeasurements, cache_config, runtime_fingerprint
from src.core.official_sources import OfficialSources
from src.core.partial_answers import recover_partial, coverage_only_rejection
from src.core.schema import (PipelineInput, PipelineOutput, AgentTelemetryStep, ResponseLevel,
    CulturalPersona, FatwaReferralCard, AnswerVersion, KnowledgeLevel, QueryAnalysis, CacheLookupMetrics)
from src.core.source_policy import load_policy, required_languages, validate_source_url, ROOT
from src.agents.terminology_preserver import TerminologyPreserverAgent
from src.agents.cultural_localizer import CulturalLocalizerAgent

class MasterOrchestrator:
    def __init__(self, analyzer_factory=AnalysisAgent, sources_factory=OfficialSources, preserver_factory=TerminologyPreserverAgent, localizer_factory=CulturalLocalizerAgent):
        self.config = load_policy()
        self.analyzer_factory = analyzer_factory
        self.sources_factory = sources_factory
        self.preserver_factory = preserver_factory
        self.localizer_factory = localizer_factory

    def can_combine_draft(self,analysis,exact=False):
        return (self.config.get('combined_draft_enabled',False) and not exact
            and analysis.level.value in self.config['combined_draft_requirement_kinds_by_level']
            and 1<=len(analysis.question_parts)<=self.config['combined_draft_max_parts']
            and all(p.requirements for p in analysis.question_parts)
            and all(r.kind in self.config['combined_draft_requirement_kinds_by_level'][analysis.level.value]
                for p in analysis.question_parts for r in p.requirements)
            and self.analyzer_factory is AnalysisAgent
            and self.localizer_factory is CulturalLocalizerAgent)

    def run_pipeline(self, inp: PipelineInput) -> PipelineOutput:
        request_started = time.monotonic()
        measurements = CacheMeasurements()
        # Latency is a target, never a reason to discard an answer in progress.
        # Durable jobs must not restart verified work at an overall cutoff.
        # Individual provider attempts and retrieval/refinement counts stay bounded.
        deadline = math.inf
        languages = required_languages(inp.target_language)
        persona = inp.cultural_persona or CulturalPersona.GENERAL_GLOBAL
        if inp.cultural_context in self.config['regions'] and persona == CulturalPersona.GENERAL_GLOBAL:
            persona = CulturalPersona(self.config['regions'][inp.cultural_context])
        output = PipelineOutput(query=inp.query,target_language=inp.target_language,
            cultural_persona=persona)
        stage = 'fatwa_gatekeeper'
        started = time.monotonic()
        def completed(name, summary):
            nonlocal started
            if time.monotonic() >= deadline:
                raise SafetyStop('deadline_exceeded')
            output.telemetry.append(AgentTelemetryStep(agent_id=name,agent_name_ar=name,status='completed',
                duration_ms=round((time.monotonic()-started)*1000,2),summary=summary))
            started=time.monotonic()
        def notice(kind, status, reason):
            output.answer_status=status
            output.stop_reason=reason
            output.citations=[]
            output.citation=None
            output.source_verified=False
            output.is_hallucination_free=False
            output.versions=[AnswerVersion(language=l,text=self.config['messages'][l][kind]) for l in languages]
            output.localized_text=output.versions[0].text
            output.localized_content=output.localized_text
            # All control notices traverse the preserver too. No fabricated safety completion.
            try:
                preserver=self.preserver_factory()
                for version in output.versions:
                    _,audit=preserver.audit_published_text(version.text,version.language,found_terms=[])
                    if audit['violations_details']:
                        raise SafetyStop('notice_lint_failed')
                output.linter_status='CONTROL_NOTICE_CHECKED'
            except Exception:
                output.linter_status='FAILED'
                output.localized_text=''
                output.localized_content=''
                output.versions=[]
                output.answer_status='BLOCKED'
            self.seal(output)
            return output
        try:
            # Even cached answers must respect an explicit all-model quota stop.
            analyzer=self.analyzer_factory(deadline)
            if hasattr(analyzer,'check_quota'):
                analyzer.check_quota()
            if personal_ruling(inp.query):
                output.response_level=ResponseLevel.LEVEL_D
                output.fatwa_referral=FatwaReferralCard(reason='Individual situation detected')
                completed(stage,'Personal ruling referred; retrieval stopped.')
                return notice('referral','REFERRED','personal_ruling')
            completed(stage,'Deterministic screen complete; semantic classification still required.')
            stage='question_analysis'
            if re.search(self.config['identity_pattern'],normalized(inp.query),re.I):
                completed(stage,'System identity request; no religious claims retrieved.')
                return notice('identity','SYSTEM_NOTICE','identity_request')
            # Exact verse identifiers can be interpreted without probabilistic semantic inference.
            exact=re.fullmatch(r'\s*(\d{1,3})\s*:\s*(\d{1,3})\s*',normalized(inp.query))
            cache_policy=cache_config()
            store=AuthenticatedStore(measurements=measurements)
            decision_key=fingerprint({'input':inp.model_dump(mode='json',exclude={'share_response'}),
                'policy':self.config,'glossary':json.loads((ROOT/'configs/sharia_lexicon.json').read_text(encoding='utf-8')),
                'contract_version':cache_policy['contract_version'],
                'runtime':runtime_fingerprint()})
            saved=store.get('decision',decision_key) if not exact and cache_policy['decision_reuse_enabled'] else None
            if exact:
                analysis=QueryAnalysis(level=ResponseLevel.LEVEL_A,confidence=1,knowledge_level=KnowledgeLevel.UNKNOWN,
                    knowledge_reason='No knowledge inference from a verse identifier.',question_language='unknown',
                    keywords={'ar':[], 'en':[]},intent='retrieve_explicit_verse',needs_clarification=False)
            elif saved:
                analysis=QueryAnalysis.model_validate(saved['analysis'])
            else:
                analysis=analyzer.analyze(inp)
                # Do not trust a mocked/alternative analyzer to have validated its output.
                analysis=QueryAnalysis.model_validate(analysis)
                if analysis.needs_clarification or analysis.confidence < self.config['classification_minimum_confidence']:
                    raise SafetyStop('classification_uncertain')
            output.analysis=analysis
            output.knowledge_level=analysis.knowledge_level
            output.response_level=analysis.level
            completed(stage,'Validated level and independent reader knowledge assessment.')
            if analysis.level==ResponseLevel.LEVEL_D:
                output.fatwa_referral=FatwaReferralCard(reason='Semantic classifier detected an individual ruling')
                return notice('referral','REFERRED','personal_ruling')
            stage='sharia_guardian'
            sources=self.sources_factory(deadline)
            if isinstance(sources,OfficialSources):
                sources.store.measurements=measurements
            explicit=[('quran',f'{int(exact[1])}:{int(exact[2])}')] if exact else (
                [tuple(r) for r in saved['references']] if saved else None)
            candidates=[]
            prepared_draft=None
            combined_localizer=None
            partial_context=None
            answer_analysis=None
            refinements=0
            for attempt in range(self.config['retrieval_attempts']+1):
                try:
                    stage='sharia_guardian'
                    retrieved=sources.retrieve(analysis,languages,explicit=explicit)
                    # Validate every source before relevance or search repair consumes it.
                    self.validate_candidates(retrieved,languages)
                    # Foreign originals are discovery material, not substitutes for requested-language passages.
                    retrieved=[c for c in retrieved if (c.content_kind in ('article','book_excerpt','dictionary') and 'ar' in c.translations) or all(l in c.translations for l in languages)]
                    if requires_primary_text(analysis):
                        retrieved=[c for c in retrieved if c.content_kind in ('quran','hadith')]
                    if not retrieved:
                        raise SafetyStop('no_approved_passages_in_required_languages')
                    # Refinement adds evidence; it must not throw away a source
                    # that already answered another part of the question.
                    accumulated={(c.content_kind,c.reference_id):c for c in candidates}
                    accumulated.update({(c.content_kind,c.reference_id):c for c in retrieved})
                    candidates=list(accumulated.values())
                    completed(stage,f'Retrieved {len(candidates)} authenticated candidates. Partial source failures: {len(sources.unavailable)}.')
                    stage='evidence_relevance'
                    if saved and sorted(c.source_digest for c in candidates)==sorted(saved['digests']):
                        citations=candidates
                    else:
                        if saved:
                            # Cached interpretations omit question wording.
                            # Changed evidence requires a fresh full plan.
                            analysis=QueryAnalysis.model_validate(analyzer.analyze(inp))
                            if analysis.needs_clarification or analysis.confidence<self.config['classification_minimum_confidence']:
                                raise SafetyStop('classification_uncertain')
                            output.analysis=analysis
                            output.response_level=analysis.level
                            output.knowledge_level=analysis.knowledge_level
                            if analysis.level==ResponseLevel.LEVEL_D:
                                output.fatwa_referral=FatwaReferralCard(reason='Fresh analysis detected an individual ruling')
                                return notice('referral','REFERRED','personal_ruling')
                            saved=None
                        assessment=sources.shortlist(analysis,candidates) if isinstance(sources,OfficialSources) else candidates
                        eligible=self.can_combine_draft(analysis,exact=bool(exact))
                        if eligible:
                            combined_localizer=self.localizer_factory(deadline)
                            citations,prepared_draft=combined_localizer.select_and_draft(
                                analyzer,inp,analysis,assessment,languages,persona)
                        else:
                            citations=candidates if exact else analyzer.select(inp,analysis,assessment)
                    break
                except SafetyStop as exc:
                    prepared_draft=None
                    combined_localizer=None
                    if (exact or str(exc) not in
                            ('no_direct_answer','no_approved_passages_in_required_languages','missing_scholarly_qualification')):
                        raise
                    if getattr(sources,'has_deferred_references',False) is True:
                        sources.full_retrieval=True
                        explicit=None
                        saved=None
                        continue
                    if refinements>=self.config['retrieval_attempts']-1:
                        if (str(exc) in ('no_direct_answer','no_approved_passages_in_required_languages','missing_scholarly_qualification') and candidates
                                and self.config['partial_answers']['enabled']
                                and isinstance(analyzer,AnalysisAgent)):
                            stage='evidence_relevance'
                            assessment=sources.shortlist(analysis,candidates) if isinstance(sources,OfficialSources) else candidates
                            citations,answer_analysis,partial_context=recover_partial(
                                analyzer,inp,analysis,assessment,languages)
                            saved=None
                            break
                        if getattr(sources,'transient_failure',False) is True:
                            raise SafetyStop('source_service_unavailable') from exc
                        raise
                    refinements+=1
                    stage='search_refinement'
                    try:
                        analysis=analyzer.expand_search(inp,analysis,candidates)
                    except SafetyStop as expansion_error:
                        if (str(expansion_error)!='repeated_search_plan' or not candidates
                                or not self.config['partial_answers']['enabled']
                                or not isinstance(analyzer,AnalysisAgent)):
                            raise
                        stage='evidence_relevance'
                        assessment=sources.shortlist(analysis,candidates) if isinstance(sources,OfficialSources) else candidates
                        citations,answer_analysis,partial_context=recover_partial(
                            analyzer,inp,analysis,assessment,languages)
                        saved=None
                        break
                    output.analysis=analysis
                    completed(stage,'Validated new search concepts; classification and evidence requirements unchanged.')
                    explicit=None
                    saved=None
            stage='evidence_relevance'
            answer_analysis=answer_analysis or analysis
            if not citations or any(c not in candidates for c in citations):
                raise SafetyStop('invalid_evidence_selection')
            if exact and (len(citations)!=1 or citations[0].reference_id!=explicit[0][1]):
                raise SafetyStop('wrong_explicit_reference')
            if any(not (c.content_kind in ('article','book_excerpt','dictionary') and 'ar' in c.translations) and any(l not in c.translations for l in languages) for c in citations):
                raise SafetyStop('source_language_mismatch')
            if requires_primary_text(analysis) and any(c.content_kind not in ('quran','hadith') for c in citations):
                raise SafetyStop('level_a_requires_primary_text')
            completed(stage,'Selected evidence supports a useful limited scope; unanswered aspects will be disclosed.' if partial_context
                else 'Selected evidence directly supports the whole question; no source category was forced.')
            stage='cultural_localizer'
            versions=[AnswerVersion(language=l,text='\n\n'.join(c.text_for(l) for c in citations),
                                   source_text='\n\n'.join(c.text_for(l) for c in citations),
                                   source_urls=[c.url_for(l) for c in citations],
                                   passages=[p for c in citations for p in c.passages_for(l)]) for l in languages]
            reuse_explanation=bool(saved and saved.get('versions') and
                sorted(c.source_digest for c in citations)==sorted(saved['digests']))
            if reuse_explanation:
                versions=[AnswerVersion.model_validate(v) for v in saved['versions']]
                output.answer_cache_reused=True
            elif not exact:
                localizer=combined_localizer if prepared_draft is not None else self.localizer_factory(deadline)
                if partial_context:
                    localizer.partial_context=partial_context
                try:
                    versions=localizer.localize(inp,answer_analysis,citations,languages,persona,
                        **({'prepared_draft':prepared_draft} if prepared_draft is not None else {}))
                except SafetyStop as explanation_error:
                    repaired=False
                    if (not partial_context and str(explanation_error)=='unsupported_personalized_explanation'
                            and isinstance(analyzer,AnalysisAgent) and any(c not in citations for c in candidates)):
                        from src.core.explanation_recovery import reselect_reviewed_answer
                        stage='evidence_relevance'
                        try:
                            assessment=sources.shortlist(analysis,candidates) if isinstance(sources,OfficialSources) else candidates
                            replacement,versions=reselect_reviewed_answer(analyzer,self.localizer_factory,
                                deadline,inp,analysis,assessment,languages,persona,
                                getattr(localizer,'review_feedback',None))
                            citations=replacement;saved=None;repaired=True
                            completed(stage,'Reselected authenticated evidence after review feedback; new complete explanation separately approved.')
                            stage='cultural_localizer'
                        except SafetyStop as retry_error:
                            if str(retry_error) in ('quota_finished','model_rate_limited','model_service_unavailable','model_billing_unavailable','model_access_denied'):
                                raise
                    if not repaired and (partial_context or str(explanation_error)!='unsupported_personalized_explanation'
                            or not self.config['partial_answers']['enabled']
                            or not isinstance(analyzer,AnalysisAgent) or not coverage_only_rejection(localizer)):
                        raise
                    # Review found supported prose but missing coverage. Re-plan
                    # and independently review a disclosed subset, never publish
                    # the rejected complete draft unchanged.
                    if not repaired:
                        stage='evidence_relevance'
                        citations,answer_analysis,partial_context=recover_partial(
                            analyzer,inp,analysis,citations,languages)
                        completed(stage,'Recovering supported portions after incomplete whole-answer review.')
                        stage='cultural_localizer'
                        localizer=self.localizer_factory(deadline)
                        localizer.partial_context=partial_context
                        versions=localizer.localize(inp,answer_analysis,citations,languages,persona)
                        saved=None
            if [v.language for v in versions] != languages:
                raise SafetyStop('explanation_language_contract')
            for v in versions:
                if v.source_text != '\n\n'.join(c.text_for(v.language) for c in citations):
                    raise SafetyStop('published_text_changed')
                if (v.passages != [p for c in citations for p in c.passages_for(v.language)]
                        or v.source_urls != [c.url_for(v.language) for c in citations]
                        or not v.text.strip()):
                    raise SafetyStop('localized_evidence_contract_changed')
            completed(stage,'Authenticated prior explanation/review reused for identical input and unchanged evidence.' if reuse_explanation
                else 'Separate evidence-bound explanation and review complete; original passages preserved.')
            stage='terminology_preserver'
            preserver=self.preserver_factory()
            for version in versions:
                # Original source text and explanation prose: read-only audit — violations BLOCK output.
                for original, original_language in [(version.text,version.language)]+[(c.text_for(version.language),c.evidence_language(version.language)) for c in citations]:
                    cleaned,audit=preserver.audit_published_text(original,original_language)
                    if cleaned!=original or audit['status']!='APPROVED_SAFE':
                        raise SafetyStop('terminology_review_required')
                # Reviewed generated text must not change after its approval.
                for key, passages in version.translated_passages.items():
                    for passage in passages:
                        unchanged,audit=preserver.audit_published_text(passage.text,version.language)
                        if unchanged!=passage.text or audit['status']!='APPROVED_SAFE':
                            raise SafetyStop('terminology_review_required')
            output.linter_status='APPROVED_SAFE'
            completed(stage,
                'All originals, explanations and reviewed commentary translations passed read-only terminology inspection.')
            output.citations=citations
            output.citation=citations[0]
            output.versions=versions
            output.localized_text=versions[0].text
            output.localized_content=output.localized_text
            output.answer_status='PARTIAL' if partial_context else 'ANSWERED'
            output.source_verified=True
            # Do not claim an absolute hallucination proof from relevance scores or hashes.
            output.is_hallucination_free=False
            stage='provenance_verifier'
            self.seal(output)
            completed(stage,'Canonical content fingerprint produced; source retrieval and receipt integrity are separate checks.')
            if not exact and isinstance(sources,OfficialSources) and self.config['evidence_navigation']['enabled']:
                # Optional learned discovery must never withhold a reviewed
                # answer or become permission to skip a new evidence review.
                try:
                    from src.core.evidence_routes import EvidenceRoutes
                    EvidenceRoutes(sources.store).remember(answer_analysis,citations)
                except Exception:
                    pass
            if not exact and not partial_context and not reuse_explanation and cache_policy['decision_reuse_enabled']:
                # Reuse only a complete successful, source-bound decision for this exact input.
                # Keep no raw question; only successful evidence-bound versions and interpretation.
                cached_analysis=analysis.model_dump(mode='json')
                cached_analysis['knowledge_reason']='Previously validated interpretation for this exact input.'
                cached_analysis['intent']='Reuse an authenticated, source-bound decision.'
                cached_analysis['question_parts']=[]  # Never retain raw subquestion wording.
                store.put('decision',decision_key,{'analysis':cached_analysis,
                    'references':[[c.content_kind,c.reference_id] for c in citations],
                    'digests':[c.source_digest for c in citations],
                    'versions':[v.model_dump(mode='json') for v in versions]},cache_policy['decision_ttl_seconds'])
            return output
        except Exception as exc:
            reason=str(exc) if isinstance(exc,SafetyStop) else 'stage_failed'
            output.telemetry.append(AgentTelemetryStep(agent_id=stage,agent_name_ar=stage,status='blocked',
                duration_ms=round((time.monotonic()-started)*1000,2),summary=reason))
            if reason in ('quota_finished','model_rate_limited','model_service_unavailable','model_billing_unavailable','model_access_denied'):
                output.answer_status='QUOTA_EXHAUSTED' if reason=='quota_finished' else 'SERVICE_UNAVAILABLE'
                output.stop_reason=reason
                output.citations=[]
                output.citation=None
                output.versions=[]
                output.localized_text=''
                output.localized_content=''
                output.fatwa_referral=None
                output.source_verified=False
                output.is_hallucination_free=False
                output.linter_status='NOT_PUBLISHED'
                self.seal(output)
                return output
            missing_evidence=reason in ('no_direct_answer','no_approved_passages_in_required_languages')
            return notice('clarify' if reason=='classification_uncertain' else ('abstain' if missing_evidence else 'verification_failed'),'ABSTAINED',reason)
        finally:
            output.cache_metrics={kind:CacheLookupMetrics(**row) for kind,row in measurements.snapshot().items()}
            output.total_duration_ms=round((time.monotonic()-request_started)*1000,2)
            if output.answer_status!='ANSWERED':
                output.answer_cache_reused=False

    def validate_candidates(self,candidates,languages):
        for c in candidates:
            if (not c.provenance_verified or not c.source_digest or not c.link_checked_at or not c.retrieval_endpoint):
                raise SafetyStop('unverified_source_contract')
            if c.content_kind not in ('quran','hadith','article','book_excerpt','dictionary'):
                raise SafetyStop('unsupported_source_content')
            native=c.content_kind in ('article','book_excerpt','dictionary')
            if not native and set(languages)-set(c.translations):
                raise SafetyStop('unverified_source_contract')
            if (c.source_language not in c.translations or c.accredited_translation != c.translations[c.source_language]
                    or c.arabic_text != c.translations.get('ar','') or c.source_url != c.translation_urls[c.source_language]
                    or (not native and c.source_language != languages[0])):
                raise SafetyStop('inconsistent_source_language_contract')
            if c.content_kind=='hadith' and c.scholarly_grading not in self.config['authenticated_hadith_grades']:
                raise SafetyStop('missing_authenticated_hadith_grade')
            for language,text in c.translations.items():
                validate_source_url(c.translation_urls[language])
                if not text.strip():
                    raise SafetyStop('empty_published_passage')
                if c.passages.get(language) and '\n\n'.join(p.text for p in c.passages[language]) != text:
                    raise SafetyStop('passage_contract_mismatch')
            if c.source_digest != OfficialSources.digest(c):
                raise SafetyStop('source_content_integrity_failed')

    @staticmethod
    def seal(output):
        output.provenance_hash=fingerprint({'query':output.query,'language':output.target_language,
            'persona':output.cultural_persona.value,'level':output.response_level.value if output.response_level else None,'status':output.answer_status,
            'text':output.localized_text,'versions':[v.model_dump(mode='json',
                exclude={'scope_notice'} if not v.scope_notice else set()) for v in output.versions],
            'citations':[c.model_dump(mode='json') for c in output.citations]})
        output.cryptographic_hash=output.provenance_hash
        output.verification_qr_data=None  # A public receipt link is created only after explicit sharing.

_orchestrator_instance: Optional[MasterOrchestrator]=None

def run_pipeline(input_data):
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance=MasterOrchestrator()
    return _orchestrator_instance.run_pipeline(input_data)
