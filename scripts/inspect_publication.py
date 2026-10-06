"""Inspect a known publisher PDF without turning unreviewed extraction into evidence.

Run: python scripts/inspect_publication.py bayyanat_2024_ar /path/to/original.pdf
Outputs a provenance/extraction audit, never an approved religious answer.
"""
import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.core.source_policy import validate_source_url


def inspect(publication_id,path):
    import fitz
    policy=json.loads((ROOT/'configs/publications.json').read_text(encoding='utf-8'))
    metadata=policy['publications'][publication_id]
    validate_source_url(metadata['landing_url']);validate_source_url(metadata['pdf_url'])
    path=Path(path)
    if path.stat().st_size>policy['max_pdf_bytes']:
        raise ValueError('Publication exceeds the ingestion limit')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    holds=[]
    if digest!=metadata['observed_sha256']:
        holds.append('original_file_hash_mismatch')
    issues=[]
    with fitz.open(path) as document:
        if len(document)>policy['max_pdf_pages']:
            raise ValueError('Publication page limit exceeded')
        pages=len(document)
        for index,page in enumerate(document):
            words=page.get_text()
            private_glyphs=sum(unicodedata.category(c)=='Co' for c in words)
            if private_glyphs:
                issues.append({'pdf_page':index+1,'reason':'unmapped_private_font_glyphs','count':private_glyphs})
            if not words.strip():
                issues.append({'pdf_page':index+1,'reason':'no_extractable_text'})
    if any(i['reason']=='unmapped_private_font_glyphs' for i in issues):
        holds.append('publisher_font_mapping_requires_review')
    # Ordinary extraction can reverse Arabic ligatures or alter word ordering.
    # No guessed substitutions, OCR or LLM repairs are accepted as originals.
    holds.append('page_text_alignment_and_language_editions_not_reviewed')
    return {'publication_id':publication_id,'metadata':metadata,'pdf_pages':pages,
        'sha256':digest,'file_matches_observed_publisher_download':digest==metadata['observed_sha256'],
        'can_auto_publish':False,'hold_reasons':holds,'page_issues':issues,
        'next_step':'Obtain correctly encoded publisher text or review extracted passages against the original page images; align published language editions before ingestion.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('publication_id');parser.add_argument('pdf_path')
    args=parser.parse_args()
    report=inspect(args.publication_id,args.pdf_path)
    policy=json.loads((ROOT/'configs/publications.json').read_text())
    directory=ROOT/policy['artifact_directory'];directory.mkdir(parents=True,exist_ok=True)
    output=directory/(args.publication_id+'.audit.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(output),'pages':report['pdf_pages'],
        'auto_publish':report['can_auto_publish'],'hold_reasons':report['hold_reasons']}))


if __name__=='__main__':main()
