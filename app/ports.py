"""Puertos de salida del BFF móvil."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import ClienteCore


@runtime_checkable
class IdentityProviderPort(Protocol):
    async def autenticar(self, email: str, password: str) -> str:
        """Valida credenciales y devuelve el ``sub``. Lanza ``NoAutorizado`` si fallan."""
        ...


@runtime_checkable
class CoreIdentityPort(Protocol):
    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore: ...
