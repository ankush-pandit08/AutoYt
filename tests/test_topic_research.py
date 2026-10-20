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
    llm = SimpleNamespace(structured=lambda *args: ai.Audit(supported=True,reasons=[]))
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
