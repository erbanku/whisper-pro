from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError


class WhisperProProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        api_key = credentials.get("api_key")
        if api_key is None or api_key == "":
            return
        if not isinstance(api_key, str) or not api_key.strip() or any(not 33 <= ord(character) <= 126 for character in api_key.strip()):
            raise ToolProviderCredentialValidationError("API Key must be a printable Bearer token without spaces, or empty for no authentication.")
