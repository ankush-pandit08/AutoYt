"""Worker-local research budgets shared by retrieval, model calls and retries."""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_budget = ContextVar("research_budget", default=None)
_request_limit = ContextVar("research_request_limit", default=None)


def active():
    return _budget.get() is not None


def check():
    budget = _budget.get()
    if budget and time.monotonic() >= budget["deadline"]:
        raise RuntimeError("TRANSIENT: Research time limit reached; retry later or provide official source links")


def timeout(default):
    check()
    values = [default]
    if _request_limit.get() is not None:
        values.append(_request_limit.get())
    if (budget := _budget.get()) is not None:
        values.append(max(.001, budget["deadline"] - time.monotonic()))
    return min(values)


def pause(seconds):
    check()
    time.sleep(min(seconds, timeout(seconds)))
    check()


def progress(message):
    check()
    if (budget := _budget.get()) and budget["notify"]:
        budget["notify"](message)


@contextmanager
def research_budget(seconds, notify=None):
    # Nested topic searches must share the stage budget, never restart it.
    if active():
        check()
        yield
        check()
        return
    token = _budget.set({"deadline": time.monotonic() + seconds, "notify": notify})
    try:
        yield
        check()
    finally:
        _budget.reset(token)


@contextmanager
def request_limit(seconds):
    token = _request_limit.set(seconds)
    try:
        yield
    finally:
        _request_limit.reset(token)


def research_duration(cfg):
    return (cfg.ollama_research_timeout_seconds if cfg.llm_provider == "ollama"
            else cfg.research_timeout_seconds)


def bounded_research(function):
    @wraps(function)
    def run(cfg, *args, **kwargs):
        with research_budget(research_duration(cfg)):
            return function(cfg, *args, **kwargs)
    return run
