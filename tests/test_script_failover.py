import json

import httpx
import pytest

from autoyt import limits
from autoyt.ai import LLM, Audit
from autoyt.pipeline import Pipeline


def enabled(cfg):
    cfg.script_fallback_enabled = True
    cfg.llm_provider = 'groq'
    cfg.groq_api_key = 'test-groq-key'
    cfg.gemini_api_key = 'test-gemini-key'
    cfg.openrouter_api_key = 'test-openrouter-key'
    return cfg


def test_quota_switches_to_gemini_and_sticks_for_audit_and_revision(cfg, monkeypatch):
    enabled(cfg)
    calls, usage = [], []
    payload = {'source': 'untrusted document'}

    def call(self, schema, instruction, data):
        calls.append((self.cfg.llm_provider, schema.__name__))
        assert data is payload and instruction == 'Keep all factual checks'
        if self.cfg.llm_provider == 'groq':
            raise RuntimeError('TRANSIENT: Groq tokens per day limit reached. Retry in 600 seconds.')
        return schema(supported=True, reasons=[])

    monkeypatch.setattr(LLM, '_structured_single', call)
    llm = LLM(cfg)
    llm.usage_sink = usage.append
    assert llm.structured(Audit, 'Keep all factual checks', payload).supported
    assert llm.structured(Audit, 'Keep all factual checks', payload).supported
    assert [p for p, _ in calls] == ['groq', 'gemini', 'gemini']
    assert llm.cfg.llm_provider == cfg.llm_provider == 'groq'
    assert usage[-1]['active_provider'] == 'gemini'
    assert 'test-groq-key' not in json.dumps(usage)


def test_all_cloud_limits_use_installed_ollama_and_release_it(cfg, monkeypatch):
    enabled(cfg)
    calls, unloaded = [], []
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200,
        request=httpx.Request('GET', 'http://localhost/api/tags'), json={'models': [{'name': cfg.ollama_model}]}))

    def call(self, *args):
        calls.append(self.cfg.llm_provider)
        if self.cfg.llm_provider != 'ollama':
            raise RuntimeError('TRANSIENT: selected provider quota reached')
        self._used = True
        return Audit(supported=True, reasons=[])

    monkeypatch.setattr(LLM, '_structured_single', call)
    monkeypatch.setattr(httpx, 'post', lambda url, **k: unloaded.append((url, k['json']['model'])) or
        httpx.Response(200, request=httpx.Request('POST', url)))
    llm = LLM(cfg)
    assert llm.structured(Audit, 'test', {}).supported
    assert calls == ['groq', 'gemini', 'openrouter', 'ollama']
    llm.release()
    assert unloaded == [(cfg.ollama_base_url + '/api/generate', cfg.ollama_model)]
    assert not llm._clients['ollama']._used


def test_missing_keys_and_offline_local_model_are_skipped_without_requests(cfg, monkeypatch):
    enabled(cfg)
    cfg.groq_api_key = cfg.gemini_api_key = cfg.openrouter_api_key = ''
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError('offline')))
    monkeypatch.setattr(LLM, '_structured_single', lambda *a: pytest.fail('No provider is configured'))
    with pytest.raises(RuntimeError, match='All configured script providers are unavailable'):
        LLM(cfg).structured(Audit, 'test', {})


def test_offline_preferred_ollama_does_not_block_cloud_success_at_voice_cleanup(cfg, monkeypatch):
    enabled(cfg)
    cfg.llm_provider = 'ollama'
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url.startswith(cfg.ollama_base_url):
            raise httpx.ConnectError('offline')
        return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{
            'finish_reason': 'stop', 'message': {'content': '{"supported":true,"reasons":[]}'}}]})

    monkeypatch.setattr(httpx, 'post', post)
    llm = LLM(cfg)
    assert llm.structured(Audit, 'test', {}).supported
    llm.release()
    assert len(calls) == 2
    assert llm._active_provider == 'groq'


