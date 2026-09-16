"""py_webauthn orchestration (AD-16, FR66): challenge bookkeeping, RP
id/origin wiring, and recording the backup-eligible/backup-state flags a
verified ceremony reports.

The real cryptographic verification (attestation/assertion signatures) is
py_webauthn's own job and is proven end-to-end against a real browser's CDP
virtual authenticator in bridge_spike/check.py -- these tests cover the
orchestration this module adds around it: which challenge is pending, what
happens to an unknown/expired one, and how a verified result is stored.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
import webauthn
from bridge_spike import webauthn_flow as webauthn_flow_module
from webauthn.helpers.structs import AttestationFormat, CredentialDeviceType, PublicKeyCredentialType

INSTALL_NAME = "webauthn-test.local"
ORIGIN = f"https://{INSTALL_NAME}:8443"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _credential_json(challenge: bytes, credential_id: bytes = b"cred-id-bytes") -> dict:
    client_data = json.dumps({"type": "webauthn.create", "challenge": _b64url(challenge), "origin": ORIGIN}).encode()
    return {
        "id": _b64url(credential_id),
        "rawId": _b64url(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64url(client_data),
            "attestationObject": _b64url(b"unused-in-these-tests"),
        },
    }


class _FakeVerifiedRegistration:
    def __init__(self, credential_id: bytes, backup_eligible: bool, backup_state: bool) -> None:
        self.credential_id = credential_id
        self.credential_public_key = b"fake-public-key-bytes"
        self.sign_count = 0
        self.credential_device_type = (
            CredentialDeviceType.MULTI_DEVICE if backup_eligible else CredentialDeviceType.SINGLE_DEVICE
        )
        self.credential_backed_up = backup_state
        self.fmt = AttestationFormat.NONE
        self.credential_type = PublicKeyCredentialType.PUBLIC_KEY
        self.user_verified = True
        self.attestation_object = b""


class _FakeVerifiedAuthentication:
    def __init__(self, credential_id: bytes, new_sign_count: int, backup_state: bool) -> None:
        self.credential_id = credential_id
        self.new_sign_count = new_sign_count
        self.credential_device_type = CredentialDeviceType.MULTI_DEVICE
        self.credential_backed_up = backup_state
        self.user_verified = True


def test_begin_registration_issues_a_fresh_challenge_each_call() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    options_one = json.loads(ceremony.begin_registration())
    options_two = json.loads(ceremony.begin_registration())
    assert options_one["challenge"] != options_two["challenge"]
    assert options_one["rp"]["id"] == INSTALL_NAME
    assert options_one["authenticatorSelection"]["userVerification"] == "required"


def test_finish_registration_rejects_an_unknown_challenge() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    ceremony.begin_registration()
    credential = _credential_json(challenge=b"never-issued-by-this-ceremony")
    with pytest.raises(webauthn_flow_module.WebAuthnError):
        ceremony.finish_registration(credential)


def test_finish_registration_records_backup_flags_from_the_verified_result() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    options = json.loads(ceremony.begin_registration())
    challenge = webauthn.base64url_to_bytes(options["challenge"])
    credential = _credential_json(challenge)

    fake_verified = _FakeVerifiedRegistration(credential_id=b"cred-id-bytes", backup_eligible=True, backup_state=True)
    with patch.object(webauthn_flow_module.webauthn, "verify_registration_response", return_value=fake_verified):
        stored = ceremony.finish_registration(credential)

    assert stored.backup_eligible is True
    assert stored.backup_state is True
    assert ceremony.has_credential is True


def test_finish_registration_wraps_a_py_webauthn_verification_failure() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    options = json.loads(ceremony.begin_registration())
    challenge = webauthn.base64url_to_bytes(options["challenge"])
    credential = _credential_json(challenge)

    with patch.object(
        webauthn_flow_module.webauthn, "verify_registration_response", side_effect=ValueError("bad attestation")
    ):
        with pytest.raises(webauthn_flow_module.WebAuthnError):
            ceremony.finish_registration(credential)


def test_a_registered_credential_is_excluded_from_a_later_registration_attempt() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    options = json.loads(ceremony.begin_registration())
    challenge = webauthn.base64url_to_bytes(options["challenge"])
    credential = _credential_json(challenge, credential_id=b"the-one-credential")
    fake_verified = _FakeVerifiedRegistration(
        credential_id=b"the-one-credential", backup_eligible=False, backup_state=False
    )
    with patch.object(webauthn_flow_module.webauthn, "verify_registration_response", return_value=fake_verified):
        ceremony.finish_registration(credential)

    next_options = json.loads(ceremony.begin_registration())
    excluded_ids = {webauthn.base64url_to_bytes(c["id"]) for c in next_options["excludeCredentials"]}
    assert b"the-one-credential" in excluded_ids


def test_begin_authentication_raises_when_no_passkey_is_enrolled() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    with pytest.raises(webauthn_flow_module.WebAuthnError):
        ceremony.begin_authentication()


def test_finish_authentication_rejects_an_unknown_credential_id() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    # Enrol one real credential so begin_authentication() is reachable.
    reg_options = json.loads(ceremony.begin_registration())
    reg_challenge = webauthn.base64url_to_bytes(reg_options["challenge"])
    reg_credential = _credential_json(reg_challenge, credential_id=b"enrolled-credential")
    fake_verified = _FakeVerifiedRegistration(
        credential_id=b"enrolled-credential", backup_eligible=False, backup_state=False
    )
    with patch.object(webauthn_flow_module.webauthn, "verify_registration_response", return_value=fake_verified):
        ceremony.finish_registration(reg_credential)

    auth_options = json.loads(ceremony.begin_authentication())
    auth_challenge = webauthn.base64url_to_bytes(auth_options["challenge"])
    unknown_credential = _credential_json(auth_challenge, credential_id=b"never-enrolled")
    with pytest.raises(webauthn_flow_module.WebAuthnError):
        ceremony.finish_authentication(unknown_credential)


def test_finish_authentication_updates_sign_count_and_backup_state() -> None:
    ceremony = webauthn_flow_module.WebAuthnCeremony(INSTALL_NAME, ORIGIN)
    reg_options = json.loads(ceremony.begin_registration())
    reg_challenge = webauthn.base64url_to_bytes(reg_options["challenge"])
    reg_credential = _credential_json(reg_challenge, credential_id=b"enrolled-credential")
    fake_registration = _FakeVerifiedRegistration(
        credential_id=b"enrolled-credential", backup_eligible=True, backup_state=False
    )
    with patch.object(webauthn_flow_module.webauthn, "verify_registration_response", return_value=fake_registration):
        ceremony.finish_registration(reg_credential)

    auth_options = json.loads(ceremony.begin_authentication())
    auth_challenge = webauthn.base64url_to_bytes(auth_options["challenge"])
    auth_credential = _credential_json(auth_challenge, credential_id=b"enrolled-credential")
    fake_authentication = _FakeVerifiedAuthentication(
        credential_id=b"enrolled-credential", new_sign_count=7, backup_state=True
    )
    with patch.object(
        webauthn_flow_module.webauthn, "verify_authentication_response", return_value=fake_authentication
    ):
        stored = ceremony.finish_authentication(auth_credential)

    assert stored.sign_count == 7
    assert stored.backup_state is True
