"""Published wording must survive inspection; unsupported languages stay blocked."""
from src.agents.terminology_preserver import TerminologyPreserverAgent
import pytest

def test_english_distortion_is_held_without_rewriting():
    words='Zakah is an Islamic tax. Jihad is holy war.'
    text,report=TerminologyPreserverAgent().audit_published_text(words,'en')
    assert text==words and report['status']=='FLAGGED_FOR_HUMAN_REVIEW'
    assert not report['deterministic_fixes_applied']

def test_french_distortion_is_held_without_rewriting():
    words="La peur d'Allah et l'impôt islamique."
    text,report=TerminologyPreserverAgent().audit_published_text(words,'fr',['taqwa','zakah'])
    assert text==words and report['status']=='FLAGGED_FOR_HUMAN_REVIEW'

def test_missing_swahili_rules_cannot_claim_safe():
    words='Muumini lazima ajue.'
    text,report=TerminologyPreserverAgent().audit_published_text(words,'sw')
    assert text==words and not report['coverage_available']
    assert report['status']=='FLAGGED_FOR_HUMAN_REVIEW'

def test_arabic_publisher_words_are_never_deleted():
    words='نص منشور (published note)'
    text,report=TerminologyPreserverAgent().audit_published_text(words,'ar')
    assert text==words and report['deterministic_fixes_applied']==[]

def test_explicit_missing_term_language_rules_cannot_borrow_english(tmp_path):
    import json
    path=tmp_path/'lexicon.json'
    path.write_text(json.dumps({'terms':{'sample':{'translations':{
        'en':{'canonical':'English sample','forbidden_substitutes':[]}}}}}))
    agent=TerminologyPreserverAgent(path)
    for language in ('sw','ur','zh'):
        original='Unreviewed language wording.'
        unchanged,report=agent.audit_published_text(original,language,found_terms=['sample'])
        assert unchanged==original
        assert report['coverage_available'] is False
        assert report['status']=='FLAGGED_FOR_HUMAN_REVIEW'

@pytest.mark.parametrize('words',[
    'Unlike conventional notions of random luck, Barakah is a blessing from Allah.',
    'Barakah is a blessing rather than random luck.',
    'Barakah differs fundamentally from the concept of good luck.',
    'While luck implies random chance or secular fortune, Barakah is rooted in divine provenance.',
    'Unlike worldly good luck, Barakah is neither haphazard nor independent of divine will.',
    'Barakah is fundamentally different from random chance or good luck.',
])
def test_explicit_contrast_is_preserved_without_rewriting(words):
    unchanged,report=TerminologyPreserverAgent().audit_published_text(words,'en')
    assert unchanged==words and report['status']=='APPROVED_SAFE'
    assert report['deterministic_fixes_applied']==[]

@pytest.mark.parametrize('words',[
    'Unlike wisdom, Barakah is random luck.',
    'Unlike luck, Barakah is merely luck.',
    'Unlike conventional notions of random luck, Barakah requires luck.',
    'While luck implies chance, Barakah is luck.',
    'Barakah differs from luck but Barakah is good luck.',
])
def test_contrast_cannot_hide_a_later_affirmative_substitute(words):
    unchanged,report=TerminologyPreserverAgent().audit_published_text(words,'en')
    assert unchanged==words and report['status']=='FLAGGED_FOR_HUMAN_REVIEW'
