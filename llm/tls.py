"""TLS policy for the configured LLM gateway only."""

from __future__ import annotations

import ssl
from pathlib import Path

import truststore


GatewayVerifySetting = bool | str | ssl.SSLContext


def gateway_verify_setting(*, tls_verify: bool, cert_file: str | None = None) -> GatewayVerifySetting:
    """Return an httpx verification setting using the native OS trust store.

    Python's bundled CA file may not include certificates installed by managed
    TLS-inspection products. The native trust store does, while
    preserving full hostname and certificate verification.  An explicit CA
    bundle remains the highest-priority override.
    """

    if not tls_verify:
        return False
    if cert_file:
        bundle = Path(cert_file).expanduser().resolve()
        if not bundle.is_file():
            raise FileNotFoundError(f"configured SSL_CERT_FILE does not exist: {bundle}")
        return str(bundle)
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
