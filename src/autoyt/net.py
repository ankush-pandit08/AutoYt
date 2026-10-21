"""Bounded HTTP retrieval. Never log request headers, tokens, or upload session URLs."""
import ipaddress
import logging
import random
import socket
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import limits

USER_AGENT = "AutoYT/0.1 (personal technology research)"


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only public HTTPS source URLs are supported")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Nonpublic source address rejected")


class Web:
    def __init__(self):
        # Some stock APIs require a key in the URL; prevent transport logs from exposing it.
        for logger in ("httpx", "httpcore"):
            logging.getLogger(logger).setLevel(logging.CRITICAL)
        self.client = httpx.Client(timeout=httpx.Timeout(30, connect=8),
                                   headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        self.robots = {}

    def get(self, url, *, max_bytes=5_000_000, headers=None, binary=False):
        for redirect in range(6):
            limits.check()
            public_url(url)
            for attempt in range(3):
                try:
                    with self.client.stream("GET", url, headers=headers,
                                            timeout=httpx.Timeout(limits.timeout(30), connect=limits.timeout(8))) as response:
                        limits.check()
                        if response.status_code in (429, 500, 502, 503, 504):
                            if attempt == 2:
                                response.raise_for_status()
                            delay = min(float(response.headers.get("Retry-After", 2 ** attempt)), 20)
                            limits.pause(delay + random.random())
                            continue
                        if response.is_redirect:
                            url = str(response.url.join(response.headers["location"]))
                            break
                        response.raise_for_status()
                        buf = bytearray()
                        for chunk in response.iter_bytes():
                            limits.check()
                            buf.extend(chunk)
                            if len(buf) > max_bytes:
                                raise ValueError("Download exceeds configured size limit")
                        return bytes(buf) if binary else bytes(buf).decode("utf-8", errors="replace")
                except (httpx.ConnectError, httpx.ReadTimeout):
                    if attempt == 2:
                        raise
                    limits.pause(2 ** attempt + random.random())
        raise ValueError("Too many source redirects")

    def article(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            parser = RobotFileParser()
            try:
                parser.parse(self.get(origin + "/robots.txt", max_bytes=500_000).splitlines())
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise ValueError("Cannot establish article access permission") from None
                parser.parse([])
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(USER_AGENT, url):
            raise ValueError("Source disallows automated retrieval")
        return self.get(url)

    def download(self, url, path, max_bytes=80_000_000):
        """Stream media to disk, never buffer an entire video in Python memory."""
        for _ in range(6):
            public_url(url)
            with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                count = 0
                with path.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=256*1024):
                        count += len(chunk)
                        if count > max_bytes:
                            raise ValueError("Footage exceeds download size limit")
                        output.write(chunk)
                return
        raise ValueError("Too many media redirects")
