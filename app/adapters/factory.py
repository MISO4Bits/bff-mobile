"""Fábrica de adaptadores del BFF móvil (sin framework de DI)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from app.adapters.core_client import CoreClientAdapter
from app.adapters.cotizacion_client import CotizacionClientAdapter
from app.adapters.fakes import (
    FakeCoreIdentity,
    FakeCotizacion,
    FakeCreditosHipotecarios,
    FakeDispositivos,
    FakeDocumentosLegales,
    FakeEntidadesFinancieras,
    FakeEvidencias,
    FakeIdentityProvider,
    FakePolizas,
)
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.adapters.perfilamiento_client import PerfilamientoClientAdapter
from app.adapters.productos_client import ProductosClientAdapter
from app.config import Settings
from app.domain import ClienteCore
from app.ports import (
    CoreIdentityPort,
    CotizacionPort,
    CreditosHipotecariosPort,
    DispositivosPort,
    DocumentosLegalesPort,
    EntidadesFinancierasPort,
    EvidenciasIdentidadPort,
    IdentityProviderPort,
    PolizasPort,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.security import SessionIssuer


@dataclass
class Dependencias:
    identity: IdentityProviderPort
    core: CoreIdentityPort
    sessions: SessionIssuer
    creditos: CreditosHipotecariosPort
    entidades: EntidadesFinancierasPort
    cotizacion: CotizacionPort
    # En modo http es el mismo adaptador que ``core``: se cierra una sola vez.
    evidencias: EvidenciasIdentidadPort
    polizas: PolizasPort
    documentos_legales: DocumentosLegalesPort
    # En modo http es el mismo adaptador que ``core``.
    dispositivos: DispositivosPort

    async def aclose(self) -> None:
        for adapter in (self.identity, self.core, self.creditos, self.entidades, self.cotizacion):
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


_SUB_DEMO = "sub-demo"


def _fakes(settings: Settings) -> tuple[FakeIdentityProvider, FakeCoreIdentity]:
    email, clave = settings.fake_demo_email, settings.fake_demo_password
    if not email or not clave:
        return FakeIdentityProvider(), FakeCoreIdentity()
    return (
        FakeIdentityProvider({email: (_SUB_DEMO, clave)}),
        FakeCoreIdentity(
            {
                _SUB_DEMO: ClienteCore(
                    id="cliente-demo", email=email, fecha_nacimiento=date(1990, 1, 1)
                )
            }
        ),
    )


def build_dependencias(settings: Settings) -> Dependencias:
    sessions = SessionIssuer(
        settings.session_secret,
        ttl_seconds=settings.session_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_ttl_seconds,
    )

    if settings.adapters == "fake":
        identity, core = _fakes(settings)
        return Dependencias(
            identity,
            core,
            sessions,
            FakeCreditosHipotecarios(),
            FakeEntidadesFinancieras(),
            FakeCotizacion(),
            FakeEvidencias(),
            FakePolizas(),
            FakeDocumentosLegales(),
            FakeDispositivos(),
        )

    if settings.adapters == "http":
        core = CoreClientAdapter(
            _cliente_http(settings, settings.core_base_url, "svc-core"),
            timeout_subida=httpx.Timeout(
                settings.evidencias_timeout_seconds, pool=settings.http_pool_timeout_seconds
            ),
            timeout_polizas=httpx.Timeout(
                settings.polizas_timeout_seconds, pool=settings.http_pool_timeout_seconds
            ),
        )
        productos = ProductosClientAdapter(
            _cliente_http(settings, settings.productos_base_url, "svc-productos")
        )
        return Dependencias(
            IdentityPlatformAdapter(
                _cliente_http(settings, settings.identity_base_url, "identity-platform"),
                settings.identity_api_key,
            ),
            core,
            sessions,
            PerfilamientoClientAdapter(
                _cliente_http(settings, settings.perfilamiento_base_url, "svc-perfilamiento")
            ),
            productos,
            CotizacionClientAdapter(
                _cliente_http(settings, settings.cotizacion_base_url, "svc-cotizacion")
            ),
            core,
            core,
            productos,
            core,
        )

    raise ValueError(f"adapters no soportado: {settings.adapters}")
