from types import SimpleNamespace

import pytest
from test_youtube_content import item

from autoyt import ai, topic_research, youtube_content
from autoyt.dashboard import Dashboard
from autoyt.pipeline import Pipeline


def test_search_links_are_not_evidence_and_creator_links_are_excluded(cfg, monkeypatch):
    cfg.rss_feeds = []
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    topic.youtube_metadata['description'] = 'https://youtube.com/watch?v=abc123DEF45'
    rss = '<rss><channel><item><title>Release</title><link>https://openai.com/index/release/</link></item></channel></rss>'
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        return rss
    llm = SimpleNamespace(structured=lambda *args: topic_research.SearchQuery(query='AI model release'))
    query, links = topic_research.source_urls(cfg, SimpleNamespace(get=get), llm, topic)
    assert query == 'AI model release'
    assert links == ['https://openai.com/index/release/']
    assert len(urls) == 3
    assert topic.source == 'youtube'


@pytest.mark.parametrize('url', ['http://openai.com/news','file:///tmp/key','https://user:pass@example.com/news',
                               'https://youtube.com/watch?v=abc123DEF45','https://www.bing.com/search?q=ai'])
def test_non_article_links_are_rejected(url):
    assert topic_research.article_url(url) is None


def test_real_article_text_and_exact_quotes_are_required(cfg, research_data, monkeypatch, tmp_path):
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    sources = research_data.sources
    monkeypatch.setattr(topic_research, 'source_urls', lambda *args: ('processor computer', [s.url for s in sources]))
    monkeypatch.setattr(topic_research.research, 'extract', lambda url,*args,**kwargs: next(s for s in sources if s.url == url))
    monkeypatch.setattr(topic_research.research, 'official_links', lambda *args: [])
    monkeypatch.setattr(topic_research, 'select_article_claims', lambda *args,**kwargs: research_data.claims)
    llm = SimpleNamespace(structured=lambda schema,*args: topic_research.WebSourceReview(credible=True,reason='Technical source') if schema is topic_research.WebSourceReview else ai.Audit(supported=True,reasons=[]))
    result = topic_research.gather(cfg, SimpleNamespace(article=lambda url:'article'), llm, topic, tmp_path)
    assert result.claims == research_data.claims
    assert (tmp_path/'source_documents.json').is_file()
    # A hallucinated quotation must fail even when the language-model audit approves.
    corrupted = research_data.model_copy(deep=True)
    corrupted.claims[0].evidence[0].quote = 'This sentence was invented and never appears in the retrieved document.'
    monkeypatch.setattr(topic_research, 'select_article_claims', lambda *args,**kwargs: corrupted.claims)
    with pytest.raises(ValueError,match='quotation absent'):
        topic_research.gather(cfg, SimpleNamespace(article=lambda url:'article'), llm, topic)


def test_metadata_alone_cannot_generate_a_script(cfg):
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    with pytest.raises(ValueError,match='Supporting article research is missing'):
        youtube_content.generate(cfg, None, topic, youtube_content.brief(topic))


def test_run_now_preserves_interrupted_history_but_creates_new_job(cfg, monkeypatch):
    runner = Pipeline(cfg)
    interrupted = runner.store.create()
    monkeypatch.setattr(runner, 'execute', lambda job: job)
    new = runner.create_run(fresh=True)
    assert new != interrupted
    assert runner.store.get(interrupted)['state'] == 'FAILED'
    assert runner.store.get(new)['state'] == 'CREATED'


def test_fresh_run_cannot_bypass_unresolved_upload(cfg, monkeypatch):
    runner = Pipeline(cfg)
    interrupted = runner.store.create()
    runner.store.update(interrupted,'UPLOADING')
    runner.store.upload_progress(interrupted,'UPLOADING',session_uri='https://upload.example/session')
    monkeypatch.setattr(runner,'execute',lambda job:pytest.fail('Must not start another upload'))
    with pytest.raises(ValueError,match='upload needs reconciliation'):
        runner.create_run(fresh=True)
    assert runner.store.get(interrupted)['state']=='UPLOADING'


def test_run_now_creates_new_job_without_prepared_script(cfg, monkeypatch):
    runner = Pipeline(cfg)
    old = runner.store.create()
    runner.store.save(old,'script',{'title':'Old prepared script'})
    runner.store.update(old,'PUBLISHED')
    monkeypatch.setattr(runner,'execute',lambda job:job)
    new = runner.create_run(fresh=True)
    assert new != old
    assert runner.store.load(new,'script') is None
    assert runner.store.load(new,'request')['fresh'] is True


def test_dashboard_run_now_passes_fresh_flag(cfg, monkeypatch):
    calls = []
    app = Dashboard(cfg)
    monkeypatch.setattr('autoyt.dashboard.Pipeline',lambda cfg:SimpleNamespace(create_run=lambda **kw:calls.append(kw)))
    try:
        app._run('run',None)
        assert calls == [{'dry_run':False,'fresh':True}]
    finally:
        app.close()


