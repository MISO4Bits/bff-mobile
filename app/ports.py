"""Puertos de salida del BFF móvil."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import (
    ClienteCore,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    ResultadoEvidencia,
)


@runtime_checkable
class IdentityProviderPort(Protocol):
    async def autenticar(self, email: str, password: str) -> str:
        """Valida credenciales y devuelve el ``sub``. Lanza ``NoAutorizado`` si fallan."""
        ...


@runtime_checkable
class CoreIdentityPort(Protocol):
    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore: ...


@runtime_checkable
class CreditosHipotecariosPort(Protocol):
    async def obtener(self, cliente_id: str) -> CreditosReportados:
        """Hipotecas abiertas que Perfilamiento guardó de Open Finance para el cliente."""
        ...


@runtime_checkable
class EntidadesFinancierasPort(Protocol):
    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]: ...


@runtime_checkable
class CotizacionPort(Protocol):
    async def crear_cotizacion(
        self, cliente_id: str, edad: int, entrada: CotizacionInput
    ) -> Cotizacion:
        """Calcula la cotización y la deja como la activa del cliente (reemplaza la anterior)."""
        ...

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion: ...

    async def eliminar_cotizacion_activa(self, cliente_id: str) -> None: ...


@runtime_checkable
class EvidenciasIdentidadPort(Protocol):
    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        """Una foto de identidad; svc-core decide si sirve. Lanza ``RecursoNoEncontrado``
        (cliente), ``SolicitudInvalida`` (no es una imagen) o ``ImagenMuyGrande``."""
        ...

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        """Estado vigente de cada tipo de evidencia (siempre los tres)."""
        ...
