"""Bounded evidence reselection after a separate explanation review rejects.

Source lock: docs/guides/10_immutability_constitution_and_source_lock.md.
Feedback is navigation, never evidence. All original selection/review gates remain.
"""
import copy
from src.core.analysis_agent import SafetyStop


def reselect_reviewed_answer(analyzer,localizer_factory,deadline,inp,analysis,candidates,languages,persona,feedback):
    retry=copy.copy(analyzer)
    request=retry.request
    def with_feedback(instruction,payload,contract):
        return request(instruction,{**payload,
            'repair_instruction':'The separate explanation review rejected completeness or claim support. Reassess the COMPLETE requested scope against these exact candidates, including omitted aspects and qualifications. Select only genuinely supporting original passages with exact witnesses. The review feedback is not evidence. Never invent missing facts or infer agreement. Mark unsupported if the pool does not suffice.',
            'previous_explanation_review':feedback},contract)
    retry.request=with_feedback
    chosen=retry.select(inp,analysis,candidates)
    if not chosen or any(c not in candidates for c in chosen):
        raise SafetyStop('invalid_evidence_selection')
    writer=localizer_factory(deadline)
    versions=writer.localize(inp,analysis,chosen,languages,persona)
    return chosen,versions