@pytest.mark.parametrize('error', [ValueError('unsupported facts'),
    RuntimeError('TRANSIENT: Research time limit reached'), RuntimeError('CONFIGURATION: prompt exceeds context')])
def test_validation_and_global_deadlines_never_trigger_failover(cfg, monkeypatch, error):
    enabled(cfg)
    calls = []

    def fail(self, *args):
        calls.append(self.cfg.llm_provider)
        raise error

    monkeypatch.setattr(LLM, '_structured_single', fail)
    with pytest.raises(type(error), match=str(error)):
        LLM(cfg).structured(Audit, 'test', {})
    assert calls == ['groq']


def test_negative_fact_audit_is_not_retried_with_another_model(cfg, monkeypatch):
    enabled(cfg)
    calls = []
    monkeypatch.setattr(LLM, '_structured_single', lambda self, *a:
        calls.append(self.cfg.llm_provider) or Audit(supported=False, reasons=['unsupported fact']))
    assert not LLM(cfg).structured(Audit, 'test', {}).supported
    assert calls == ['groq']


def test_http_quota_fails_over_immediately_without_waiting(cfg, monkeypatch):
    enabled(cfg)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if 'groq.com' in url:
            return httpx.Response(429, request=httpx.Request('POST', url),
                                  headers={'retry-after': '45'}, json={'error': {'message': 'tokens per minute'}})
        return httpx.Response(200, request=httpx.Request('POST', url), json={'candidates': [{
            'finishReason': 'STOP', 'content': {'parts': [{'text': '{"supported":true,"reasons":[] }'}]}}]})

    monkeypatch.setattr(httpx, 'post', post)
    monkeypatch.setattr(limits, 'pause', lambda *args: pytest.fail('Failover must not wait for quota'))
    assert LLM(cfg).structured(Audit, 'test', {}).supported
    assert len(calls) == 2


def test_openrouter_backup_stays_explicitly_free(cfg, monkeypatch):
    enabled(cfg)
    cfg.gemini_api_key = ''
    requests = []

    def post(url, **kwargs):
        if 'groq.com' in url:
            return httpx.Response(429, request=httpx.Request('POST', url), json={'error': {'message': 'tokens per day'}})
        requests.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{
            'finish_reason': 'stop', 'message': {'content': '{"supported":true,"reasons":[]}'}}]})

    monkeypatch.setattr(httpx, 'post', post)
    assert LLM(cfg).structured(Audit, 'test', {}).supported
    assert requests[0]['model'].endswith(':free')
    assert requests[0]['provider']['max_price'] == {'prompt': 0, 'completion': 0}
    assert requests[0]['provider']['allow_fallbacks'] is False


@pytest.mark.parametrize('slot', [None, '2030-01-01T09:30:00+05:30'])
def test_manual_and_scheduled_jobs_persist_actual_provider_usage(cfg, monkeypatch, slot):
    enabled(cfg)
    cfg.enable_upload = False
    runner = Pipeline(cfg)
    job = runner.store.create(dry_run=True, slot=slot)
    runner.store.update(job, 'FAILED', stage=2)

    def call(self, *args):
        if self.cfg.llm_provider == 'groq':
            raise RuntimeError('TRANSIENT: Groq quota reached')
        return Audit(supported=True, reasons=[])

    def generate(*args, **kwargs):
        runner.llm.structured(Audit, 'Check facts', {})
        raise ValueError('Stop after actual-model usage is saved')

    monkeypatch.setattr(LLM, '_structured_single', call)
    monkeypatch.setattr(runner, '_generate_script', generate)
    runner.execute(job)
    usage = runner.store.load(job, 'script_provider_usage')
    assert usage['active_provider'] == 'gemini'
    assert usage['calls'][-1]['status'] == 'success'
    assert usage['preferred_provider'] == 'groq'
    assert runner.cfg.tts_engine == cfg.tts_engine
