"""Exception taxonomy. Each error knows its HTTP status and CLI exit code.

Validation findings are never exceptions - they go into ``validation_errors``.
"""

from __future__ import annotations


class MedExtractError(Exception):
    http_status = 500
    exit_code = 1
    code = "internal_error"


# --- bad input (caller's fault) -------------------------------------------------------------
class InputError(MedExtractError):
    http_status = 422
    exit_code = 2
    code = "invalid_input"


class UnsupportedFileType(InputError):
    http_status = 415
    code = "unsupported_file_type"


class FileTooLarge(InputError):
    http_status = 413
    code = "file_too_large"


class EmptyDocument(InputError):
    code = "empty_document"


class UnreadablePDF(InputError):
    code = "unreadable_pdf"


class EncryptedPDF(InputError):
    code = "encrypted_pdf"


class TooManyPages(InputError):
    code = "too_many_pages"


# --- LLM provider -----------------------------------------------------------------------------
class ProviderError(MedExtractError):
    http_status = 502
    exit_code = 3
    code = "provider_error"


class ProviderAuthError(ProviderError):
    http_status = 500
    code = "provider_auth_error"


class ProviderUnavailable(ProviderError):
    http_status = 503
    code = "provider_unavailable"


class ModelRefusal(ProviderError):
    http_status = 422
    code = "model_refusal"


class OutputTruncated(ProviderError):
    code = "output_truncated"


class SchemaParseError(ProviderError):
    code = "schema_parse_error"


# --- configuration ----------------------------------------------------------------------------
class ConfigError(MedExtractError):
    exit_code = 4
    code = "config_error"
