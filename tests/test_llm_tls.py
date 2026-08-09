from __future__ import annotations

import ssl

import pytest

from llm.tls import gateway_verify_setting


def test_gateway_tls_uses_native_trust_store_when_verification_is_enabled() -> None:
    verify = gateway_verify_setting(tls_verify=True)

    assert isinstance(verify, ssl.SSLContext)
    assert verify.verify_mode == ssl.CERT_REQUIRED
    assert verify.check_hostname is True


def test_gateway_tls_prefers_an_explicit_ca_bundle(tmp_path) -> None:
    bundle = tmp_path / "corporate-ca.pem"
    bundle.write_text("test fixture", encoding="utf-8")

    assert gateway_verify_setting(tls_verify=True, cert_file=str(bundle)) == str(bundle.resolve())


def test_gateway_tls_rejects_a_missing_explicit_ca_bundle(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="configured SSL_CERT_FILE does not exist"):
        gateway_verify_setting(tls_verify=True, cert_file=str(tmp_path / "missing.pem"))


def test_gateway_tls_disablement_is_explicit() -> None:
    assert gateway_verify_setting(tls_verify=False) is False
