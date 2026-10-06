"""Recover useful supported evidence after complete-answer retrieval is exhausted.

The reduced scope still passes exact witnesses, independent full-prose review,
qualification, source integrity and terminology gates. No raw candidates are
published merely because an answer could not be completed.
"""
from src.core.analysis_agent import SafetyStop, personal_ruling
from src.core.schema import PartialEvidencePlan
from src.core.source_policy import load_policy


def coverage_only_rejection(localizer):
    """An incomplete answer may recover; unsupported claims are never reused."""
    feedback = getattr(localizer, 'review_feedback', None)
    if not isinstance(feedback, dict) or not feedback.get('checks'):
        return False
    flags = ('versions_agree', 'correct_languages', 'preserves_qualifications',
        'respectful_without_stereotypes', 'no_unmarked_quotes')
    return all(feedback.get(flag) is True for flag in flags) and all(
        c.get('supported') is True and c.get('confidence', 0) >= load_policy()['minimum_confidence']
        for c in feedback['checks'])


def recover_partial(analyzer, inp, analysis, candidates, languages):
    policy = load_policy()
    if (analysis.level.value not in policy['partial_answers']['levels']
            or personal_ruling(inp.query) or not candidates):
        raise SafetyStop('no_direct_answer')
    # A single requested primary quotation is indivisible: adjacent scripture
    # cannot stand in for the requested narration, verse or claimed wording.
    # Multi-part requests can still recover a genuinely supported original part.
    if len(analysis.question_parts)<=1 and any(r.kind=='exact_quote'
            for p in analysis.question_parts for r in p.requirements):
        raise SafetyStop('no_direct_answer')
    payload = analyzer.selection_payload(inp, analysis, candidates)
    # The original checklist is context, not the required coverage rows for a
    # reduced scope. Keeping "Whole question" here recreated the rejection.
    payload['original_question_parts']=payload.pop('question_parts')
    payload['previous_selection_feedback']=getattr(analyzer,'selection_feedback',None)
    payload['assessment_scope']='Propose useful supported_parts of the original question. Selection coverage and fully_answers_question assess ONLY those proposed supported_parts; disclose all remaining original aspects in unanswered_aspects.'
    return request_partial(analyzer, inp, analysis, candidates, languages, payload, policy)


def request_partial(analyzer, inp, analysis, candidates, languages, payload, policy):
    for attempt in range(policy['analysis_attempts']):
        plan = PartialEvidencePlan.model_validate(analyzer.request(
            policy['selection_instruction'] + '\nPartial recovery rules (override whole-question completeness for this request):\n'
            + policy['partial_selection_instruction'], payload, PartialEvidencePlan))
        try:
            return validate_partial(analyzer, inp, analysis, candidates, languages, plan, policy)
        except SafetyStop as exc:
            if str(exc) not in ('no_direct_answer','missing_scholarly_qualification') or attempt==policy['analysis_attempts']-1:
                raise
            payload={**payload,'previous_partial_plan':plan.model_dump(mode='json'),
                'validation_failure':str(exc),
                'repair_instruction':policy['partial_selection_repair_instruction']}


def validate_partial(analyzer, inp, analysis, candidates, languages, plan, policy):
    if not plan.supported_parts or any(not p.requirements for p in plan.supported_parts):
        raise SafetyStop('no_direct_answer')
    gaps = plan.unanswered_aspects
    if (set(gaps) != set(languages) or any(not items or
            len(items) > policy['partial_answers']['maximum_gaps'] or any(
                not item.strip() or len(item) > policy['partial_answers']['maximum_gap_characters']
                or 'http://' in item or 'https://' in item for item in items)
            for items in gaps.values())):
        raise SafetyStop('invalid_partial_scope')
    # Only the answer scope is reduced. Original classification, input and
    # complete original plan remain available to the independent reviewer.
    scoped = analysis.model_copy(update={'question_parts': plan.supported_parts})
    selected = analyzer.validate_selection(inp, scoped, candidates, plan.selection)
    context = {'mode': 'partial_answer',
        'original_question_parts': [p.model_dump(mode='json') for p in analysis.question_parts],
        'unanswered_aspects': gaps}
    return selected, scoped, context
