import logging

from ompass.client import OmpassClient
from ompass.config import OmpassConfig
from ompass.enums import Language, LoginClientType
from ompass.exceptions import OmpassApiException
from ompass.models.request import AuthStartRequest, TokenVerifyRequest
from ompass.models.response import AuthStartResponse, TokenVerifyResponse

logger = logging.getLogger(__name__)


class OmpassService:

    def __init__(self, client_id: str, secret_key: str, base_url: str):
        config = OmpassConfig(
            client_id=client_id,
            secret_key=secret_key,
            base_url=base_url,
        )
        self._client = OmpassClient(config)

    def start_auth(self, username: str) -> AuthStartResponse:
        logger.info("Starting OMPASS auth for user: %s", username)
        request = AuthStartRequest(
            username=username,
            lang_init=Language.KR,
            login_client_type=LoginClientType.BROWSER,
            session_timeout_seconds=300,
        )
        return self._client.start_auth(request)

    def verify_token(self, username: str, token: str) -> TokenVerifyResponse:
        logger.info("Verifying OMPASS token for user: %s", username)
        request = TokenVerifyRequest(username=username, token=token)
        return self._client.verify_token(request)

    def has_authenticators(self, username: str) -> bool:
        logger.info("Checking OMPASS authenticators for user: %s", username)
        try:
            response = self._client.get_authenticators(username)
            return bool(response.authenticators)
        except OmpassApiException as e:
            logger.warning("Failed to get authenticators for user %s: %s", username, e)
            return False

    def delete_all_authenticators(self, username: str) -> int:
        logger.info("Deleting all OMPASS authenticators for user: %s", username)
        response = self._client.get_authenticators(username)
        if not response.authenticators:
            return 0
        deleted = 0
        for auth in response.authenticators:
            try:
                self._client.delete_authenticator(auth.id)
                deleted += 1
            except OmpassApiException as e:
                logger.warning("Failed to delete authenticator %s: %s", auth.id, e)
        return deleted
