"""Evidence-bound explanation writer and separate full-draft review gate.

Published quotations never come from model output. The model returns reference
IDs; the server inserts complete retrieved passages. All prose needs evidence.
"""
import json
import re
from types import SimpleNamespace
from src.core.analysis_agent import AnalysisAgent, SafetyStop, interpreted_question, normalized
from src.core.schema import ExplanationDraft, ExplanationReview, PartialExplanationReview, AnswerVersion, ExplanationSegment, SourceBoundDraft
from src.core.source_policy import ROOT, load_policy


class CulturalLocalizerAgent:
    def __init__(self, deadline):
        self.client = AnalysisAgent(deadline)
        self.policy = load_policy()

    def localize(self, inp, analysis, citations, languages, persona, prepared_draft=None):
        if analysis.level.value == 'LEVEL_D' or not citations:
            raise SafetyStop('explanation_requires_evidence')
        payload=self.draft_payload(inp,analysis,citations,languages,persona)
        partial_context=getattr(self,'partial_context',None)
        instruction=self.policy['localization_instruction']
        if partial_context:
            instruction+='\nPartial answer rules:\n'+self.policy['partial_localization_instruction']
        for attempt in range(self.policy['explanation_attempts']):
            try:
                draft=(prepared_draft if attempt==0 and prepared_draft is not None else
                    ExplanationDraft.model_validate(self.client.request(
                        instruction,payload,ExplanationDraft)))
                if [v.language for v in draft.versions]!=languages:
                    raise SafetyStop('explanation_language_contract')
                versions=self.render_draft(draft,citations,languages,inp,persona)
                if partial_context:
                    for version in versions:
                        gaps=partial_context['unanswered_aspects'][version.language]
                        version.scope_notice=self.policy['messages'][version.language]['partial_notice']+'\n'+'\n'.join('• '+gap for gap in gaps)
                        version.text=version.scope_notice+'\n\n'+version.text
                self.audit_explanation(versions)
                self.audit_beginner_opening(inp,analysis,versions,payload)
                self.review(inp,analysis,versions,languages,citations,persona)
                return versions
            except SafetyStop as exc:
                repairable={'malformed_explanation_prose','unsupported_personalized_explanation',
                    'incomplete_explanation_review','missing_personalized_explanation','explanation_language_contract',
                    'unmarked_published_quote','draft_terminology_review_required','beginner_opening_requires_repair'}
                if attempt==self.policy['explanation_attempts']-1 or str(exc) not in repairable:
                    raise
                payload={**payload,'repair_instruction':self.policy['explanation_repair_instruction'],
                    'rejection':str(exc),'review_feedback':getattr(self,'review_feedback',None),
                    'terminology_feedback':getattr(self,'terminology_feedback',None),
                    'previous_draft':draft.model_dump(mode='json')}

    def audit_explanation(self,versions):
        """Repair generated prose before review; never repair original quotations."""
        from src.agents.terminology_preserver import TerminologyPreserverAgent
        preserver=TerminologyPreserverAgent()
        self.terminology_feedback=[]
        for version in versions:
            for segment in version.explanation_segments:
                if segment.kind!='explanation':continue
                _,audit=preserver.audit_published_text(segment.text,version.language)
                if audit['status']!='APPROVED_SAFE':
                    self.terminology_feedback.append(audit)
        if self.terminology_feedback:
            raise SafetyStop('draft_terminology_review_required')

    def audit_beginner_opening(self,inp,analysis,versions,payload):
        """A novice meets the plain concept before the unfamiliar requested term.

        Inspect only generated prose; aliases come from the unchanged lexicon.
        This is an ordering check, never a claim that the meaning is correct.
        """
        if analysis.knowledge_level.value!='beginner':return
        glossary=payload['canonical_terminology']['terms']
        def contains(text,alias):
            return bool(alias and re.search(r'(?<!\w)'+re.escape(normalized(alias))+r'(?!\w)',
                normalized(text),re.I))
        requested=[]
        for name,term in glossary.items():
            aliases=[name,term['arabic'],term['transliteration']]
            if any(contains(inp.query,alias) for alias in aliases):
                requested.append((aliases,term))
        for version in versions:
            prose=next((s.text for s in version.explanation_segments if s.kind=='explanation'),'')
            limit=self.policy['beginner_sentence_word_limits'].get(version.language)
            sentences=[sentence for s in version.explanation_segments if s.kind=='explanation'
                for sentence in re.split(r'[.!?؟。](?:\s|$)',s.text)]
            for sentence in sentences:
                if any(re.search(pattern,sentence,re.I) for pattern in self.policy['beginner_opaque_patterns'].get(version.language,[])):
                    self.review_feedback={'language':version.language,'offending_sentence':sentence,
                        'reason':'Replace opaque abstract labels with familiar direct words explaining the same supported concept. For a number use ordinary number-before-noun word order; for exclusivity use only or alone directly with the supported subject. Preserve meaning and qualifications; do not add a doctrine or definition.'}
                    raise SafetyStop('beginner_opening_requires_repair')
            if limit and any(len(sentence.split())>limit for sentence in sentences):
                self.review_feedback={'reason':'Use short ordinary sentences before technical labels. The configured maximum is '+str(limit)+' words per explanation sentence; preserve all supported meaning and qualifications.'}
                raise SafetyStop('beginner_opening_requires_repair')
            opening=re.split(r'[.!?؟。](?:\s|$)',prose,maxsplit=1)[0]
            for aliases,term in requested:
                canonical=term['translations'].get(version.language,{}).get('canonical','')
                if any(contains(opening,alias) for alias in aliases+[canonical]):
                    self.review_feedback={'language':version.language,'offending_sentence':opening,
                        'requested_term':canonical or term['arabic'],
                        'reason':'Repair this language specifically. Finish the plain concept sentence with sentence punctuation BEFORE naming the unfamiliar requested term. Introduce the term in a separate later sentence. A comma or conjunction inside the first sentence does not satisfy this order. Preserve all sourced meaning and qualifications.'}
                    raise SafetyStop('beginner_opening_requires_repair')

    def draft_payload(self,inp,analysis,citations,languages,persona):
        lexicon = json.loads((ROOT / 'configs/sharia_lexicon.json').read_text(encoding='utf-8'))
        payload = {'question': interpreted_question(inp.query), 'original_question':inp.query, 'level': analysis.level.value,
            'question_parts':[{'part_id':i,**p.model_dump(mode='json')} for i,p in enumerate(analysis.question_parts)],
            'knowledge_level': analysis.knowledge_level.value,
            'audience': persona.value, 'required_languages': languages,
            'canonical_terminology': {
                'source_standards':lexicon['source_standards'],
                'terms':{name:{'arabic':term['arabic'],
                    'transliteration':term.get('transliteration',''),
                    'translations':{language:{key:value for key,value in translation.items()
                        if key in ('canonical','approved_gloss','forbidden_substitutes')}
                        for language,translation in term.get('translations',{}).items()
                        if language in languages}}
                    for name,term in lexicon['terms'].items()}},
            'evidence': [{'citation_id': i, 'reference': c.reference_id,
                'kind': c.content_kind, 'grade': c.scholarly_grading,
                'source_languages':{l:c.evidence_language(l) for l in languages},
                **({'dictionary_entry':{l:json.loads(meta) for l,meta in c.published_attribution.items()}}
                    if c.content_kind=='dictionary' else {}),
                'published_passages': c.translations} for i, c in enumerate(citations)]}
        if getattr(self,'partial_context',None):
            payload['answer_scope']=self.partial_context
        return payload

    def select_and_draft(self,analyzer,inp,analysis,candidates,languages,persona):
        """One proposal request, then the unchanged server evidence gate.

        The independent review in localize still makes a separate request.
        Citation IDs are remapped only after selection passes; rejected evidence
        can never support an explanation merely because it appeared in discovery.
        """
        instruction=(self.policy['combined_draft_instruction']+'\nEvidence selection rules:\n'+
            self.policy['selection_instruction']+'\nExplanation rules:\n'+self.policy['localization_instruction'])
        writing=self.draft_payload(inp,analysis,candidates,languages,persona)
        # Selection already supplies every complete original. Repeating the same
        # evidence in writing wastes input tokens and slows the combined request.
        writing.pop('evidence')
        writing['max_explanation_words']=self.policy['combined_draft_max_words']*len(analysis.question_parts)
        payload={**analyzer.selection_payload(inp,analysis,candidates),'writing':writing}
        for attempt in range(self.policy['analysis_attempts']):
            result=SourceBoundDraft.model_validate(analyzer.request(instruction,payload,SourceBoundDraft))
            try:
                chosen=analyzer.validate_selection(inp,analysis,candidates,result.selection)
                break
            except SafetyStop as exc:
                if str(exc) not in ('invented_evidence_witness','incomplete_relevance_assessment',
                        'incomplete_evidence_coverage') or attempt==self.policy['analysis_attempts']-1:
                    raise
                payload={**payload,'repair_instruction':
                    'Reassess honestly. Return every candidate and coverage row exactly once. Select valid server witness_spans IDs with empty source_text; never reconstruct source text. Draft only from genuinely supporting evidence.',
                    'rejection':str(exc),'previous_assessment':result.selection.model_dump(mode='json')}
        mapping={i:chosen.index(c) for i,c in enumerate(candidates) if c in chosen}
        draft=result.draft.model_copy(deep=True)
        for version in draft.versions:
            for segment in version.segments:
                if any(i not in mapping for i in segment.citation_ids):
                    raise SafetyStop('unselected_explanation_citation')
                segment.citation_ids=[mapping[i] for i in segment.citation_ids]
        return chosen,draft

    def render_draft(self, draft, citations, languages, inp=None, persona=None):
        versions = []
        for version in draft.versions:
            paragraphs = []
            rendered = []
            if not any(s.kind == 'explanation' for s in version.segments):
                raise SafetyStop('missing_personalized_explanation')
            for index, segment in enumerate(version.segments):
                ids = segment.citation_ids
                if (len(set(ids)) != len(ids) or any(type(i) is not int or i < 0 or i >= len(citations) for i in ids)):
                    raise SafetyStop('invalid_explanation_citation')
                if segment.kind == 'quote':
                    if segment.text or len(ids) != 1:
                        raise SafetyStop('model_written_quote_rejected')
                    c = citations[ids[0]]
                    metadata=json.loads(c.published_attribution.get(c.source_language,'{}')) if c.content_kind in ('dictionary','article','book_excerpt') else {}
                    if metadata.get('contains_scripture') and version.language not in c.translations:
                        raise SafetyStop('untranslated_scripture_in_publication')
                    # Insert the complete original scripture/narration, not a model quotation.
                    words = c.passages_for(version.language)
                    text = words[0].text if words else c.text_for(version.language)
                else:
                    if not segment.text.strip() or 'http://' in segment.text or 'https://' in segment.text:
                        raise SafetyStop('invalid_explanation_prose')
                    style = self.policy['explanation_format']
                    if (re.search(style['inline_citation_pattern'], segment.text)
                            or re.search(style['fragment_start_pattern'], segment.text)
                            or not re.search(style['sentence_end_pattern'], segment.text)):
                        raise SafetyStop('malformed_explanation_prose')
                    if self.copies_scripture(segment.text,[citations[i] for i in ids],version.language):
                        raise SafetyStop('unmarked_published_quote')
                    text = segment.text
                rendered.append(ExplanationSegment(kind=segment.kind,text=text,citation_ids=ids))
                paragraphs.append(text)
            text = '\n\n'.join(s.text + (' ' + ' '.join('['+str(i+1)+']' for i in s.citation_ids) if s.kind=='explanation' else '') for s in rendered)
            if sum(len(s.text) for s in rendered if s.kind=='explanation') > self.policy['max_explanation_characters']:
                raise SafetyStop('explanation_too_long')
            av = AnswerVersion(language=version.language, text=text,
                source_text='\n\n'.join(c.text_for(version.language) for c in citations),
                source_urls=[c.url_for(version.language) for c in citations],
                passages=[p for c in citations for p in c.passages_for(version.language)],
                explanation_segments=rendered)
            # Translate Arabic-only source passages for non-Arabic output languages.
            # Quran and Hadith already have official publisher translations — never translated here.
            if version.language != 'ar':
                av.translated_passages = self.translate_arabic_only_passages(
                    version.language, citations, inp=inp, persona=persona)
                from src.agents.terminology_preserver import TerminologyPreserverAgent
                preserver=TerminologyPreserverAgent()
                for passages in av.translated_passages.values():
                    for passage in passages:
                        passage.text,_=preserver.correct_translated_text(passage.text,version.language)
            versions.append(av)
        return versions

    def translate_arabic_only_passages(self, target_language, citations, inp=None, persona=None):
        """Translate Arabic-only article/book_excerpt/dictionary passages via LLM.

        Only applies to content_kind in ('article', 'book_excerpt', 'dictionary').
        Quran and Hadith are NEVER translated here — they have official publisher translations.

        The translation is:
        - Terminology-aware: every term uses the canonical form from sharia_lexicon.json for the
          target language. If the language has no lexicon entry, falls back to English canonical form.
        - Region/persona-aware: cultural_context and persona are injected so the LLM produces
          language that is natural for that audience (e.g. Malay vs Indonesian vs Malaysian Arabic).
        - Linted post-generation: the orchestrator runs the terminology preserver on the output.

        Returns dict keyed by str(citation_index) → List[SourcePassage].
        """
        from src.core.schema import SourcePassage, PassageTranslationResult
        lexicon = json.loads((ROOT / 'configs/sharia_lexicon.json').read_text(encoding='utf-8'))
        translatable_kinds = ('article', 'book_excerpt', 'dictionary')

        # Gather citations that need translation: Arabic-only, not already in target language
        to_translate = {}  # citation_index -> list of SourcePassage (Arabic)
        for idx, c in enumerate(citations):
            if c.content_kind not in translatable_kinds:
                continue
            if any(json.loads(meta).get('contains_scripture') for meta in c.published_attribution.values()):
                continue
            if target_language in c.translations:
                continue  # already has a native translation, no LLM call needed
            ar_passages = c.passages.get('ar', [])
            if not ar_passages:
                ar_text = c.translations.get('ar', '')
                if ar_text.strip():
                    ar_passages = [SourcePassage(kind='commentary', text=ar_text,
                                                  reference_id=c.reference_id, source_url=c.source_url)]
            if ar_passages:
                to_translate[idx] = ar_passages

        if not to_translate:
            return {}

        # Build canonical terminology map for this target language.
        # For each term: use the target language entry; fall back to English if that language
        # has no lexicon entry (covers ur, sw, zh and any future language not yet in the lexicon).
        canonical_terms = {}
        for name, term in lexicon.get('terms', {}).items():
            translations = term.get('translations', {})
            lang_entry = translations.get(target_language) or translations.get('en', {})
            if lang_entry:
                canonical_terms[name] = {
                    'arabic': term.get('arabic', ''),
                    'transliteration': term.get('transliteration', ''),
                    'canonical': lang_entry.get('canonical', ''),
                    'approved_gloss': lang_entry.get('approved_gloss', ''),
                    'forbidden_substitutes': lang_entry.get('forbidden_substitutes', []),
                }

        # Regional / persona context to guide natural-sounding translation
        cultural_context = getattr(inp, 'cultural_context', 'general') if inp else 'general'
        audience = persona.value if persona else 'general'

        # Flatten all passages into a single ordered list for one batched LLM call
        items = []
        index_map = []  # (citation_idx, original_SourcePassage)
        for citation_idx, passages in to_translate.items():
            for passage in passages:
                items.append({'arabic_text': passage.text, 'kind': passage.kind})
                index_map.append((citation_idx, passage))

        payload = {
            'target_language': target_language,
            'cultural_context': cultural_context,
            'audience': audience,
            'canonical_terminology': canonical_terms,
            'passages': items,
        }

        instruction = self.policy.get('source_passage_translation_instruction', '')
        try:
            result = self.client.request(instruction, payload, PassageTranslationResult)
            translated_texts = result.translations
        except Exception:
            # Translation is best-effort — if it fails at all, fall back to showing Arabic original.
            # This never crashes the pipeline; Arabic remains fully available in the Arabic version.
            return {}

        if len(translated_texts) != len(index_map):
            # Mismatched count — do not partially apply; fall back to Arabic
            return {}

        result_map = {}
        for (citation_idx, original_passage), translated_text in zip(index_map, translated_texts):
            if not translated_text.strip():
                continue
            key = str(citation_idx)
            if key not in result_map:
                result_map[key] = []
            result_map[key].append(SourcePassage(
                kind=original_passage.kind,
                text=translated_text.strip(),
                reference_id=original_passage.reference_id,
                source_url=original_passage.source_url,
            ))
        return result_map


    def copies_scripture(self,text,citations,language):
        """Copied scripture must use a full server-inserted quote, not prose."""
        def words(value):
            return re.findall(r'[^\W_]+',normalized(value))
        prose=words(text)
        size=self.policy['verbatim_quote_window_words']
        windows={tuple(prose[i:i+size]) for i in range(len(prose)-size+1)}
        for citation in citations:
            passages=citation.passages.get(language,[])
            originals=[p.text for p in passages if p.kind in ('quran','hadith')]
            if not passages and citation.content_kind in ('quran','hadith'):
                originals=[citation.translations[language]]
            for original in originals:
                source=words(original)
                if len(source)>=self.policy['verbatim_quote_minimum_words'] and source==prose:
                    return True
                if any(tuple(source[i:i+size]) in windows for i in range(len(source)-size+1)):
                    return True
        return False

    def review(self,inp,analysis,versions,languages,citations,persona):
        # Generated commentary translations are prose too, never authenticated
        # publisher translations. Include each in the independent review gate.
        review_segments={v.language:list(v.explanation_segments) for v in versions}
        translation_checks={}
        for version in versions:
            for key,passages in version.translated_passages.items():
                index=int(key)
                if not 0<=index<len(citations):raise SafetyStop('invalid_explanation_citation')
                for passage in passages:
                    position=len(review_segments[version.language])
                    review_segments[version.language].append(SimpleNamespace(
                        kind='explanation',text=passage.text,citation_ids=[index]))
                    translation_checks[(version.language,position)]=True
        checks=[(l,i) for l,segments in review_segments.items() for i,s in enumerate(segments) if s.kind=='explanation']
        partial_context=getattr(self,'partial_context',None)
        contract=PartialExplanationReview if partial_context else ExplanationReview
        instruction=self.policy['explanation_review_instruction']
        if partial_context:
            instruction+='\nPartial review rules:\n'+self.policy['partial_review_instruction']
        if translation_checks:
            instruction+='\nChecks marked generated_commentary_translation must preserve ALL meaning and qualifications of the exact Arabic original, with no additions or omissions and canonical terminology. They are model translations, never published translations. Reject unsupported or misleading translation even when the answer prose is supported. Scripture translation remains forbidden. Supplemental translation check IDs are ONLY for checks; coverage and requirements must refer ONLY to IDs in draft.versions. Source-box translations cannot replace an actual answer or requested quotation.'
        # A second request audits the whole draft; generation cannot approve itself.
        review = contract.model_validate(self.client.request(
            instruction,
            {'question':interpreted_question(inp.query), 'original_question':inp.query, 'level':analysis.level.value, 'required_languages':languages,
             'question_parts':[{'part_id':i,**p.model_dump(mode='json')} for i,p in enumerate(analysis.question_parts)],
             'knowledge_level':analysis.knowledge_level.value, 'audience':persona.value,
             'canonical_terminology':self.draft_payload(inp,analysis,citations,languages,persona)['canonical_terminology'],
             **({'answer_scope':partial_context,'scope_notices':{v.language:v.scope_notice for v in versions}} if partial_context else {}),
             'required_checks':[{'language':l,'segment_id':i,
                'text':review_segments[l][i].text,
                **({'generated_commentary_translation':True} if (l,i) in translation_checks else {}),
                'cited_evidence':[{'citation_id':j, 'published_text':citations[j].text_for(l), **({'source_language':citations[j].evidence_language(l)} if citations[j].content_kind in ('article','book_excerpt','dictionary') else {})}
                    for j in review_segments[l][i].citation_ids]}
                for l,i in checks],
             'draft': {'versions':[{'language':v.language,
                'segments':[s.model_dump(mode='json') for s in v.explanation_segments]} for v in versions]}}, contract))
        self.review_feedback=review.model_dump(mode='json')
        got = [(c.language, c.segment_id) for c in review.checks]
        if sorted(got) != sorted(checks):
            raise SafetyStop('incomplete_explanation_review')
        if analysis.question_parts:
            expected={(i,l) for i in range(len(analysis.question_parts)) for l in languages}
            got=[(c.part_id,c.language) for c in review.coverage]
            if len(got)!=len(expected) or set(got)!=expected:
                raise SafetyStop('incomplete_explanation_review')
            for coverage in review.coverage:
                version=next(v for v in versions if v.language==coverage.language)
                requirements=analysis.question_parts[coverage.part_id].requirements
                if (not coverage.answered or not coverage.segment_ids or
                        len(set(coverage.segment_ids))!=len(coverage.segment_ids) or
                        any(i<0 or i>=len(version.explanation_segments) or
                            (not requirements and version.explanation_segments[i].kind!='explanation') for i in coverage.segment_ids)):
                    raise SafetyStop('unsupported_personalized_explanation')
                if requirements:
                    got=[r.requirement_id for r in coverage.requirements]
                    if sorted(got)!=list(range(len(requirements))):
                        raise SafetyStop('unsupported_personalized_explanation')
                    for delivered in coverage.requirements:
                        if not delivered.segment_ids or any(i not in coverage.segment_ids for i in delivered.segment_ids):
                            raise SafetyStop('unsupported_personalized_explanation')
                        if requirements[delivered.requirement_id].kind=='exact_quote' and not any(
                                version.explanation_segments[i].kind=='quote' for i in delivered.segment_ids):
                            raise SafetyStop('unsupported_personalized_explanation')
        flags = [review.fully_answers_question, review.versions_agree,
            review.correct_languages, review.preserves_qualifications,
            review.respectful_without_stereotypes, review.no_unmarked_quotes]
        if partial_context:
            flags.extend([review.partial_scope_valid,review.limitations_accurate])
        if not all(flags) or any(not c.supported or c.confidence < self.policy['minimum_confidence'] for c in review.checks):
            raise SafetyStop('unsupported_personalized_explanation')
