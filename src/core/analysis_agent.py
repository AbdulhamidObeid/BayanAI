"""Structured question analysis and full-passage relevance assessment; no answer generation."""
import json
import os
import re
import time
import unicodedata
import copy
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from src.core.schema import QueryAnalysis, EvidenceSelection, SearchExpansion
from src.core.source_policy import load_policy, required_languages, ROOT
from src.core.query_jobs import execution_config
from pydantic import ValidationError

class SafetyStop(Exception):
    """A mandatory stage cannot produce a valid result."""

def normalized(text):
    text = text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return re.sub(r'[إأآٱ]', 'ا', text)

def requires_primary_text(analysis):
    """An A quotation request must be backed by primary Quran/authenticated hadith text."""
    return analysis.level.value=='LEVEL_A' and any(r.kind=='exact_quote'
        for part in analysis.question_parts for r in part.requirements)

def personal_ruling(query):
    q = normalized(query)
    return any(re.search(p, q, re.I) for p in load_policy()['personal_ruling_patterns'])

def interpreted_question(query):
    """Expand a configured informal question form without changing the stored question."""
    for rule in load_policy().get('question_forms',[]):
        match=re.fullmatch(rule['pattern'],query.strip())
        if match and normalized(match.group(1)) not in rule['excluded_subjects']:
            return re.sub(rule['pattern'],rule['replacement'],query.strip())
    return query

def provider_schema(contract):
    """Express literal constraints as single-value enums without relaxing them."""
    def convert(value):
        if isinstance(value,list):
            return [convert(item) for item in value]
        if isinstance(value,dict):
            result={key:convert(item) for key,item in value.items() if key!='const'}
            if 'const' in value:
                result['enum']=[value['const']]
            if 'keywords' in result.get('properties',{}):
                # Bound search concepts during generation, not only after the
                # provider repeatedly produces the same invalid long phrase.
                concepts=result['properties']['keywords']['additionalProperties']
                concepts.update(minItems=1,maxItems=load_policy()['max_keywords'])
                concepts['items'].update(pattern=r'^\S+(?:\s+\S+){0,2}$',maxLength=80)
            for field in ('question_parts','coverage','requirements'):
                if field in result.get('properties',{}):
                    result.setdefault('required',[]).append(field)
                    result['properties'][field]['minItems']=1
                    if field=='requirements':result['properties'][field].pop('maxItems',None)
            return result
        return value
    return convert(contract.model_json_schema())

