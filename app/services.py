"""Orquestación de la sesión del cliente móvil."""

from __future__ import annotations

import logging

from app.domain import Sesion
from app.ports import CoreIdentityPort, IdentityProviderPort
from app.security import SessionIssuer

logger = logging.getLogger("bff_mobile.sesion")


class SesionService:
    def __init__(
        self,
        identity: IdentityProviderPort,
        core: CoreIdentityPort,
        sessions: SessionIssuer,
    ) -> None:
        self._identity = identity
        self._core = core
        self._sessions = sessions

    async def iniciar_sesion(self, email: str, password: str) -> Sesion:
        logger.info("iniciar_sesion: autenticando contra Identity Platform")
        sub = await self._identity.autenticar(email, password)
        cliente = await self._core.buscar_cliente_por_identidad(sub)
        logger.info("iniciar_sesion: sesión iniciada cliente_id=%s", cliente.id)
        return self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)

    def refrescar(self, refresh_token: str) -> Sesion:
        return self._sessions.refrescar(refresh_token)
