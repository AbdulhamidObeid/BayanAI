"""Local retrieval must preserve authenticated originals, expiry and safety gates."""
import time
from unittest.mock import Mock
import pytest
from src.core.integrity import AuthenticatedStore
from src.core.source_index import SourceIndex
from src.core.official_sources import OfficialSources
from src.core.publication_reader import PublicationReader
from src.core.orchestrator import MasterOrchestrator
from src.core.schema import SourceRepository
from tests.test_trust_pipeline import analysis, citation


def original():
    c=PublicationReader.citation('النص الأصلي المنشور عن موضوع الاختبار. '*6,
        'https://islamic-content.com/post/18220','ar',SourceRepository.AL_JAMHARAH,'article',1,{})
    c.provenance_verified=True;c.link_checked_at='checked';c.retrieval_endpoint=c.source_url
    c.source_digest=OfficialSources.digest(c)
    return c


def test_local_lookup_preserves_original_and_needs_no_network(tmp_path):
    store=AuthenticatedStore(tmp_path);c=original();SourceIndex(store).put(c)
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    source=OfficialSources(time.monotonic()+10,store)
    source.discover=Mock(side_effect=AssertionError('No remote discovery needed'))
    found=source.retrieve(a,['ar'])
    assert len(found)==1 and found[0].arabic_text==c.arabic_text
    assert found[0].source_digest==c.source_digest and found[0].is_offline_cached


def test_unverified_original_cannot_enter_index(tmp_path):
    c=original();c.provenance_verified=False
    with pytest.raises(Exception,match='unverified_source_contract'):
        SourceIndex(AuthenticatedStore(tmp_path)).put(c)


def test_expired_index_record_is_not_evidence(tmp_path):
    index=SourceIndex(AuthenticatedStore(tmp_path));index.put(original(),ttl=-1)
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    assert index.search(a,['ar'])==[]


def test_tampered_fts_cannot_supply_fabricated_evidence(tmp_path):
    store=AuthenticatedStore(tmp_path);index=SourceIndex(store);index.put(original())
    with store.connect() as db:db.execute("UPDATE records SET payload='{}' WHERE kind='indexed_source'")
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    with pytest.raises(ValueError,match='authentication'):index.search(a,['ar'])


def test_search_can_use_native_article_but_not_missing_scripture_translation(tmp_path):
    index=SourceIndex(AuthenticatedStore(tmp_path));index.put(original())
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    found=index.search(a,['en','ar'])
    assert found[0].text_for('en')==original().arabic_text
    c=citation(('ar',));index.put(c)
    a.keywords={'ar':['الله'],'en':[]}
    assert all(x.content_kind!='quran' for x in index.search(a,['en','ar']))


def test_observed_post_article_is_read_and_catalog_description_is_excluded(tmp_path):
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path));source.search_analysis=analysis()
    source.search_analysis.keywords={'ar':['النص'],'en':[]}
    reader=PublicationReader(source);text='النص الأصلي الكامل دون تعديل. '*8
    html='<div class="description">catalog only</div><div id="body" class="subject-content">'+text+'</div>'
    reader.download=Mock(return_value=(html.encode(),'https://islamic-content.com/post/18220'))
    found=reader.article('https://islamic-content.com/post/18220')
    assert found[0].arabic_text==text.strip()
    assert 'catalog only' not in found[0].arabic_text


def test_native_p20_layout_preserves_published_section(tmp_path):
    source=OfficialSources(time.monotonic()+10,AuthenticatedStore(tmp_path));source.search_analysis=analysis()
    source.search_analysis.keywords={'ar':['النص'],'en':[]};reader=PublicationReader(source)
    text='النص الأصلي الكامل مع المرجع. '*8
    html='<div class="entry-main-content"><div class="p-20"><h2>عنوان</h2>'+text+'</div></div>'
    reader.download=Mock(return_value=(html.encode(),'https://islamic-content.com/t/11662'))
    assert reader.article('https://islamic-content.com/t/11662')[0].arabic_text=='عنوان '+text.strip()


