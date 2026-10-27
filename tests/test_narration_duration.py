from types import SimpleNamespace

import pytest
from test_youtube_content import draft, item, supported_brief

from autoyt import ai, narration_duration, pipeline, youtube_content
from autoyt.pipeline import Pipeline


def resized(script, count):
    value = script.model_copy(deep=True)
    words = script.script.split()
    words = (words * (count // len(words) + 1))[:count]
    passages = []
    for index, scene in enumerate(value.scenes):
        scene.narration = " ".join(words[index * count // 6:(index + 1) * count // 6]).rstrip('.,!?') + "."
        passages.append(scene.narration)
    value.script = " ".join(passages)
    return value


@pytest.mark.parametrize('seconds,direction', [(25.87, 'EXPAND'), (70, 'SHORTEN')])
def test_measured_rate_sets_direction_and_safe_word_budget(cfg, script, seconds, direction):
    cfg.duration_min, cfg.duration_max = 35, 55
    original = resized(script, 69 if direction == 'EXPAND' else 170)
    result = narration_duration.feedback(cfg, original, seconds)
    assert result['direction'] == direction
    assert result['target_words'] == round(len(original.script.split()) / seconds * 45)
    assert result['previous_draft']['narration'] == [s.narration for s in original.scenes]
    assert direction in narration_duration.instruction(result)
    with pytest.raises(ValueError, match=direction):
        narration_duration.validate(original, result)
    narration_duration.validate(resized(script, result['target_words']), result)


@pytest.mark.parametrize('durations', [(25.9, 30, 44.9), (70, 58, 45), (35,), (55,)])
def test_pipeline_measures_revisions_until_fit_without_changing_voice(cfg, script, monkeypatch, durations):
    cfg.duration_min, cfg.duration_max, cfg.video_duration_seconds = 35, 55, 45
    runner = Pipeline(cfg)
    job = runner.store.create(dry_run=True)
    runner.store.save(job, 'script', script.model_dump(mode='json'))
    runner.store.update(job, 'FAILED', stage=3)
    measured, corrections = [], []

    def synthesize(self, current, folder):
        assert self.cfg.tts_voice == cfg.tts_voice
        seconds = durations[len(measured)]
        measured.append(seconds)
        return [{'text': current.script, 'start': 0, 'end': seconds}]

    def rewrite(job, feedback):
        corrections.append(feedback)
        return resized(script, feedback['target_words'])

    def stop(*args):
        raise ValueError('Test stopped after successful voice fit')

    monkeypatch.setattr(pipeline.audio.Speech, 'synthesize', synthesize)
    monkeypatch.setattr(runner, '_generate_script', rewrite)
    monkeypatch.setattr(pipeline.media, 'choose_media', stop)
    runner.execute(job)
    assert runner.store.get(job)['stage'] == 4
    assert measured == list(durations)
    assert len(corrections) == len(durations) - 1
    assert runner.store.load(job, 'timing')[-1]['end'] == durations[-1]
    attempts = runner.store.load(job, 'narration_duration_attempts')
    assert attempts[-1]['within_limits']
    assert len(attempts) == len(durations)


def test_corrections_are_bounded_and_do_not_rewrite_supplied_script(cfg, script, monkeypatch):
    cfg.duration_min, cfg.duration_max, cfg.video_duration_seconds = 35, 55, 45
    runner = Pipeline(cfg)
    job = runner.store.create(dry_run=True)
    runner.store.save(job, 'script', script.model_dump(mode='json'))
    runner.store.update(job, 'FAILED', stage=3)
    calls = []
    monkeypatch.setattr(pipeline.audio.Speech, 'synthesize', lambda *args:
        calls.append(1) or [{'text': 'test', 'start': 0, 'end': 70}])
    monkeypatch.setattr(runner, '_generate_script', lambda job, feedback: resized(script, feedback['target_words']))
    runner.execute(job)
    assert len(calls) == narration_duration.MAX_REVISIONS + 1
    assert runner.store.get(job)['stage'] == 3
    assert 'after 3 measured revisions' in runner.store.get(job)['error']
    runner.store.save(job, 'script', script.model_dump(mode='json'))
    runner.store.save(job, 'request', {'manual_input': {'mode': 'script', 'topic': 'Computer software',
                                                     'content': script.script}})
    calls.clear()
    monkeypatch.setattr(runner, '_generate_script', lambda *args: pytest.fail('User script must be preserved'))
    runner.execute(job)
    assert len(calls) == 1
    assert 'was not rewritten' in runner.store.get(job)['error']


@pytest.mark.parametrize('writer', ['youtube', 'legacy'])
def test_writer_receives_actual_draft_and_expands_short_audio_before_synthesizing(
        cfg, script, research_data, monkeypatch, writer):
    cfg.narration_language = 'en'
    cfg.duration_min, cfg.duration_max, cfg.video_duration_seconds = 35, 55, 45
    feedback = narration_duration.feedback(cfg, resized(script, 69), 25.87)
    calls = []

    def response(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[], misleading=False, defensive_only=True)
        assert 'EXPAND' in instruction
        assert data['previous_draft']
        assert data['duration_feedback'] == feedback
        calls.append(data)
        if len(calls) == 1:
            return draft(resized(script, 70))
        assert 'EXPAND' in str(data['previous_problem'])
        return draft(resized(script, feedback['target_words']))

    llm = SimpleNamespace(structured=response)
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    if writer == 'youtube':
        result = youtube_content.generate(cfg, llm, topic, supported_brief(topic, research_data), feedback)
    else:
        monkeypatch.setattr(ai, 'validate_grounding', lambda *args: None)
        result = ai.generate(cfg, llm, topic, research_data, feedback)
    assert len(calls) == 2
    narration_duration.validate(result, feedback)
