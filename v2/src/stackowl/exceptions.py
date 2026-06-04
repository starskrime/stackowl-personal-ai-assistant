"""Base exception hierarchy for StackOwl (ARCH-88)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar, Literal

log = logging.getLogger("stackowl.security")


class StackOwlError(Exception):
    """Root exception for all StackOwl errors."""


class DomainError(StackOwlError):
    """Base for domain-logic errors (business rule violations)."""


class ToolRegistrationError(DomainError):
    """Raised when a tool registration is refused (name collision / dangerous shadow)."""

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(f"tool registration refused for {tool_name!r}: {reason}")
        self.tool_name = tool_name
        self.reason = reason


class InfrastructureError(StackOwlError):
    """Base for infrastructure / external-dependency errors."""


class TransientError(InfrastructureError):
    """Raised for errors that may resolve on retry (network blips, rate-limits)."""


class SecurityError(StackOwlError):
    """Raised for security violations (path traversal, policy breach).

    On every raise, three side-effects fire automatically:

    1. CRITICAL log via ``stackowl.security`` logger
    2. Audit log entry (via registered ``_audit_fn`` callback)
    3. Toast notification (via registered ``_notify_fn`` callback)

    Register the callbacks once at startup via :meth:`register_side_effects`.
    """

    _audit_fn: ClassVar[Callable[[str, dict[str, object]], None] | None] = None
    _notify_fn: ClassVar[Callable[[str], None] | None] = None

    @classmethod
    def register_side_effects(
        cls,
        audit_fn: Callable[[str, dict[str, object]], None] | None,
        notify_fn: Callable[[str], None] | None,
    ) -> None:
        """Register callbacks for audit logging and toast notifications.

        Pass ``None`` to clear either callback (useful in tests).
        """
        cls._audit_fn = audit_fn
        cls._notify_fn = notify_fn

    def __init__(
        self,
        message: str,
        category: Literal[
            "nfr33",
            "path_traversal",
            "policy_breach",
            "capability_denied",
            "boundary_violation",
            "plugin_capability_denied",
            "consequential_action_blocked",
            "export_sanitization_failed",
            "audit_integrity_broken",
        ] = "nfr33",
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.category: str = category
        self.context: dict[str, object] = context or {}

        # Side-effect 1 — CRITICAL log
        log.critical(
            "security violation: %s",
            message,
            extra={"_fields": {"category": category, "context": self.context}},
        )

        # Side-effect 2 — audit log entry
        # Access via type() to avoid Python's descriptor protocol binding the
        # stored callable as a bound method (which would prepend self as an arg).
        _audit = type(self).__dict__.get("_audit_fn") or SecurityError.__dict__.get("_audit_fn")
        if _audit is not None:
            try:
                _audit("security_violation", {"category": category, **self.context})
            except Exception as _exc:
                log.error("SecurityError._audit_fn raised", exc_info=_exc)

        # Side-effect 3 — toast notification
        _notify = type(self).__dict__.get("_notify_fn") or SecurityError.__dict__.get("_notify_fn")
        if _notify is not None:
            try:
                _notify(f"Security violation: {message}")
            except Exception as _exc:
                log.error("SecurityError._notify_fn raised", exc_info=_exc)


class ConfigurationError(StackOwlError):
    """Raised when configuration loading or secret resolution fails."""


class MigrationError(StackOwlError):
    """Raised when a database migration fails."""

    def __init__(self, migration: str, reason: str) -> None:
        self.migration = migration
        self.reason = reason
        super().__init__(f"Migration {migration} failed: {reason}")


class FilesystemProbeError(StackOwlError):
    """Raised when the filesystem probe finds an unusable path."""

    def __init__(self, check: str, path: str) -> None:
        self.check = check
        self.path = path
        super().__init__(f"Filesystem probe failed [{check}]: {path}")


class A2ATimeoutError(DomainError):
    """Raised when A2AQueue.receive() times out waiting for a message."""

    def __init__(self, owl_name: str) -> None:
        self.owl_name = owl_name
        super().__init__(f"A2A receive timeout for owl '{owl_name}'")


class ProviderNotFoundError(DomainError):
    """Raised when a requested provider is not registered."""

    def __init__(self, name: str) -> None:
        self.provider_name = name
        super().__init__(f"Provider not found: '{name}'")


class ProviderError(InfrastructureError):
    """Raised when a ModelProvider call fails."""

    def __init__(self, provider_name: str, cause: BaseException) -> None:
        self.provider_name = provider_name
        self.cause = cause
        super().__init__(f"Provider '{provider_name}' error: {cause}")


class StartupError(StackOwlError):
    """Raised by StartupOrchestrator when a phase fails."""

    def __init__(self, phase: int, name: str, reason: str) -> None:
        self.phase = phase
        self.name = name
        self.reason = reason
        super().__init__(f"✗ Startup failed at phase {phase} ({name}): {reason}")


class ManifestValidationError(DomainError):
    """Raised when an owl manifest fails validation."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Owl manifest validation failed [{field}]: {reason}")


