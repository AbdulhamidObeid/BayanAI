"""Refresh signed original passages outside the user request path.

Run PYTHONPATH=. python3 scripts/build_source_index.py. No question/answer pairs
are manufactured. The report records real ingestion failures and collection size.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from src.core.integrity import AuthenticatedStore
from src.core.source_index import SourceIndex
from src.core.official_sources import OfficialSources
from src.core.publication_reader import PublicationReader
from src.core.schema import QueryAnalysis, SourceCitation
from src.core.source_policy import ROOT


def main():
    config=json.loads((ROOT/'configs/source_ingestion.json').read_text())
    store=AuthenticatedStore();index=SourceIndex(store)
    report={'started_at':time.time(),'imported_cache':0,'fetched_passages':0,'failures':[],'collections':{}}
    with store.connect() as db:
        rows=db.execute("SELECT id,expires FROM records WHERE kind='source' AND expires>?",(time.time(),)).fetchall()
    refresh_originals=set()
    for record_id,expires in rows:
        try:
            saved=store.get('source',record_id)
            if saved:
                index.put(SourceCitation.model_validate(saved),ttl=max(0,expires-time.time()))
                report['imported_cache']+=1
                c=SourceCitation.model_validate(saved)
                if c.content_kind in ('quran','hadith'):
                    refresh_originals.add((c.content_kind,c.reference_id,tuple(c.translations)))
        except Exception as exc:report['failures'].append({'collection':'cache','code':type(exc).__name__})
    sources=OfficialSources(time.monotonic()+config['timeout_seconds'],store)
    sources.ingest_all=True
    sources.force_refresh=True
    sources.search_analysis=QueryAnalysis(level='LEVEL_B',confidence=1,knowledge_level='unknown',
        knowledge_reason='Publisher ingestion, no user knowledge inference.',question_language='ar',
        keywords={'ar':config['search_terms'],'en':[]},intent='index_originals',needs_clarification=False)
    reader=PublicationReader(sources);urls=set(config['article_urls'])
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        tasks={pool.submit(reader.search_articles,term):term for term in config['search_terms']}
        for task in as_completed(tasks):
            try:urls.update(url for _,url in task.result())
            except Exception as exc:report['failures'].append({'collection':'articles','code':type(exc).__name__})
        tasks={pool.submit(sources.get,'article',url,['ar']):url for url in urls}
        for task in as_completed(tasks):
            try:report['fetched_passages']+=len(task.result() or [])
            except Exception as exc:report['failures'].append({'collection':'articles','url':tasks[task],'code':type(exc).__name__})
    shamela_urls=set(config.get('shamela_urls',[]))
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        searches={pool.submit(reader.search_shamela,term):term for term in config['search_terms']}
        for task in as_completed(searches):
            try:shamela_urls.update(url for _,url in task.result())
            except Exception as exc:report['failures'].append({'collection':'shamela','code':type(exc).__name__})
        tasks={pool.submit(sources.get,'shamela',url,['ar']):url for url in shamela_urls}
        for task in as_completed(tasks):
            try:report['fetched_passages']+=len(task.result() or [])
            except Exception as exc:report['failures'].append({'collection':'shamela','url':tasks[task],'code':type(exc).__name__})
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        tasks={pool.submit(sources.get,kind,ref,list(languages)):(kind,ref)
            for kind,ref,languages in refresh_originals}
        for task in as_completed(tasks):
            try:
                if task.result():report['fetched_passages']+=1
            except Exception as exc:report['failures'].append({'collection':tasks[task][0],'code':type(exc).__name__})
    # Probe registered history originals honestly; a blocked collection is not empty.
    for url in config['history_urls']:
        try:
            sources.check_link(url)
            report['collections']['history']='reachable; parser not yet validated'
        except Exception as exc:
            report['collections']['history']='unavailable'
            report['failures'].append({'collection':'history','url':url,'code':str(exc)})
    with index.connect() as db:report['indexed_records']=db.execute('SELECT count(*) FROM passages').fetchone()[0]
    report['finished_at']=time.time()
    (store.directory/'source_ingestion_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    result=main()
    print(json.dumps({**{k:v for k,v in result.items() if k!='failures'},'failure_count':len(result['failures'])},ensure_ascii=False))