class AnalysisAgent:
    def __init__(self, deadline):
        self.deadline = deadline
        self.policy = load_policy()

    def check_quota(self):
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from src.core.model_router import QuotaLedger, RoutingStop
        try:
            QuotaLedger().ensure_available()
        except RoutingStop as exc:
            raise SafetyStop(str(exc)) from exc

    def request(self, instruction, payload, contract):
        from dotenv import load_dotenv
        from src.core.model_router import QuotaLedger, RoutingStop, models_for
        load_dotenv(ROOT / '.env')
        key = os.getenv('GEMINI_API_KEY', '')
        if not key or key == 'paste_your_key_here':
            raise SafetyStop('analysis_unavailable')
        from google import genai
        from google.genai import types
        ledger = QuotaLedger()
        schema = provider_schema(contract)
        request_payload = payload
        attempted = set()
        failures = {}
        service_attempts = {}
        contract_attempts = {}
        retry_until = None
        retry_phase = False
        invalid = False
        last_failure = None
        repair = bool(payload.get('repair_instruction') or payload.get('rejection') or payload.get('contract_repair'))
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise SafetyStop('deadline_exceeded')
            models = models_for(contract, payload, ledger.config, repair=repair)
            retry_phase = False
            # Recover the normal pool before slow emergency models consume the
            # retry window. Every retry still reserves quota and honors cooldown.
            state=ledger.snapshot()['models']
            retryable={m for m in models if failures.get(m)=='service'
                and service_attempts.get(m,0)<ledger.config['service_attempts_per_model']
                and state[m]['remaining']>0 and not state[m]['disabled']}
            if (retryable and all(state[m]['remaining']==0 or state[m]['disabled'] or m in attempted for m in models)
                    and (retry_until is None or time.monotonic()<retry_until)):
                if retry_until is None:
                    retry_until=min(self.deadline,time.monotonic()+ledger.config['service_retry_window_seconds'])
                attempted.difference_update(retryable)
                retry_phase=True
            # Service failures exhaust this request's usable Lite candidates too.
            # RPM/TPM alone must not drain scarce Flash budgets.
            if not retry_phase and not (payload.get('level')=='LEVEL_C' and contract.__name__ in ledger.config['sensitive_contracts']):
                state=ledger.snapshot()['models']
                if all(state[m]['remaining']==0 or state[m]['disabled'] or
                        failures.get(m) in ('service','unavailable') for m in models):
                    models=list(dict.fromkeys(models+ledger.config['pools']['strong']))
            try:
                model = ledger.acquire(models, contract.__name__,
                    min(self.deadline,retry_until) if retry_phase else self.deadline, excluded=attempted)
            except RoutingStop as exc:
                reason = str(exc)
                if reason == 'model_service_unavailable':
                    # Retry the emergency pool only after its fresh alternatives.
                    state=ledger.snapshot()['models']
                    retryable={m for m in models if failures.get(m)=='service'
                        and service_attempts.get(m,0)<ledger.config['service_attempts_per_model']
                        and state[m]['remaining']>0 and not state[m]['disabled']}
                    if retryable and (retry_until is None or time.monotonic()<retry_until):
                        if retry_until is None:
                            retry_until=min(self.deadline,time.monotonic()+ledger.config['service_retry_window_seconds'])
                        attempted.difference_update(retryable)
                        retry_phase=True
                        continue
                    if invalid:reason = 'invalid_model_response'
                    elif last_failure=='rate':reason = 'model_rate_limited'
                raise SafetyStop(reason) from exc
            retry_phase = service_attempts.get(model,0)>0
            attempted.add(model)
            client = None
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise SafetyStop('deadline_exceeded')
                if retry_phase:
                    remaining=min(remaining,max(0,retry_until-time.monotonic()))
                    if remaining<=0:raise SafetyStop('model_service_unavailable')
                # A slow, healthy generation should finish rather than be
                # restarted at 18 seconds. Optional discovery still has its
                # own short deadline; transport stalls remain recoverable.
                attempt_timeout=(execution_config()['model_attempt_max_seconds'] if math.isinf(self.deadline)
                    else self.policy['model_timeout_seconds'] * (1 + service_attempts.get(model,0)))
                client = genai.Client(api_key=key, http_options=types.HttpOptions(
                    timeout=int(max(ledger.config['provider_minimum_timeout_seconds'],
                        min(remaining,attempt_timeout)) * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                    # extra_body supports thinkingLevel even in older SDK types.
                    extra_body=({'generationConfig':{'thinkingConfig':{'thinkingLevel':
                        ledger.config['thinking_levels'][model]}}}
                        if model in ledger.config.get('thinking_levels',{}) else None)))
                response = client.models.generate_content(model=model,
                    contents=json.dumps(request_payload,ensure_ascii=False),
                    config=types.GenerateContentConfig(system_instruction=instruction+
                        '\nRequired JSON output contract: return valid JSON matching the supplied response schema exactly.',
                        temperature=0,response_mime_type='application/json',
                        response_json_schema=schema))
                if time.monotonic() >= self.deadline:
                    raise SafetyStop('deadline_exceeded')
                result = contract.model_validate_json(response.text)
                if contract is QueryAnalysis and (not result.question_parts or any(not p.requirements for p in result.question_parts)):
                    raise ValueError('Question decomposition is required')
                return result
            except SafetyStop:
                raise
            except (ValidationError,ValueError) as exc:
                invalid = True
                contract_attempts[model]=contract_attempts.get(model,0)+1
                if len(ledger.config['models'])==1:
                    if contract_attempts[model]>=ledger.config.get('contract_attempts_per_model',2):
                        raise SafetyStop('invalid_model_response') from exc
                    attempted.discard(model)
                repair = True
                request_payload={**payload,'contract_repair':
                    exc.errors(include_input=False,include_context=False) if isinstance(exc,ValidationError)
                    else [{'type':'invalid_json_or_required_fields'}]}
            except Exception as exc:
                last_failure = ledger.failure(model, exc)
                failures[model] = last_failure
                if last_failure=='access':
                    raise SafetyStop('model_access_denied') from exc
                if last_failure=='billing':
                    raise SafetyStop('model_billing_unavailable') from exc
                if last_failure=='service':
                    code=getattr(exc,'code',None) or getattr(exc,'status_code',None)
                    # Retry only transport failures and temporary server errors;
                    # malformed requests/authentication need a real repair.
                    if code is None or str(code) in ('500','502','503','504'):
                        service_attempts[model]=service_attempts.get(model,0)+1
                    else:
                        service_attempts[model]=ledger.config['service_attempts_per_model']
            finally:
                if client is not None:
                    client.close()

    def analyze(self, inp):
        registry = json.loads((ROOT / 'configs/sources_registry.json').read_text(encoding='utf-8'))
        payload={'query':interpreted_question(inp.query),'original_question':inp.query,
            'output_language':inp.target_language,'max_keywords':self.policy['max_keywords'],'max_words_per_keyword':3}
        instruction=self.policy['analysis_instruction']+'\nOfficial definitions:\n'+json.dumps(registry['response_levels'],ensure_ascii=False)
        for attempt in range(self.policy['analysis_attempts']):
            result=self.request(instruction,payload,QueryAnalysis)
            try:
                result.keywords=self.prepare_keywords(result.keywords)
                for part in result.question_parts:
                    part.keywords=self.prepare_keywords(part.keywords)
                self.validate_keywords(result.keywords,expanded=True)
                for part in result.question_parts:
                    self.validate_keywords(part.keywords,expanded=True)
                self.validate_question_scope(inp,result)
                if result.confidence>=self.policy['classification_minimum_confidence'] and not result.needs_clarification:
                    return result
            except SafetyStop as exc:
                if attempt==self.policy['analysis_attempts']-1:
                    raise
                payload={**payload,'rejection':str(exc)}
            payload={**payload,'previous_interpretation':result.model_dump(mode='json'),
                'repair_instruction':self.policy['analysis_repair_instruction']+'\n'+
                    self.policy['question_scope']['repair_instruction']}
        raise SafetyStop('classification_uncertain')

    def validate_question_scope(self,inp,analysis):
        """Reject added mandatory comparisons, never silently erase user scope."""
        scope=self.policy['question_scope']
        requirements=[r for p in analysis.question_parts for r in p.requirements]
        quote_cues=scope.get('quote_request_cues',{}).get(analysis.question_language)
        if (quote_cues and re.search(quote_cues,normalized(inp.query),re.I)
                and not any(r.kind=='exact_quote' for r in requirements)):
            raise SafetyStop('requested_quotation_missing')
        cues=scope['comparison_cues'].get(analysis.question_language)
        if (any(r.kind=='comparison' for r in requirements) and cues
                and not re.search(cues,normalized(inp.query),re.I)):
            raise SafetyStop('unrequested_comparison')

    def prepare_keywords(self,keywords):
        """Split a model's long search concept into overlapping short concepts.

        Search metadata only: preserve every word, never edit original sources,
        question meanings, classifications or answer content.
        """
        result={}
        for language,concepts in keywords.items():
            if len(concepts)>self.policy['max_keywords']:
                raise SafetyStop('invalid_keywords')
            short=[]
            for concept in concepts:
                words=concept.split()
                if len(words)>self.policy['max_keyword_input_words']:
                    raise SafetyStop('invalid_keywords')
                if len(words)<=3:
                    short.append(concept.strip())
                else:
                    short.extend(' '.join(words[i:i+3]) for i in range(0,len(words)-2,2))
                    if (len(words)-3)%2:
                        short.append(' '.join(words[-3:]))
            result[language]=list(dict.fromkeys(short))
        return result

    def validate_keywords(self,keywords,expanded=False):
        if not keywords.get('ar') or not keywords.get('en'):
            raise SafetyStop('missing_keywords')
        for language, words in keywords.items():
            if not words or len(words) > self.policy['max_expanded_keywords' if expanded else 'max_keywords'] or any(
                    not word.strip() or len(word.split()) > 3 or len(word) > 80 for word in words):
                raise SafetyStop('invalid_keywords')

    def expand_search(self,inp,analysis,candidates):
        history = getattr(self, 'search_history', [])
        current = [p.keywords for p in analysis.question_parts] or [analysis.keywords]
        history.append(current)
        self.search_history = history
        payload={'question':interpreted_question(inp.query),'original_question':inp.query,'level':analysis.level.value,'previous_keywords':analysis.keywords,'max_keywords':self.policy['max_keywords'],'max_words_per_keyword':3,
             'question_parts':[p.model_dump(mode='json') for p in analysis.question_parts],
             'missing_parts':getattr(self,'missing_parts',list(range(len(analysis.question_parts)))),
             'coverage_feedback':getattr(self,'selection_feedback',None), 'search_history':history,
             'previous_candidates':[{'reference':c.reference_id,'passages':c.translations} for c in candidates]}
        for attempt in range(self.policy['analysis_attempts']):
            result=self.request(self.policy['search_expansion_instruction'],payload,SearchExpansion)
            try:
                parts=analysis.question_parts
                if parts:
                    if sorted(p.part_id for p in result.part_searches)!=list(range(len(parts))):
                        raise SafetyStop('invalid_search_plan')
                    parts=list(parts)
                    for search in result.part_searches:
                        search.keywords=self.prepare_keywords(search.keywords)
                        self.validate_keywords(search.keywords,expanded=True)
                        # Retain original subject anchors. Refinement must not
                        # drift from the actual topic into generic qualifications.
                        anchors=history[0][search.part_id]
                        combined={l:list(dict.fromkeys(anchors[l][:1]+search.keywords[l]))
                            for l in ('ar','en')}
                        self.validate_keywords(combined,expanded=True)
                        parts[search.part_id]=parts[search.part_id].model_copy(update={'keywords':combined})
                    # Global concepts are a summary only; retain every aspect's
                    # complete validated concepts in its own search plan.
                    keywords={l:list(dict.fromkeys(word for part in parts for word in part.keywords[l]))[:self.policy['max_keywords']]
                        for l in ('ar','en')}
                else:
                    keywords=result.keywords
                self.validate_keywords(keywords)
                # Require an actual new discovery concept for each unresolved
                # aspect, not another round with the same failed search plan.
                def signature(concept):
                    return tuple(sorted(normalized(concept).split()))
                next_groups=[p.keywords for p in parts] or [keywords]
                missing=getattr(self,'missing_parts',list(range(len(next_groups))))
                for index in missing:
                    seen={signature(k) for previous in history for group in previous[index:index+1]
                        for concepts in group.values() for k in concepts}
                    if not any(signature(k) not in seen for concepts in next_groups[index].values() for k in concepts):
                        raise SafetyStop('repeated_search_plan')
                return analysis.model_copy(update={'keywords':keywords,'question_parts':parts})
            except SafetyStop as exc:
                if attempt==self.policy['analysis_attempts']-1:
                    raise
                payload={**payload,'invalid_keywords':result.keywords,
                    'rejection':str(exc), 'repair_instruction':'Return genuinely new concepts for missing parts. Every concept must contain at most 3 words and each language at most '+str(self.policy['max_keywords'])+' concepts. Keep the question and level unchanged.'}

    def select(self, inp, analysis, candidates):
        if len(analysis.question_parts)>1:
            chosen=[]
            missing=[]
            feedback=[]
            def assess(part):
                # Isolated feedback prevents one part overwriting another's
                # coverage while independent requests are running together.
                worker=copy.copy(self);worker.selection_feedback=None
                focused=analysis.model_copy(update={'question_parts':[part]})
                try:return worker.select_part(inp,focused,candidates,part.question),None,worker.selection_feedback
                except SafetyStop as exc:return [],exc,worker.selection_feedback
            pool=ThreadPoolExecutor(max_workers=self.policy['evidence_workers'])
            tasks=[pool.submit(assess,part) for part in analysis.question_parts]
            try:
                for i,task in enumerate(tasks):
                    try:selected,error,assessment=task.result(timeout=None if math.isinf(self.deadline)
                        else max(0,self.deadline-time.monotonic()))
                    except FutureTimeout:raise SafetyStop('deadline_exceeded')
                    if error:
                        if str(error)!='no_direct_answer':raise error
                        missing.append(i)
                    chosen.extend(c for c in selected if c not in chosen)
                    feedback.append({'part_id':i,'assessment':assessment})
            finally:
                pool.shutdown(wait=False,cancel_futures=True)
            self.missing_parts=missing
            self.selection_feedback=feedback
            if missing:
                raise SafetyStop('no_direct_answer')
            return chosen
        return self.select_part(inp,analysis,candidates)

    def select_part(self, inp, analysis, candidates, assessment_question=None):
        repair_feedback=None
        for attempt in range(self.policy['analysis_attempts']):
            try:
                return self._select_part_once(inp,analysis,candidates,assessment_question,repair_feedback)
            except SafetyStop as exc:
                if str(exc)!='invented_evidence_witness' or attempt==self.policy['analysis_attempts']-1:
                    raise
                repair_feedback='The previous evidence witness was not an exact substring of its source. Reassess every candidate and coverage requirement. Select valid server witness_spans IDs with empty source_text, preserving exact punctuation and diacritics through server restoration. Never reconstruct or paraphrase a witness; mark unsupported if no actual passage supports it.'

    def _select_part_once(self, inp, analysis, candidates, assessment_question=None, repair_feedback=None):
        payload=self.selection_payload(inp,analysis,candidates,assessment_question)
        if repair_feedback:
            payload['repair_instruction']=repair_feedback
        for attempt in range(self.policy['analysis_attempts']):
            result = self.request(self.policy['selection_instruction'],payload,EvidenceSelection)
            ids = [d.candidate_id for d in result.decisions]
            expected_coverage={(i,l) for i in range(len(analysis.question_parts)) for l in required_languages(inp.target_language)}
            got=[(c.part_id,c.language) for c in result.coverage]
            complete_ids=sorted(ids)==list(range(len(candidates)))
            complete_coverage=not analysis.question_parts or (len(got)==len(expected_coverage) and set(got)==expected_coverage)
            if complete_ids and complete_coverage:
                break
            if attempt==self.policy['analysis_attempts']-1:
                raise SafetyStop('incomplete_relevance_assessment' if not complete_ids else 'incomplete_evidence_coverage')
            payload={**payload,'previous_assessment':result.model_dump(mode='json'),
                'repair_instruction':'Repair the incomplete assessment. Return every candidate ID exactly once and every required part/language coverage row exactly once. Preserve honest support judgments; do not approve unrelated evidence to satisfy the contract.',
                'required_candidate_ids':list(range(len(candidates)))}
        return self.validate_selection(inp,analysis,candidates,result)

    def selection_payload(self,inp,analysis,candidates,assessment_question=None):
        return {'question':assessment_question or interpreted_question(inp.query),'original_question':inp.query,
             'assessment_scope':'Only the question and question_parts in THIS request. Original question supplies context; other aspects are assessed separately.' if assessment_question else 'Whole question.',
             'level':analysis.level.value,'knowledge_level':analysis.knowledge_level.value,
             'question_parts':[{'part_id':i,**p.model_dump(mode='json')} for i,p in enumerate(analysis.question_parts)],
             'required_languages':required_languages(inp.target_language),
             'support_threshold':self.policy['selection_minimum_confidence'],'candidates':[
                {'candidate_id':i,'repository':c.repository.value,'reference':c.reference_id,
                 **({'dictionary_entry':{l:json.loads(meta) for l,meta in c.published_attribution.items()}}
                    if c.content_kind=='dictionary' else {}),
                 'witness_spans':{l:[{'span_id':j,'text':text} for j,text in enumerate(self.witness_spans(c.text_for(l)))]
                     for l in required_languages(inp.target_language)},
                 'source_languages':{l:c.evidence_language(l) for l in required_languages(inp.target_language)},
                 'grade':c.scholarly_grading,'kind':c.content_kind}
                for i,c in enumerate(candidates)]}

    def witness_spans(self,original):
        """Lossless navigation IDs, never model-written evidence or shortened originals."""
        limit=self.policy['witness_span_characters']
        spans=[]
        for sentence in re.findall(r'.+?(?:[.!?؟؛](?=\s)|\n|$)',original,re.S):
            while len(sentence)>limit:
                end=sentence.rfind(' ',0,limit)
                end=end+1 if end>0 else limit
                spans.append(sentence[:end]);sentence=sentence[end:]
            if sentence:spans.append(sentence)
        return spans

    def validate_selection(self,inp,analysis,candidates,result):
        self.selection_feedback=result.model_dump(mode='json')
        ids = [d.candidate_id for d in result.decisions]
        if sorted(ids) != list(range(len(candidates))):
            raise SafetyStop('incomplete_relevance_assessment')
        if analysis.level.value == 'LEVEL_C' and not result.explains_disagreement:
            self.missing_parts=list(range(len(analysis.question_parts)))
            raise SafetyStop('missing_scholarly_qualification')
        chosen_ids = [d.candidate_id for d in sorted(result.decisions,key=lambda d:d.confidence,reverse=True)
            if d.direct_answer and d.confidence >= self.policy['selection_minimum_confidence']]
        if analysis.question_parts:
            expected={(i,l) for i in range(len(analysis.question_parts)) for l in required_languages(inp.target_language)}
            got=[(c.part_id,c.language) for c in result.coverage]
            if len(got)!=len(expected) or set(got)!=expected:
                raise SafetyStop('incomplete_evidence_coverage')
            self.missing_parts=sorted({c.part_id for c in result.coverage if not c.supported or
                c.confidence<self.policy['selection_minimum_confidence'] or not c.candidate_ids})
            for coverage in result.coverage:
                if (len(set(coverage.candidate_ids))!=len(coverage.candidate_ids) or
                        any(i<0 or i>=len(candidates) for i in coverage.candidate_ids)):
                    raise SafetyStop('invalid_evidence_coverage')
                if any(i not in chosen_ids for i in coverage.candidate_ids):
                    self.missing_parts=sorted(set(self.missing_parts+[coverage.part_id]))
                requirements=analysis.question_parts[coverage.part_id].requirements
                if requirements and coverage.supported:
                    witnessed={w.requirement_id for w in coverage.witnesses}
                    if witnessed!=set(range(len(requirements))):
                        self.missing_parts=sorted(set(self.missing_parts+[coverage.part_id]))
                    for witness in coverage.witnesses:
                        if witness.candidate_id not in coverage.candidate_ids or witness.requirement_id>=len(requirements):
                            raise SafetyStop('invalid_evidence_coverage')
                        c=candidates[witness.candidate_id]
                        original=c.text_for(coverage.language)
                        if witness.source_span_id is not None:
                            spans=self.witness_spans(original)
                            if witness.source_span_id>=len(spans):
                                raise SafetyStop('invented_evidence_witness')
                            exact=spans[witness.source_span_id]
                            if witness.source_text and witness.source_text!=exact:
                                raise SafetyStop('invented_evidence_witness')
                            witness.source_text=exact
                        if not witness.source_text.strip() or witness.source_text not in original:
                            raise SafetyStop('invented_evidence_witness')
                        requirement=requirements[witness.requirement_id]
                        if (requirement.kind=='exact_quote' and c.content_kind not in ('quran','hadith')
                                and re.search(self.policy['question_scope']['primary_text_cues'],
                                    normalized(requirement.description),re.I)):
                            self.missing_parts=sorted(set(self.missing_parts+[coverage.part_id]))
                        if requirement.kind=='exact_quote' and c.content_kind in ('quran','hadith'):
                            originals=[p.text for p in c.passages_for(coverage.language) if p.kind==c.content_kind] or [original]
                            if not any(witness.source_text in text for text in originals):
                                self.missing_parts=sorted(set(self.missing_parts+[coverage.part_id]))
            if self.missing_parts:
                raise SafetyStop('no_direct_answer')
            # Publish only evidence actually supporting the requested aspects.
            used={i for coverage in result.coverage for i in coverage.candidate_ids}
            chosen_ids=[i for i in chosen_ids if i in used]
        # Note: fully_answers_question is a descriptive summary from the LLM,
        # not the primary completeness gate. The real gate is missing_parts above
        # (lines 441-479) which checks every part and requirement individually.
        # We only require chosen_ids to be non-empty — if all parts are satisfied
        # by the coverage checks, the answer is publishable.
        if not chosen_ids:
            raise SafetyStop('no_direct_answer')
        return [candidates[i] for i in chosen_ids]
