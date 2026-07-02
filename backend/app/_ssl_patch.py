"""SSL compatibility shim.

Some Windows installs ship a certificate store that ``ssl.create_default_context``
cannot parse (``ASN1: NOT_ENOUGH_DATA``). Libraries that import ``aiohttp``
(e.g. ``google-genai``) call ``ssl.create_default_context()`` at import time and
crash before application code can react. This shim makes the default context use
the ``certifi`` bundle instead of the Windows store, which is the same trust
material already configured via SSL_CERT_FILE in .env.

It must be imported before any module that imports aiohttp / google.genai /
sentence_transformers, so it is imported from app/__init__.py.
"""
from __future__ import annotations

import os
import ssl


def apply() -> None:
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi is a core dependency
        return

    cafile = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)
    os.environ.setdefault("CURL_CA_BUNDLE", cafile)

    original = ssl.create_default_context

    def patched_create_default_context(purpose=ssl.Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None):
        if cafile is None and capath is None and cadata is None:
            cafile = certifi_path
        return original(purpose=purpose, cafile=cafile, capath=capath, cadata=cadata)

    certifi_path = cafile
    patched_create_default_context.__name__ = original.__name__
    patched_create_default_context.__qualname__ = original.__qualname__
    ssl.create_default_context = patched_create_default_context  # type: ignore[assignment]

    try:
        ssl.create_default_https_context = patched_create_default_context  # type: ignore[assignment]
    except AttributeError:  # pragma: no cover
        pass


apply()
