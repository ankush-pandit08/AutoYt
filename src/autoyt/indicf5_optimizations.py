"""Reuse deterministic conditioning within one inference, without altering model arithmetic."""
from contextlib import contextmanager


@contextmanager
def cached_conditioning(model):
    """ODE steps share the same text and positions; clear caches between utterances."""
    text_layer = model.model.transformer.text_embed
    rotary = model.model.transformer.rotary_embed
    original_text = text_layer.forward
    original_rotary = rotary.forward_from_seq_len
    text_cache, rotary_cache = {}, {}

    def text_forward(text, seq_len, drop_text=False):
        key = (text.data_ptr(), seq_len, drop_text)
        if key not in text_cache:
            text_cache[key] = original_text(text, seq_len, drop_text=drop_text)
        return text_cache[key]

    def rotary_forward(seq_len):
        if seq_len not in rotary_cache:
            rotary_cache[seq_len] = original_rotary(seq_len)
        return rotary_cache[seq_len]

    text_layer.forward = text_forward
    rotary.forward_from_seq_len = rotary_forward
    try:
        yield
    finally:
        text_layer.forward = original_text
        rotary.forward_from_seq_len = original_rotary
