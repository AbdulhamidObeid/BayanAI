"""Original publication fetching and recovery regressions, not answer shortcuts."""
import json
import time
from unittest.mock import Mock
import pytest
from src.core.official_sources import OfficialSources
from src.core.publication_reader import PublicationReader
from src.core.analysis_agent import AnalysisAgent, SafetyStop
from src.core.schema import SourceRepository, PipelineInput, EvidenceSelection
from src.core.orchestrator import MasterOrchestrator
from tests.test_trust_pipeline import citation, analysis


@pytest.fixture
def reader(tmp_path):
    from src.core.integrity import AuthenticatedStore
    source=OfficialSources(time.monotonic()+60,store=AuthenticatedStore(tmp_path/'private'))
    source.search_analysis=analysis()
    source.search_analysis.keywords={'ar':['النص'],'en':['published']}
    return PublicationReader(source)


def test_search_reads_result_titles_not_sidebar_or_navigation(reader,monkeypatch):
    response=Mock(url='https://islamic-content.com/search',content=b'<nav><a href="/t/9">nav</a></nav><div class="loop-list"><h4><a href="/t/10">result</a></h4></div>')
    monkeypatch.setattr('src.core.publication_reader.requests.get',Mock(return_value=response))
    assert reader.search_articles('topic')==[('article','https://islamic-content.com/t/10')]


def test_topic_only_page_is_not_evidence(reader):
    reader.download=Mock(return_value=(b'<main><h1>topic</h1><a href="/t/10">related</a></main>','https://islamic-content.com/t/1'))
    assert reader.article('https://islamic-content.com/t/1')==[]


def test_article_preserves_entire_published_section_and_locator(reader):
    text='النص المنشور يظل محفوظا دون تعديل أو صياغة جديدة. '*6
    html='<main><div class="p-30"><h4 data-card="1">عنوان</h4><p>'+text+'</p></div></main>'
    reader.download=Mock(return_value=(html.encode(),'https://islamic-content.com/t/1'))
    results=reader.article('https://islamic-content.com/t/1')
    assert len(results)==1
    assert results[0].arabic_text=='عنوان '+text.strip()
    assert results[0].reference_id.endswith('|section=1')
    assert json.loads(results[0].published_attribution['ar'])['document_sha256']


def test_hadith_collection_cannot_bypass_grade_adapter(reader):
    reader.download=Mock(return_value=(b'<main><div class="hadeeth">ungraded narration</div></main>','https://islamic-content.com/t/1'))
    assert reader.article('https://islamic-content.com/t/1')==[]


def test_library_description_is_never_a_book_excerpt(reader):
    reader.sources.call=Mock(return_value={'structuredContent':{'id':'library:1:ar','url':'https://islamcontent.com/ar/content/1','text':'a book description','metadata':{'language':'ar'}}})
    reader.download=Mock(return_value=(b'<main>description only</main>','https://islamcontent.com/ar/content/1'))
    assert reader.library('library:1:ar')==[]
    assert 'library_original_text_unavailable' in reader.sources.unavailable


def test_library_opens_observed_pdf_not_catalog_prose(reader):
    reader.sources.call=Mock(return_value={'structuredContent':{'id':'library:1:ar','url':'https://islamcontent.com/ar/content/1','metadata':{'language':'ar'}}})
    reader.download=Mock(return_value=(b'<a href="/storage/book.pdf">download</a><a href="https://unapproved.example/book.pdf">external</a>','https://islamcontent.com/ar/content/1'))
    reader.pdf=Mock(return_value=['exact pages'])
    assert reader.library('library:1:ar')==['exact pages']
    assert reader.pdf.call_args.args[0]=='https://islamcontent.com/storage/book.pdf'


def test_wrong_library_id_rejected(reader):
    reader.sources.call=Mock(return_value={'structuredContent':{'id':'library:2:ar'}})
    with pytest.raises(SafetyStop,match='wrong_library_document'):
        reader.library('library:1:ar')


@pytest.mark.parametrize('bad',['\ue123','\ufffd','األولياء','االستغفار','إىل','يف','اهلل'])
def test_corrupted_arabic_pdf_is_not_repaired_or_published(reader,bad):
    assert not reader.readable(('النص العربي الواضح دون أخطاء. '*6)+bad,'ar')


def test_detached_diacritics_from_broken_font_map_are_not_original_evidence(reader):
    assert not reader.readable(('نص أصلي طويل. '*20)+' ٚ ٌ ِ ','ar')
    assert reader.readable('النَّصُّ العَرَبِيُّ المَنْشُورُ محفوظ بكامل معناه. '*6,'ar')