def test_explicit_refresh_bypasses_creator_discovery_cache(cfg, monkeypatch):
    calls = []
    class Client:
        def videos(self):return self
        def search(self):return self
        def list(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda:{'items':[]})
    monkeypatch.setattr('autoyt.youtube.service',lambda cfg:(Client(),None))
    youtube_content.discover(cfg)
    count=len(calls)
    youtube_content.discover(cfg)
    assert len(calls)==count
    youtube_content.discover(cfg,refresh=True)
    assert len(calls)>count


def test_short_provider_draft_is_kept_at_natural_length(cfg,script,research_data):
    from test_youtube_content import shorts_draft, supported_brief
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    def response(schema,instruction,data):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        value=shorts_draft(script)
        value.narration=['This technology changes how people use their devices.' for _ in range(6)]
        return value
    result=youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,supported_brief(topic,research_data))
    assert len(result.script.split())==48 and result.natural_pacing


def test_non_cybersecurity_story_is_not_rejected_by_defensive_flag(cfg,script,research_data):
    from test_youtube_content import shorts_draft, supported_brief
    topic=youtube_content.topics_from_items([item(title='Meta unveils wearable camera glasses')],cfg)[0]
    def response(schema,*args):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[],misleading=False,defensive_only=False)
        return shorts_draft(script)
    assert youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,supported_brief(topic,research_data))



def test_official_page_does_not_need_a_second_article(research_data):
    from autoyt import research
    evidence = research_data.model_copy(deep=True)
    evidence.sources = evidence.sources[:1]
    for claim in evidence.claims:
        claim.evidence = claim.evidence[:1]
        claim.verification_status = "official_source"
    assert topic_research.evidence_basis(evidence) == "official"
    topic_research.verify_evidence(evidence)
    with pytest.raises(ValueError, match="independent corroboration"):
        research.verify_claims(evidence.claims,evidence.sources)
    evidence.claims[0].evidence[0].quote = "An invented quotation that is absent from this official document."
    with pytest.raises(ValueError, match="quotation absent"):
        topic_research.verify_evidence(evidence)


def test_single_nonofficial_web_page_is_not_independent_evidence(research_data):
    source=research_data.sources[1].model_copy(update={"reviewed_web":True})
    with pytest.raises(ValueError,match="official page"):
        topic_research.validate_sources([source])


def test_non_news_web_pages_are_discovered_and_screened(cfg,monkeypatch):
    cfg.rss_feeds=[]
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    rss='<rss><channel><item><link>https://technical-research.example/docs/model</link></item></channel></rss>'
    llm=SimpleNamespace(structured=lambda *args:topic_research.SearchQuery(query="AI model documentation"))
    _,urls=topic_research.source_urls(cfg,SimpleNamespace(get=lambda *args,**kwargs:rss),llm,topic)
    assert urls==['https://technical-research.example/docs/model']


def test_official_only_script_is_labelled_and_attributed(cfg,script,research_data):
    from test_youtube_content import shorts_draft
    evidence=research_data.model_copy(deep=True)
    evidence.sources=evidence.sources[:1]
    for claim in evidence.claims:
        claim.evidence=claim.evidence[:1]
        claim.verification_status="official_source"
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    source=youtube_content.brief(topic).model_copy(update={"evidence":evidence,"fact_check_status":"official_source_only"})
    def response(schema,instruction,data):
        assert data["evidence_basis"]=="official"
        if schema is ai.ScriptAudit:
            assert "require attribution" in instruction
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        assert "explicitly attribute" in instruction
        value=shorts_draft(script)
        value.narration[0]="According to the company, this AI model sees moving objects."
        return value
    result=youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,source)
    assert result.source_basis=="official" and len(result.source_urls)==1
    assert all(scene.claim_ids for scene in result.scenes)



def test_shared_repository_host_is_not_sufficient_as_only_official_source(research_data):
    source=research_data.sources[0].model_copy(update={"url":"https://github.com/random-user/rumors","publisher":"microsoft"})
    with pytest.raises(ValueError,match="official page"):
        topic_research.validate_sources([source])



def test_official_research_prompt_is_bounded(research_data):
    source=research_data.sources[0].model_copy(update={"text":" ".join(
        f"Processors execute computing instructions using documented architecture details in section {i}."
        for i in range(150))})
    def check(schema,instruction,data):
        assert 1 <= len(data["passages"]) <= 16
        assert sum(len(p["text"]) for p in data["passages"]) <= 6500
        raise RuntimeError("Bounded prompt checked")
    with pytest.raises(RuntimeError,match="Bounded prompt checked"):
        topic_research.select_official_claims(SimpleNamespace(structured=check),[source],"processor architecture")
