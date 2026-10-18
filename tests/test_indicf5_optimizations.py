from types import SimpleNamespace

import pytest

from autoyt.indicf5_optimizations import cached_conditioning


def fixture():
    calls = []
    def text(text, seq_len, drop_text=False):
        calls.append((text.data_ptr(), seq_len, drop_text))
        return object()
    def rotary(seq_len):
        calls.append(("rotary", seq_len))
        return object()
    transformer = SimpleNamespace(text_embed=SimpleNamespace(forward=text),
                                  rotary_embed=SimpleNamespace(forward_from_seq_len=rotary))
    return SimpleNamespace(model=SimpleNamespace(transformer=transformer)), calls, text, rotary


def test_conditioning_reuses_exact_outputs_and_separates_guidance_branches():
    model, calls, original_text, original_rotary = fixture()
    transformer = model.model.transformer
    tokens = SimpleNamespace(data_ptr=lambda: 123)
    with cached_conditioning(model):
        first = transformer.text_embed.forward(tokens, 900)
        assert transformer.text_embed.forward(tokens, 900) is first
        assert transformer.text_embed.forward(tokens, 900, drop_text=True) is not first
        assert transformer.text_embed.forward(tokens, 901) is not first
        positions = transformer.rotary_embed.forward_from_seq_len(900)
        assert transformer.rotary_embed.forward_from_seq_len(900) is positions
    assert len(calls) == 4
    assert transformer.text_embed.forward is original_text
    assert transformer.rotary_embed.forward_from_seq_len is original_rotary


def test_conditioning_cache_clears_between_scenes_and_restores_on_error():
    model, calls, original_text, original_rotary = fixture()
    transformer = model.model.transformer
    tokens = SimpleNamespace(data_ptr=lambda: 123)
    with cached_conditioning(model):
        first = transformer.text_embed.forward(tokens, 900)
    with pytest.raises(ValueError):
        with cached_conditioning(model):
            assert transformer.text_embed.forward(tokens, 900) is not first
            raise ValueError("failed inference")
    assert len(calls) == 2
    assert transformer.text_embed.forward is original_text
    assert transformer.rotary_embed.forward_from_seq_len is original_rotary