def test_native_original_can_support_localization_without_fake_translation(reader):
    text='النص العربي الأصلي. '*10
    c=reader.citation(text,'https://islamic-content.com/t/1','ar',SourceRepository.AL_JAMHARAH,'article',1,{})
    c.provenance_verified=True;c.link_checked_at='checked';c.source_digest=OfficialSources.digest(c)
    MasterOrchestrator().validate_candidates([c],['en','ar'])
    assert c.text_for('en')==text
    assert c.evidence_language('en')=='ar'
    assert set(c.translations)=={'ar'}
    assert c.url_for('en')==c.source_url


def test_missing_scripture_translation_still_rejected():
    c=citation();c.translations.pop('en');c.source_language='ar';c.accredited_translation=c.arabic_text;c.source_url=c.translation_urls['ar'];c.source_digest=OfficialSources.digest(c)
    with pytest.raises(SafetyStop,match='unverified_source_contract'):
        MasterOrchestrator().validate_candidates([c],['en','ar'])
    with pytest.raises(KeyError):c.text_for('en')


def test_pdf_reader_uses_whole_actual_pages_and_hash(reader):
    import fitz
    doc=fitz.open();page=doc.new_page();page.insert_text((40,60),'Published original content. '*8,fontsize=8)
    body=doc.tobytes();doc.close()
    reader.download=Mock(return_value=(body,'https://islamcontent.com/storage/book.pdf'))
    result=reader.pdf('https://islamcontent.com/storage/book.pdf','en',SourceRepository.ISLAMIC_LIBRARY,'Original book')
    assert len(result)==1
    assert result[0].reference_id.endswith('|page=1')
    assert result[0].translations['en'].startswith('Published original content.')
    assert result[0].arabic_text==''


def test_parallel_pdf_extraction_keeps_originals_and_native_objects_in_children(reader):
    import fitz
    from concurrent.futures import ThreadPoolExecutor
    doc=fitz.open();page=doc.new_page();page.insert_text((40,60),'Exact publisher page content. '*8,fontsize=8)
    body=doc.tobytes();doc.close()
    reader.download=Mock(side_effect=lambda url,limit:(body,url))
    reader.sources.ingest_all=True
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:reader.pdf('https://islamcontent.com/storage/book'+str(n)+'.pdf','en',SourceRepository.ISLAMIC_LIBRARY,'Original'),range(2)))
    assert all(len(result)==1 and result[0].translations['en'].startswith('Exact publisher page content.') for result in results)


def test_native_pdf_child_failure_is_an_explicit_source_hold(reader,monkeypatch):
    import subprocess
    reader.download=Mock(return_value=(b'%PDF- broken','https://islamcontent.com/storage/broken.pdf'))
    monkeypatch.setattr('src.core.publication_reader.subprocess.run',Mock(return_value=subprocess.CompletedProcess([], -11, b'')))
    with pytest.raises(SafetyStop,match='publication_extraction_failed'):
        reader.pdf('https://islamcontent.com/storage/broken.pdf','en',SourceRepository.ISLAMIC_LIBRARY,'Original')


def test_incomplete_candidate_assessment_gets_repaired_before_stopping():
    from tests.test_multipart_coverage import selection, multipart
    agent=AnalysisAgent(time.monotonic()+60)
    incomplete=selection();incomplete.decisions=incomplete.decisions[:1]
    complete=selection()
    agent.request=Mock(side_effect=[incomplete,complete])
    candidates=[citation(),citation()]
    candidates[1].reference_id='112:2'
    selected=agent.select_part(PipelineInput(query='Whole question'),multipart(),candidates)
    assert selected==candidates
    assert agent.request.call_count==2
    assert agent.request.call_args.args[1]['required_candidate_ids']==[0,1]


def test_duplicate_failed_search_plan_is_rejected():
    from src.core.schema import SearchExpansion,PartSearch
    from tests.test_multipart_coverage import multipart
    a=multipart();agent=AnalysisAgent(time.monotonic()+60)
    same=SearchExpansion(keywords=a.keywords,part_searches=[PartSearch(part_id=i,keywords=p.keywords) for i,p in enumerate(a.question_parts)])
    agent.request=Mock(return_value=same)
    with pytest.raises(SafetyStop,match='repeated_search_plan'):
        agent.expand_search(PipelineInput(query='Whole question'),a,[])
    assert agent.request.call_count==2


def test_fuzzy_search_results_are_ranked_before_download_limit(reader,monkeypatch):
    reader.policy['articles_per_keyword']=1
    response=Mock(url='https://islamic-content.com/search',content=b'<div class="loop-list"><h4><a href="/t/1">unrelated</a></h4><h4><a href="/t/2">published topic</a></h4></div>')
    monkeypatch.setattr('src.core.publication_reader.requests.get',Mock(return_value=response))
    assert reader.search_articles('published topic')==[('article','https://islamic-content.com/t/2')]


