import base64
import json

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from my_team.errors import Invalid

RP_ID = "localhost"
RP_NAME = "my-team"
USER_ID = b"my-team-human"
CHALLENGE_TTL_MS = 5 * 60_000


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def registration_challenge(existing: list[bytes]) -> tuple[bytes, dict]:
    options = generate_registration_options(
        rp_id=RP_ID, rp_name=RP_NAME, user_id=USER_ID, user_name="human",
        exclude_credentials=[PublicKeyCredentialDescriptor(id=credential) for credential in existing])
    return options.challenge, json.loads(options_to_json(options))


def verify_registration(credential: dict, challenge: bytes, origin: str) -> dict:
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=RP_ID,
            expected_origin=origin, require_user_verification=False)
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as exc:
        raise Invalid("bad_registration", f"Passkey registration failed: {exc}.") from exc
    return {"credential_id": b64u_encode(verified.credential_id),
            "public_key": verified.credential_public_key, "sign_count": verified.sign_count}


def authentication_challenge(credentials: list[bytes]) -> tuple[bytes, dict]:
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=credential) for credential in credentials])
    return options.challenge, json.loads(options_to_json(options))


def verify_authentication(credential: dict, challenge: bytes, origin: str, public_key: bytes,
                          sign_count: int) -> int:
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=RP_ID,
            expected_origin=origin, credential_public_key=public_key,
            credential_current_sign_count=sign_count, require_user_verification=False)
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as exc:
        raise Invalid("bad_authentication", f"Passkey login failed: {exc}.") from exc
    if verified.new_sign_count < sign_count:
        raise Invalid("cloned_authenticator", "Passkey sign count went backwards; it may be cloned.")
    return verified.new_sign_count


def challenge_of(credential: dict) -> str:
    try:
        client_data = json.loads(b64u_decode(credential["response"]["clientDataJSON"]))
        return str(client_data["challenge"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid("bad_credential", f"Credential has no challenge: {exc}.") from exc


def credential_id_of(credential: dict) -> str:
    try:
        return str(credential["id"])
    except (KeyError, TypeError) as exc:
        raise Invalid("bad_credential", f"Credential has no id: {exc}.") from exc