class OwlNotFoundError(DomainError):
    """Raised when a requested owl is not in the registry."""

    def __init__(self, name: str) -> None:
        self.owl_name = name
        super().__init__(f"Owl not found: '{name}'")


class CommandNotFoundError(DomainError):
    """Raised when a slash command is not registered."""

    def __init__(self, name: str) -> None:
        self.command_name = name
        super().__init__(f"Unknown command: '/{name}'")


class CommandParseError(DomainError):
    """Raised when a slash command receives malformed arguments."""

    def __init__(self, command: str, reason: str) -> None:
        self.command_name = command
        self.reason = reason
        super().__init__(f"/{command}: {reason}")


class ToolExecutionError(InfrastructureError):
    """Raised when a tool execute() call fails unexpectedly."""

    def __init__(self, tool_name: str, cause: BaseException) -> None:
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' failed: {cause}")


class CircuitOpenError(InfrastructureError):
    """Raised when CircuitBreaker is OPEN and blocks a provider call."""

    def __init__(self, provider_name: str, retry_after_seconds: float) -> None:
        self.provider_name = provider_name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Circuit open for '{provider_name}' — retry after {retry_after_seconds:.0f}s")


class AllProvidersUnavailableError(InfrastructureError):
    """Raised when cascade finds all providers OPEN."""

    def __init__(self, details: list[str]) -> None:
        self.details = details
        super().__init__("All providers unavailable: " + "; ".join(details))


class OwlTimeoutError(InfrastructureError):
    """Raised when an owl execution exceeds its timeout_seconds limit."""

    def __init__(self, owl_name: str, timeout_seconds: float) -> None:
        self.owl_name = owl_name
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Owl '{owl_name}' timed out after {timeout_seconds:.1f}s")


class OwlTokenLimitError(DomainError):
    """Raised when an owl response is truncated at max_tokens."""

    def __init__(self, owl_name: str, max_tokens: int, actual_tokens: int) -> None:
        self.owl_name = owl_name
        self.max_tokens = max_tokens
        self.actual_tokens = actual_tokens
        super().__init__(f"Owl '{owl_name}' response truncated at {max_tokens} tokens ({actual_tokens} generated)")


class OwlConcurrencyError(DomainError):
    """Raised when an owl's concurrency semaphore cannot be acquired."""

    def __init__(self, owl_name: str, max_concurrent: int) -> None:
        self.owl_name = owl_name
        self.max_concurrent = max_concurrent
        super().__init__(f"Owl '{owl_name}' concurrency limit reached ({max_concurrent} max)")


class ParliamentTimeoutError(InfrastructureError):
    """Raised when a Parliament session exceeds its hard wall-clock budget."""

    def __init__(self, session_id: str, elapsed_s: float) -> None:
        self.session_id = session_id
        self.elapsed_s = elapsed_s
        super().__init__(f"Parliament session {session_id} timed out after {elapsed_s:.1f}s")