def test_topic_follows_observed_article_card_only_once(reader):
    text='النص المنشور دون تعديل. '*12
    topic=b'<main><h4 class="post-title"><a href="/t/2">article</a></h4></main>'
    article=('<main><div class="author-description">'+text+'</div></main>').encode()
    reader.download=Mock(side_effect=[(topic,'https://islamic-content.com/t/1'),(article,'https://islamic-content.com/t/2')])
    found=reader.article('https://islamic-content.com/t/1')
    assert len(found)==1
    assert found[0].source_url=='https://islamic-content.com/t/2'
    assert found[0].arabic_text==text.strip()


def test_shortlist_preserves_later_question_part_and_complete_passages(reader):
    from tests.test_multipart_coverage import multipart
    a=multipart();a.question_parts[0].keywords={'ar':['first'],'en':['first']};a.question_parts[1].keywords={'ar':['second'],'en':['second']}
    reader.sources.policy['assessment_candidate_limit']=2
    candidates=[reader.citation(('first published original. '*8),'https://islamic-content.com/t/'+str(i),'en',SourceRepository.AL_JAMHARAH,'article',1,{}) for i in range(4)]
    later=reader.citation(('second published original. '*8),'https://islamic-content.com/t/9','en',SourceRepository.AL_JAMHARAH,'article',1,{})
    found=reader.sources.shortlist(a,candidates+[later])
    assert later in found
    assert len(found)==2
    assert all(c.translations['en'].endswith('original. ') for c in found)


def test_invalid_model_contract_gives_fallback_repair_details(monkeypatch):
    from google import genai
    from src.core.schema import QuestionPart
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    first=Mock();first.models.generate_content.return_value=Mock(text='{}')
    expected=analysis();expected.question_parts=[QuestionPart(question='question',keywords=expected.keywords,requirements=[{'kind':'explanation','description':'Answer the requested question.'}])]
    second=Mock();second.models.generate_content.return_value=Mock(text=expected.model_dump_json())
    monkeypatch.setattr(genai,'Client',Mock(side_effect=[first,second]))
    result=AnalysisAgent(time.monotonic()+60).request('classify',{'question':'x'},type(expected))
    assert result==expected
    repaired=json.loads(second.models.generate_content.call_args.kwargs['contents'])
    assert repaired['contract_repair']
    assert all('input' not in e for e in repaired['contract_repair'])


def test_invalid_model_contract_is_not_labeled_an_api_outage(monkeypatch):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY','test-only-key')
    client=Mock();client.models.generate_content.return_value=Mock(text='{}')
    monkeypatch.setattr(genai,'Client',Mock(return_value=client))
    with pytest.raises(SafetyStop,match='invalid_model_response'):
        AnalysisAgent(time.monotonic()+60).request('classify',{'question':'x'},type(analysis()))


def test_library_has_its_own_search_budget(reader,monkeypatch):
    from src.core.schema import QuestionPart
    a=analysis();a.question_parts=[QuestionPart(question='topic',keywords={'ar':['topic'],'en':['topic']})]
    def results(tool,args):
        if args['sources']==['library']:
            return {'structuredContent':{'results':[{'id':'library:42:ar','url':'https://islamcontent.com/ar/content/42'}]}}
        return {'structuredContent':{'results':[]}}
    reader.sources.call=Mock(side_effect=results)
    monkeypatch.setattr(PublicationReader,'search_articles',Mock(return_value=[]))
    monkeypatch.setattr(PublicationReader,'search_shamela',Mock(return_value=[]))
    monkeypatch.setattr(PublicationReader,'search_dawa',Mock(return_value=[]))
    monkeypatch.setattr(PublicationReader,'search_quranpedia',Mock(return_value=[]))
    found=reader.sources.discover(a)
    assert ('library','library:42:ar') in found
    searched={tuple(c.args[1]['sources']) for c in reader.sources.call.call_args_list}
    assert searched=={('library',),('hadith',),('quran',)}


def test_shamela_search_uses_observed_page_links_not_snippet_text(reader,monkeypatch):
    response=Mock(content=b'<a href="https://shamela.ws/book/9621/253">book page</a><p>snippet</p><a href="https://evil.example/book/1/2">external</a>')
    post=Mock(return_value=response);monkeypatch.setattr('src.core.publication_reader.requests.post',post)
    assert reader.search_shamela('كسوة الكعبة')==[('shamela','https://shamela.ws/book/9621/253')]
    assert post.call_args.kwargs['data']['term']=='+كسوة +الكعبة'


