"""Puertos de salida del BFF móvil."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import ClienteCore, CreditosReportados, EntidadFinanciera


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