class ParliamentTokenBudgetError(DomainError):
    """Raised when a Parliament owl's cumulative output exceeds the token budget."""

    def __init__(self, owl_name: str, token_count: int, budget: int) -> None:
        self.owl_name = owl_name
        self.token_count = token_count
        self.budget = budget
        super().__init__(
            f"Token budget exceeded for {owl_name}: {token_count} > {budget}"
        )


class DuplicateFactError(DomainError):
    """Raised when a fact_id is already present in the staged queue."""

    def __init__(self, fact_id: str) -> None:
        self.fact_id = fact_id
        super().__init__(f"Fact {fact_id!r} is already staged")


class MemoryBudgetExceededError(DomainError):
    """Raised when per-user memory usage would exceed the configured ceiling."""

    def __init__(self, usage_bytes: int, ceiling_bytes: int) -> None:
        self.usage_bytes = usage_bytes
        self.ceiling_bytes = ceiling_bytes
        super().__init__(
            f"Memory budget exceeded: {usage_bytes} >= {ceiling_bytes} bytes"
        )


class MemoryDeletePartialError(InfrastructureError):
    """Raised when a delete operation succeeded in some stores but failed in others."""

    def __init__(self, fact_id: str, failed_stores: list[str]) -> None:
        self.fact_id = fact_id
        self.failed_stores = failed_stores
        super().__init__(
            f"Partial delete of {fact_id}: failed in {failed_stores}"
        )


class FactExtractionParseError(DomainError):
    """Raised when an LLM fact-extraction response cannot be parsed."""

    def __init__(self, reason: str, raw_response_excerpt: str) -> None:
        self.reason = reason
        self.raw_response_excerpt = raw_response_excerpt[:500]
        super().__init__(f"Fact extraction parse failed: {reason}")


class SchedulerError(DomainError):
    """Raised when a scheduler operation (pause/resume/stop/recover) fails."""


class ChannelNotFoundError(DomainError):
    """Raised when a requested channel adapter is not registered."""

    def __init__(self, name: str) -> None:
        self.channel_name = name
        super().__init__(f"Channel not found: '{name}'")


class ChannelAlreadyRegisteredError(DomainError):
    """Raised when attempting to register a channel adapter whose name is taken."""

    def __init__(self, name: str) -> None:
        self.channel_name = name
        super().__init__(f"Channel already registered: '{name}'")


class PluginValidationError(DomainError):
    """Raised when a plugin manifest or implementation fails validation."""

    def __init__(self, plugin: str, reason: str) -> None:
        super().__init__(f"Plugin {plugin!r}: {reason}")
        self.plugin = plugin
        self.reason = reason


class PluginNotFoundError(DomainError):
    """Raised when a requested plugin is not found in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Plugin {name!r} not found")
        self.name = name


class PluginCapabilityDeniedError(SecurityError):
    """Raised when a plugin accesses a capability not granted in its manifest."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"Capability {capability!r} not granted to this plugin")
        self.capability = capability


class IntegrationNotFoundError(DomainError):
    """Raised when a requested integration service is not registered."""

    def __init__(self, service_name: str) -> None:
        super().__init__(f"Integration {service_name!r} not registered")
        self.service_name = service_name


class UnsupportedActionError(DomainError):
    """Raised by IntegrationAdapter.execute_action for unrecognized action names."""

    def __init__(self, service_name: str, action: str) -> None:
        super().__init__(f"Integration {service_name!r} does not support action {action!r}")
        self.service_name = service_name
        self.action = action


class PrincipalNotFoundError(DomainError):
    """Raised when a requested owner principal is not in the principals table."""

    def __init__(self, principal_id: str) -> None:
        super().__init__(f"Principal not found: '{principal_id}'")
        self.principal_id = principal_id


class PidFileExistsError(StackOwlError):
    """Raised when a PID file already exists and the recorded process is still alive."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__(f"StackOwl is already running (PID {pid})")
