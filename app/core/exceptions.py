"""Application errors raised by configuration and the LLM client.

Provider SDKs raise their own exception types. The rest of the application
should depend on the exceptions in this module instead.
"""


class LLMError(Exception):
    """Base class for errors raised while preparing or making an LLM request."""


class LLMConfigurationError(LLMError):
    """LLM settings are missing, blank, or use an unsupported provider."""


class LLMProviderError(LLMError):
    """The provider failed, or its response could not be normalized."""


class LLMTimeoutError(LLMError):
    """The provider did not respond before the configured timeout."""
