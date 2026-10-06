"""Export synthetic acceptance evidence for a real independent human review.

This creates a pending review package, never scholarly approval. File hashes
detect later changes; they do not establish reviewer identity or qualifications.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def prepare(run_directory, destination):
    run_directory=Path(run_directory)
    destination=Path(destination)
    run=json.loads((run_directory/'run.json').read_text())
    if run.get('mode')!='live' or not run.get('finished_at'):
        raise ValueError('A completed real live run is required.')
    if destination.exists():
        raise ValueError('Use a new package directory to preserve previous evidence.')
    destination.mkdir(parents=True)
    files={}
    names=['run.json','manifest.json','assertions.json']
    names += [p.name for pattern in ('query-*.json','answer-*.json')
        for p in sorted(run_directory.glob(pattern))]
    for name in sorted(set(names)):
        source=run_directory/name
        shutil.copyfile(source,destination/name)
        files[name]=hashlib.sha256(source.read_bytes()).hexdigest()
    cases=[]
    for query in sorted(destination.glob('query-*.json')):
        number=query.stem.split('-')[1]
        answer=destination/f'answer-{number}.json'
        cases.append({'query_file':query.name,'answer_file':answer.name,
            'answer_available':answer.exists(),'decision':'PENDING',
            'classification':'PENDING','claim_entailment':'PENDING',
            'canonical_terminology':'PENDING','consensus_and_qualifications':'PENDING',
            'multilingual_equivalence':'PENDING','originals_and_attribution':'PENDING',
            'findings':[]})
    review={'status':'PENDING_INDEPENDENT_SCHOLARLY_REVIEW',
        'reviewer_name':None,'institution':None,'qualifications':None,
        'languages_reviewed':[],'independence_statement':None,
        'review_date':None,'signed_review_reference':None,'cases':cases}
    (destination/'review-form.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
    (destination/'evidence-hashes.json').write_text(json.dumps(files,indent=2))
    (destination/'REVIEW.md').write_text('''# Independent scholarly review — pending

This package contains synthetic acceptance requests, exact answers and publisher
passages, source URLs, language metadata, the live scorecard and code/configuration
fingerprints. Model stages and terminology audits are diagnostic proposals, not
human scholarly approval. Failed or unavailable answers are part of the review.

A qualified reviewer independent of implementation must verify every case:

1. A/B/C/D classification, personal-ruling referrals and honest abstentions.
2. Every religious claim against its exact cited passage, including negation,
   context, qualifications and any unsupported comparative implication.
3. Canonical terminology against the protected lexicon; publisher dictionary
   originals must be evidence, not glossary text presented as publisher evidence.
4. Consensus claims and differing scholarly positions, with explicit attribution.
5. Arabic, English and other requested languages for meaning and qualifications;
   native Arabic evidence must not be labelled a published foreign translation.
6. Complete scripture quotations, publisher translations, source provenance and
   immutable originals. AI-generated scripture translations are prohibited.

Record qualifications, language competence, independence, case findings and
required corrections in review-form.json. Supply a signed review through the
project owner. File hashes detect changed evidence; they do not authenticate a
reviewer or confer qualifications. No approval is inferred from an empty form,
a passing benchmark, an AI review, or a self-declared reviewer identity.

Any correction to code, configuration, sources or answers needs fresh affected
evaluation and reviewer reconsideration. This finite package cannot certify
universal correctness or availability. No reviewer has been contacted by export.
''')
    return review


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory');parser.add_argument('destination')
    args=parser.parse_args()
    result=prepare(args.run_directory,args.destination)
    print(json.dumps({'status':result['status'],'cases':len(result['cases'])}))
