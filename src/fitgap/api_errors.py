"""Readable diagnostics for Anthropic API failures.

The SDK raises ``APIStatusError`` subclasses whose ``str()`` is often just
``Error code: 400`` — the SDK only appends the error body when the response
actually carried one. That is useless at a terminal, and worse, it is rendered
as a raw traceback in the middle of a progress bar.

``api_guard`` wraps a pipeline stage and turns any API failure into a
structured report: status, error type, message, body, request id, the response
headers that matter, and the environment variables that can silently redirect
or break a request.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import typer

#: Response headers worth showing. ``request-id`` is the important one: every
#: genuine api.anthropic.com response carries it, so its absence means the
#: response was produced by something else on the path.
_HEADERS = (
    "request-id",
    "anthropic-organization-id",
    "retry-after",
    "x-should-retry",
    "content-type",
    "content-length",
    "server",
    "via",
    "cf-ray",
)

#: Environment variables that change where a request goes or how TLS is done.
#: None hold secrets, so the values are safe to print.
_ENV_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
)


def _hint(status: int | None, body, request_id: str | None) -> str:
    """Best interpretation of the failure, given what came back."""
    text = str(body).lower() if body else ""

    if "credit balance" in text or "billing" in text:
        return (
            "Your organisation's credit balance is too low. Anthropic returns "
            "this as a 400, not a 402.\n"
            "  Fix: https://console.anthropic.com/settings/billing"
        )
    if status == 400 and not body:
        return (
            "The 400 carried an EMPTY body"
            + (
                " and no request-id, so it did not come from api.anthropic.com.\n"
                "  Something on the network path rejected the request — a "
                "corporate proxy, VPN, TLS-inspecting gateway, or endpoint\n"
                "  security agent. Check the environment above, and try the "
                "same call off the corporate network / VPN."
                if not request_id
                else ".\n  It did reach Anthropic (a request-id came back), so "
                "quote that request-id to Anthropic support."
            )
        )
    if status == 401:
        return (
            "Authentication failed. The key is set but was rejected — it may be "
            "revoked, truncated, or from another organisation.\n"
            "  Keys: https://console.anthropic.com/settings/keys"
        )
    if status == 403:
        return (
            "The key is valid but lacks permission for this model or feature. "
            "Check the workspace the key belongs to."
        )
    if status == 404:
        return (
            "Unknown model or endpoint. Check the `model:` value in fitgap.yaml "
            "against https://docs.claude.com/en/docs/about-claude/models"
        )
    if status == 429:
        return "Rate limited. Wait for the retry-after above, or lower --batch-size."
    if status is not None and status >= 500:
        return "Anthropic-side error. Retry with backoff; check status.anthropic.com."
    return ""


def _report(stage: str, exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    request_id = headers.get("request-id")

    lines = [
        f"Anthropic API call failed during '{stage}'.",
        "",
        f"  exception   : {type(exc).__name__}",
    ]
    if status is not None:
        lines.append(f"  http status : {status}")
    if isinstance(body, dict):
        err = body.get("error") or {}
        if err.get("type"):
            lines.append(f"  error type  : {err['type']}")
        if err.get("message"):
            lines.append(f"  api message : {err['message']}")

    message = getattr(exc, "message", None) or str(exc)
    lines.append(f"  sdk message : {message}")

    if body:
        raw = repr(body)
        lines.append(f"  raw body    : {raw[:1500]}{'...' if len(raw) > 1500 else ''}")
    elif status is not None:
        lines.append("  raw body    : (empty — the response had no content)")

    present = [(h, headers[h]) for h in _HEADERS if h in headers]
    if present:
        lines.append("  headers     :")
        lines.extend(f"      {name}: {value}" for name, value in present)
    elif response is not None:
        lines.append("  headers     : (none of interest returned)")

    env = [(name, os.environ[name]) for name in _ENV_VARS if os.environ.get(name)]
    lines.append("  environment :")
    if env:
        lines.extend(f"      {name}={value}" for name, value in env)
    else:
        lines.append("      (no base-url, proxy, or TLS overrides set)")

    hint = _hint(status, body, request_id)
    if hint:
        lines.extend(["", f"Likely cause: {hint}"])
    return "\n".join(lines)


@contextmanager
def api_guard(stage: str):
    """Report Anthropic API failures in ``stage`` legibly, then exit non-zero.

    Non-API exceptions propagate untouched — only the SDK's own error types are
    intercepted, so genuine bugs still raise a normal traceback.
    """
    import anthropic

    try:
        yield
    except anthropic.APIStatusError as exc:
        typer.secho("\n" + _report(stage, exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    except anthropic.APIConnectionError as exc:
        typer.secho(
            f"\nCould not reach the Anthropic API during '{stage}': "
            f"{type(exc).__name__}: {exc}\n"
            "  Check network connectivity, proxy settings, and TLS interception.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from None
