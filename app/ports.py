"""Puertos de salida del BFF móvil."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import (
    ClienteCore,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    DocumentoLegal,
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


@runtime_checkable
class PolizasPort(Protocol):
    """Solicitud y consulta de pólizas en svc-core (BITS-101). Devuelve el cuerpo tal cual lo
    entrega svc-core (ya validado allá); los errores salen como ``BffError`` con su ``codigo``."""

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        """Crea la solicitud. El booleano es ``True`` si se creó ahora, ``False`` si ya existía."""
        ...

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]: ...

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        """La póliza; si está pendiente, svc-core la re-evalúa antes de responder."""
        ...

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        """Estado del documento. El booleano es ``True`` si ya está listo (``False``: se está
        generando)."""
        ...


@runtime_checkable
class DocumentosLegalesPort(Protocol):
    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        """Sin ``tipo``, los del registro; con ``tipo``, ese documento (p. ej. ``poliza``)."""
        ...

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal: ...
