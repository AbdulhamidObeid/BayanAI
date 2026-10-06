"""Official access policy, ISO language selection, and exact host validation."""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]

@lru_cache(maxsize=1)
def load_policy():
    policy=json.loads((ROOT / 'configs/trust_policy.json').read_text(encoding='utf-8'))
    policy['dictionary_retrieval']=json.loads((ROOT/'configs/dictionary_retrieval.json').read_text(encoding='utf-8'))
    policy['chapter_retrieval']=json.loads((ROOT/'configs/chapter_retrieval.json').read_text(encoding='utf-8'))
    policy['library_html']=json.loads((ROOT/'configs/library_html.json').read_text(encoding='utf-8'))
    explanation=json.loads((ROOT/'configs/explanation_policy.json').read_text(encoding='utf-8'))
    policy['beginner_sentence_word_limits']=explanation['beginner_sentence_word_limits']
    policy['beginner_opaque_patterns']=explanation['beginner_opaque_patterns']
    policy['question_scope']=json.loads((ROOT/'configs/question_scope.json').read_text(encoding='utf-8'))
    policy['analysis_instruction']+='\n'+policy['question_scope']['planning_instruction']
    policy['analysis_instruction']+='\n'+explanation['comparison_scope']
    policy['selection_instruction']+='\n'+explanation['native_prose']+'\n'+explanation['dictionary_selection']+'\n'+explanation['comparison_scope']
    policy['localization_instruction']+='\n'+explanation['native_prose']+'\n'+explanation['dictionary_writing']+'\n'+explanation['disagreement_writing']+'\n'+explanation['beginner_writing']+'\n'+explanation['comparison_scope']
    policy['explanation_review_instruction']+='\n'+explanation['native_prose']+'\n'+explanation['review_style']+'\n'+explanation['beginner_writing']+'\n'+explanation['disagreement_writing']+'\n'+explanation['comparison_scope']
    return policy

def validate_source_url(value):
    p = urlsplit(value)
    if (p.scheme != 'https' or p.hostname not in load_policy()['approved_hosts']
            or p.username or p.password or p.port not in (None, 443)
            or not p.path or p.path == '/' or p.fragment or any(c.isspace() for c in value)):
        raise ValueError('A direct HTTPS URL on an officially accredited host is required')
    return value

def required_languages(language):
    return ['ar'] if language == 'ar' else (['en','ar'] if language == 'en' else [language,'ar','en'])