def test_shamela_preserves_whole_original_body_and_footnotes_with_page_locator(reader):
    text='هذا النص الأصلي من الكتاب بلا أي تغيير. '*5
    footnote='هامش الناشر الأصلي محفوظ بالكامل.'
    html='<title>اسم الكتاب</title><div class="nass" data-page-id="253" data-page-num="258"><p>'+text+'</p><a class="btn_tag">copy UI</a><p class="hamesh">'+footnote+'</p></div><footer>not evidence</footer>'
    reader.download=Mock(return_value=(html.encode(),'https://shamela.ws/book/9621/253'))
    found=reader.shamela('https://shamela.ws/book/9621/253')[0]
    assert found.repository==SourceRepository.SHAMELA
    assert found.arabic_text==text.strip()+' '+footnote
    assert found.reference_id.endswith('|page=258')
    assert 'copy UI' not in found.arabic_text and 'not evidence' not in found.arabic_text


def test_shamela_wrong_page_and_missing_original_rejected(reader):
    reader.download=Mock(return_value=(b'<div class="nass" data-page-id="99">catalog</div>','https://shamela.ws/book/9621/253'))
    with pytest.raises(SafetyStop):reader.shamela('https://shamela.ws/book/9621/253')


def test_all_collections_start_before_extra_synonyms(reader,monkeypatch):
    from concurrent.futures import Future
    import src.core.official_sources as module
    submitted=[]
    class Pool:
        def __init__(self,**kw):pass
        def submit(self,fn,*args):
            name='mcp:'+args[1]['sources'][0] if fn==reader.sources.call else fn.__name__
            submitted.append(name);f=Future()
            f.set_result({'structuredContent':{'results':[]}} if name.startswith('mcp:') else [])
            return f
        def shutdown(self,**kw):pass
    monkeypatch.setattr(module,'ThreadPoolExecutor',Pool)
    monkeypatch.setattr(PublicationReader,'search_articles',lambda self,k:[])
    monkeypatch.setattr(PublicationReader,'search_shamela',lambda self,k:[])
    monkeypatch.setattr(PublicationReader,'search_dawa',lambda self,k:[])
    monkeypatch.setattr(PublicationReader,'search_quranpedia',lambda self,k:[])
    # Lambdas have the same name, distinguish by count for this submission test.
    a=analysis();a.keywords={'ar':['one','two'],'en':['one','two']}
    reader.sources.discover(a)
    assert submitted[:7]==['mcp:library','mcp:hadith','mcp:quran','<lambda>','<lambda>','<lambda>','<lambda>']


def test_quranpedia_discovery_uses_publisher_verse_number_not_global_id(reader,monkeypatch):
    html='<a class="result-item" href="https://quranpedia.net/surah/1/5?ayah_id=766"><p>search snippet</p><div><span>المائدة : ٩٧</span></div></a>'
    response=Mock();response.json.return_value={'html':html}
    monkeypatch.setattr('src.core.publication_reader.requests.get',Mock(return_value=response))
    assert reader.search_quranpedia('الكعبة')==[('quran','5:97')]


def test_quranpedia_without_published_verse_label_is_not_evidence(reader,monkeypatch):
    response=Mock();response.json.return_value={'html':'<a class="result-item" href="https://quranpedia.net/surah/1/5?ayah_id=766">snippet only</a>'}
    monkeypatch.setattr('src.core.publication_reader.requests.get',Mock(return_value=response))
    assert reader.search_quranpedia('الكعبة')==[]


def test_dawa_discovery_follows_only_observed_approved_file_links(reader,monkeypatch):
    response=Mock(content=b'<a href="/file/3081">book</a><a href="https://evil.example/file/7">external</a>')
    get=Mock(return_value=response);monkeypatch.setattr('src.core.publication_reader.requests.get',get)
    assert reader.search_dawa('كسوة الكعبة')==[('dawa','https://dawa.center/file/3081')]
    assert get.call_args.kwargs['params']=={'query':'كسوة الكعبة'}


def test_dawa_description_is_navigation_only_original_pdf_required(reader):
    reader.download=Mock(return_value=(b'<title>book</title><p>Description only</p>','https://dawa.center/file/3081'))
    assert reader.dawa('https://dawa.center/file/3081')==[]


def test_dawa_downloads_exact_observed_pdf_not_generated_description(reader):
    reader.download=Mock(return_value=(b'<title>book</title><a href="/storage/files/observed.pdf">PDF</a>','https://dawa.center/file/3081'))
    reader.pdf=Mock(return_value=[])
    reader.dawa('https://dawa.center/file/3081')
    assert reader.pdf.call_args.args==('https://dawa.center/storage/files/observed.pdf','ar',SourceRepository.DAWA_CENTER,'book')