def test_slow_web_answer_finishes_and_holds_worker_slot(monkeypatch):
    import asyncio
    from src.web import app as web
    from src.web.app import QueryRequest
    from src.core.schema import PipelineOutput
    monkeypatch.setitem(web.POLICY,'pipeline_timeout_seconds',.01)
    def slow(req):
        time.sleep(.08)
        return PipelineOutput(query=req.query,target_language=req.target_language)
    monkeypatch.setattr(web,'get_pipeline',lambda:slow)
    async def exercise():
        slots=asyncio.Semaphore(1);monkeypatch.setattr(web,'query_slots',slots)
        task=asyncio.create_task(web.query_pipeline(QueryRequest(query='A question')))
        await asyncio.sleep(.03)
        assert not task.done() and slots.locked()
        response=await task
        assert response['success'] and not slots.locked()
    asyncio.run(exercise())


def test_uthmani_superscript_alif_is_searchable_without_changing_original(tmp_path):
    c=citation(('ar',));text='إِبۡرَٰهِيمَ نَبِيًّا'
    c.arabic_text=text;c.accredited_translation=text;c.translations={'ar':text}
    c.passages={};c.source_digest=OfficialSources.digest(c)
    index=SourceIndex(AuthenticatedStore(tmp_path));index.put(c)
    a=analysis();a.keywords={'ar':['إبراهيم'],'en':[]}
    assert index.search(a,['ar'])[0].arabic_text==text


def test_translation_variants_do_not_crowd_out_distinct_sources(tmp_path):
    index=SourceIndex(AuthenticatedStore(tmp_path))
    first=citation();index.put(first)
    index.put(citation(('ar',)))
    a=analysis();a.keywords={'ar':['الله'],'en':[]}
    found=index.search(a,['ar'])
    assert len(found)==1 and found[0].reference_id=='112:1'


def test_sensitive_questions_receive_more_distinct_whole_local_originals(tmp_path):
    from src.core.schema import ResponseLevel
    index=SourceIndex(AuthenticatedStore(tmp_path))
    originals=[]
    for n in range(8):
        c=original();c.reference_id='published-'+str(n);c.source_digest=OfficialSources.digest(c)
        index.put(c);originals.append(c)
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    assert len(index.search(a,['ar']))==4
    a.level=ResponseLevel.LEVEL_C
    found=index.search(a,['ar'])
    assert len(found)==6 and len({c.reference_id for c in found})==6
    assert all(c.arabic_text==original().arabic_text for c in found)


def test_arabic_adjective_and_noun_share_navigation_without_rewriting_source(tmp_path):
    c=original();text='النص الأصلي يتناول أدوات موسيقية. '*6
    c.arabic_text=text;c.accredited_translation=text;c.translations={'ar':text}
    c.passages['ar'][0].text=text
    c.source_digest=OfficialSources.digest(c)
    index=SourceIndex(AuthenticatedStore(tmp_path));index.put(c)
    a=analysis();a.keywords={'ar':['الموسيقى'],'en':[]}
    found=index.search(a,['ar'])
    assert len(found)==1 and found[0].translations['ar']==text
    assert found[0].source_digest==c.source_digest


def test_old_navigation_index_upgrades_without_renewing_or_editing_evidence(tmp_path):
    store=AuthenticatedStore(tmp_path);index=SourceIndex(store);c=original();index.put(c)
    with store.connect() as db:
        before=db.execute("SELECT payload,signature,expires FROM records WHERE kind='indexed_source'").fetchall()
    with index.connect() as db:
        db.execute('DELETE FROM navigation_metadata')
        db.execute("UPDATE passages SET body='outdated navigation'")
    rebuilt=SourceIndex(store)
    a=analysis();a.keywords={'ar':['موضوع الاختبار'],'en':[]}
    assert rebuilt.search(a,['ar'])[0].arabic_text==c.arabic_text
    with store.connect() as db:
        assert db.execute("SELECT payload,signature,expires FROM records WHERE kind='indexed_source'").fetchall()==before
