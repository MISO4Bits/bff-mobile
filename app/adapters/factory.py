"""Fábrica de adaptadores del BFF móvil (sin framework de DI)."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeCoreIdentity, FakeIdentityProvider
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.config import Settings
from app.ports import CoreIdentityPort, IdentityProviderPort
from app.resilience import ResilientHttpClient, build_breaker
from app.security import SessionIssuer


@dataclass
class Dependencias:
    identity: IdentityProviderPort
    core: CoreIdentityPort
    sessions: SessionIssuer

    async def aclose(self) -> None:
        for adapter in (self.identity, self.core):
            cerrar = getattr(adapter, "aclose", None)
            if cerrar is not None:
                await cerrar()


def _cliente_http(settings: Settings, base_url: str, nombre: str) -> ResilientHttpClient:
    return ResilientHttpClient(
        base_url,
        breaker=build_breaker(
            nombre,
            fail_max=settings.circuit_fail_max,
            reset_timeout=settings.circuit_reset_timeout_seconds,
        ),
        timeout=settings.http_timeout_seconds,
        retries=settings.http_retries,
        pool_timeout=settings.http_pool_timeout_seconds,
        max_connections=settings.http_max_connections,
        max_keepalive_connections=settings.http_max_keepalive_connections,
    )


def build_dependencias(settings: Settings) -> Dependencias:
    sessions = SessionIssuer(
        settings.session_secret,
        ttl_seconds=settings.session_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_ttl_seconds,
    )

    if settings.adapters == "fake":
        return Dependencias(FakeIdentityProvider(), FakeCoreIdentity(), sessions)

    if settings.adapters == "http":
        return Dependencias(
            IdentityPlatformAdapter(
                _cliente_http(settings, settings.identity_base_url, "identity-platform"),
                settings.identity_api_key,
            ),
            CoreClientAdapter(_cliente_http(settings, settings.core_base_url, "svc-core")),
            sessions,
        )

    raise ValueError(f"adapters no soportado: {settings.adapters}")
