"""Outbound HTTPS settings shared by every client the engine creates.

Certificates are checked against the operating system's trust store (`truststore`)
instead of Python's bundled list. That is what browsers do, so the engine keeps working
behind antivirus software or a company proxy that inspects HTTPS, where Python's own
list fails with CERTIFICATE_VERIFY_FAILED. Verification is never turned off.
"""

from __future__ import annotations

import ssl
from functools import lru_cache

import anthropic
import truststore


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def anthropic_client(api_key: str | None, timeout: float) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=timeout,
        http_client=anthropic.DefaultAsyncHttpxClient(verify=tls_context()),
    )
