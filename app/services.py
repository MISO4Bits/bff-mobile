"""Orquestación de la sesión del cliente móvil."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Callable

from app.domain import (
    ESTADO_DISPONIBLE,
    ESTADO_NO_DISPONIBLE,
    BffError,
    CreditoHipotecario,
    CreditosHipotecarios,
    EntidadFinanciera,
    Sesion,
)
from app.ports import (
    CoreIdentityPort,
    CreditosHipotecariosPort,
    EntidadesFinancierasPort,
    IdentityProviderPort,
)
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


_TOKEN = re.compile(r"[a-z0-9]+")


def _clave(nombre: str) -> str:
    """Forma comparable de un nombre de entidad: sin tildes, mayúsculas ni puntuación y sin
    el sufijo societario, para que ``BANCO DE BOGOTÁ S.A.`` y ``Banco de Bogotá`` coincidan."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    tokens = _TOKEN.findall(sin_tildes.lower())
    if tokens[-2:] == ["s", "a"]:
        tokens = tokens[:-2]
    elif tokens[-1:] == ["sa"]:
        tokens = tokens[:-1]
    return " ".join(tokens)


class CreditosHipotecariosService:
    """Pantalla previa a la cotización: las hipotecas del cliente, ya traídas de Open
    Finance por Perfilamiento, junto con la lista de bancos del mercado.

    Si Perfilamiento no responde, la respuesta es ``NO_DISPONIBLE`` con la lista de bancos:
    el cliente reintenta o escribe los datos a mano, nunca se bloquea.
    """

    def __init__(
        self,
        creditos: CreditosHipotecariosPort,
        entidades: EntidadesFinancierasPort,
        *,
        cache_segundos: float = 3600,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._creditos = creditos
        self._entidades = entidades
        self._cache_segundos = cache_segundos
        self._reloj = reloj
        self._cache: dict[str, tuple[float, tuple[EntidadFinanciera, ...]]] = {}

    async def obtener(self, cliente_id: str, mercado: str) -> CreditosHipotecarios:
        reportados, entidades = await asyncio.gather(
            self._creditos.obtener(cliente_id),
            self._entidades_del_mercado(mercado),
            return_exceptions=True,
        )
        if isinstance(reportados, BaseException):
            if not isinstance(reportados, BffError):
                raise reportados
            logger.warning("créditos hipotecarios no disponibles cliente_id=%s", cliente_id)
            if isinstance(entidades, BaseException):
                raise entidades
            return CreditosHipotecarios(ESTADO_NO_DISPONIBLE, (), entidades)

        if isinstance(entidades, BaseException):
            # Con hipotecas a la vista la lista del mercado no hace falta; sin ellas sí.
            if reportados.estado != ESTADO_DISPONIBLE:
                raise entidades
            entidades = ()

        por_clave = {
            _clave(nombre): entidad
            for entidad in entidades
            for nombre in (entidad.nombre, *entidad.alias)
        }
        creditos = []
        for hipoteca in reportados.hipotecas:
            entidad = por_clave.get(_clave(hipoteca.entidad_acreedora))
            creditos.append(
                CreditoHipotecario(
                    entidad_id=entidad.id if entidad else None,
                    entidad_nombre=entidad.nombre if entidad else hipoteca.entidad_acreedora,
                    valor_credito=hipoteca.valor_credito,
                    saldo_insoluto=hipoteca.saldo_insoluto,
                    plazo_restante_meses=hipoteca.plazo_restante_meses,
                    cuota_mensual=hipoteca.cuota_mensual,
                )
            )
        return CreditosHipotecarios(
            reportados.estado, tuple(creditos), tuple(entidades), reportados.fecha_consulta
        )

    async def _entidades_del_mercado(self, mercado: str) -> tuple[EntidadFinanciera, ...]:
        guardada = self._cache.get(mercado)
        ahora = self._reloj()
        if guardada is not None and ahora - guardada[0] < self._cache_segundos:
            return guardada[1]
        try:
            entidades = tuple(await self._entidades.listar_entidades(mercado))
        except BffError:
            if guardada is None:
                raise
            logger.warning("Productos no responde: se usa la lista guardada mercado=%s", mercado)
            return guardada[1]
        self._cache[mercado] = (ahora, entidades)
        return entidades
