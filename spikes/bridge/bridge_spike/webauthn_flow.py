"""py_webauthn passkey registration/authentication at the install-name
origin (AD-16, FR66): the RP ID is always the install name, the expected
origin is exact (never a wildcard), user verification is required on both
ceremonies, and the backup-eligible/backup-state flags a verified
registration reports are recorded rather than discarded -- they are what
lets a later story tell "this passkey syncs" from "this one doesn't"
without re-deriving it.

Single-owner spike: one RP identity (this kit's install name) and one
`user_id`/`user_name` ("owner"). A second device is never enrolled with its
own passkey here -- it is vouched for by an already-signed-in device via
the name+matching-code ceremony in `device_requests.py` instead. Multi-user
enrolment is out of scope for this story.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass

import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

logger = logging.getLogger("bridge_spike.webauthn_flow")

CHALLENGE_TTL_SECONDS = 300.0


class WebAuthnError(Exception):
    """Raised for any registration/authentication failure. Callers map this
    to a 400/403/409 -- `401` is reserved for the signed-request scheme in
    `tokens.py`, a different failure mode (an already-enrolled device making
    a bad request) than "this ceremony didn't verify"."""


@dataclass
class StoredCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    backup_eligible: bool  # WebAuthn "BE" flag (credential_device_type == multi_device)
    backup_state: bool  # WebAuthn "BS" flag (credential_backed_up)


class WebAuthnCeremony:
    """One RP identity's worth of passkey state: pending challenges plus
    whatever credential(s) have been verified so far."""

    def __init__(self, install_name: str, origin: str) -> None:
        self.rp_id = install_name
        self.rp_name = install_name
        self.origin = origin
        self.user_id = secrets.token_bytes(16)
        self.user_name = "owner"
        self._credentials: dict[bytes, StoredCredential] = {}
        self._pending_challenges: dict[bytes, float] = {}

    @property
    def has_credential(self) -> bool:
        return bool(self._credentials)

    # -- registration ----------------------------------------------------

    def begin_registration(self) -> str:
        options = webauthn.generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=self.user_id,
            user_name=self.user_name,
            user_display_name=self.user_name,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=cred_id) for cred_id in self._credentials],
        )
        self._remember_challenge(options.challenge)
        return webauthn.options_to_json(options)

    def finish_registration(self, credential_json: str | dict) -> StoredCredential:
        challenge = self._extract_challenge(credential_json)
        self._consume_challenge(challenge)
        try:
            verified = webauthn.verify_registration_response(
                credential=credential_json,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except Exception as exc:  # py_webauthn raises its own exception hierarchy
            raise WebAuthnError(f"registration verification failed: {exc}") from exc
        stored = StoredCredential(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            backup_eligible=verified.credential_device_type.value == "multi_device",
            backup_state=verified.credential_backed_up,
        )
        self._credentials[stored.credential_id] = stored
        logger.info(
            "bridge_spike.webauthn_flow: passkey registered — backup_eligible=%s backup_state=%s",
            stored.backup_eligible,
            stored.backup_state,
        )
        return stored

    # -- authentication ----------------------------------------------------

    def begin_authentication(self) -> str:
        if not self._credentials:
            raise WebAuthnError("no passkey enrolled yet -- register one first")
        options = webauthn.generate_authentication_options(
            rp_id=self.rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
            allow_credentials=[PublicKeyCredentialDescriptor(id=cred_id) for cred_id in self._credentials],
        )
        self._remember_challenge(options.challenge)
        return webauthn.options_to_json(options)

    def finish_authentication(self, credential_json: str | dict) -> StoredCredential:
        challenge = self._extract_challenge(credential_json)
        self._consume_challenge(challenge)
        credential_id = self._extract_credential_id(credential_json)
        stored = self._credentials.get(credential_id)
        if stored is None:
            raise WebAuthnError("unknown credential id")
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential_json,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            raise WebAuthnError(f"authentication verification failed: {exc}") from exc
        # Sign count and backup state can change on every authentication
        # (a cloud-synced passkey's backup state can flip; sign count
        # increments on authenticators that track one) -- refreshed here so
        # the stored record never goes stale after the first ceremony.
        stored.sign_count = verified.new_sign_count
        stored.backup_state = verified.credential_backed_up
        return stored

    # -- helpers ----------------------------------------------------

    def _remember_challenge(self, challenge: bytes) -> None:
        self._pending_challenges[challenge] = time.monotonic() + CHALLENGE_TTL_SECONDS

    def _consume_challenge(self, challenge: bytes) -> None:
        expiry = self._pending_challenges.pop(challenge, None)
        if expiry is None:
            raise WebAuthnError("no pending ceremony for this challenge (unknown, or already used)")
        if time.monotonic() > expiry:
            raise WebAuthnError("ceremony challenge expired")

    @staticmethod
    def _credential_payload(credential_json: str | dict) -> dict:
        return json.loads(credential_json) if isinstance(credential_json, str) else credential_json

    @classmethod
    def _extract_challenge(cls, credential_json: str | dict) -> bytes:
        payload = cls._credential_payload(credential_json)
        try:
            client_data_json = webauthn.base64url_to_bytes(payload["response"]["clientDataJSON"])
        except (KeyError, TypeError) as exc:
            raise WebAuthnError("credential is missing response.clientDataJSON") from exc
        try:
            client_data = json.loads(client_data_json)
            return webauthn.base64url_to_bytes(client_data["challenge"])
        except (ValueError, KeyError) as exc:
            raise WebAuthnError("clientDataJSON is missing a usable challenge") from exc

    @classmethod
    def _extract_credential_id(cls, credential_json: str | dict) -> bytes:
        payload = cls._credential_payload(credential_json)
        try:
            return webauthn.base64url_to_bytes(payload["id"])
        except (KeyError, TypeError) as exc:
            raise WebAuthnError("credential is missing id") from exc
