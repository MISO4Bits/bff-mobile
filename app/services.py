"""Orquestación de la sesión del cliente móvil."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import date

from app.domain import (
    ESTADO_DISPONIBLE,
    ESTADO_NO_DISPONIBLE,
    BffError,
    Cotizacion,
    CotizacionInput,
    CreditoHipotecario,
    CreditosHipotecarios,
    DocumentoLegal,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    ImagenMuyGrande,
    NoAutorizado,
    ResultadoEvidencia,
    Sesion,
    SolicitudInvalida,
    calcular_edad,
    hoy_en_colombia,
)
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
        return self._sessions.emitir(
            sub=sub,
            cliente_id=cliente.id,
            email=cliente.email,
            fecha_nacimiento=cliente.fecha_nacimiento,
        )

    def refrescar(self, refresh_token: str) -> Sesion:
        return self._sessions.refrescar(refresh_token)


class CotizacionesService:
    """Cotización activa del cliente. La edad no la manda el dispositivo: se deriva de la
    fecha de nacimiento que viaja en la sesión."""

    def __init__(
        self, cotizacion: CotizacionPort, hoy: Callable[[], date] = hoy_en_colombia
    ) -> None:
        self._cotizacion = cotizacion
        self._hoy = hoy

    async def crear(
        self, cliente_id: str, fecha_nacimiento: date | None, entrada: CotizacionInput
    ) -> Cotizacion:
        """Cotiza y deja la cotización como la activa del cliente (reemplaza la anterior)."""
        if fecha_nacimiento is None:
            # Sesión emitida antes de que la fecha viajara en el token.
            raise NoAutorizado("la sesión está desactualizada: inicia sesión de nuevo")
        edad = calcular_edad(fecha_nacimiento, self._hoy())
        logger.info("crear_cotizacion: solicitando cotización cliente_id=%s", cliente_id)
        cotizacion = await self._cotizacion.crear_cotizacion(cliente_id, edad, entrada)
        logger.info("crear_cotizacion: cotización activa estado=%s", cotizacion.estado)
        return cotizacion

    async def obtener_activa(self, cliente_id: str) -> Cotizacion:
        logger.info("obtener_cotizacion_activa: consultando cliente_id=%s", cliente_id)
        return await self._cotizacion.obtener_cotizacion_activa(cliente_id)

    async def eliminar_activa(self, cliente_id: str) -> None:
        """El cliente declinó las condiciones: vuelve al estado de cero cotizaciones."""
        logger.info("eliminar_cotizacion_activa: eliminando cliente_id=%s", cliente_id)
        await self._cotizacion.eliminar_cotizacion_activa(cliente_id)


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


class EvidenciasIdentidadService:
    """Foto de la cédula y selfie. svc-core decide si una foto sirve; aquí solo se descartan
    los envíos que ni vale la pena reenviar."""

    def __init__(self, evidencias: EvidenciasIdentidadPort, *, max_bytes: int) -> None:
        self._evidencias = evidencias
        self.max_bytes = max_bytes

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        if len(imagen) > self.max_bytes:
            raise ImagenMuyGrande(f"La imagen supera el máximo de {self.max_bytes} bytes")
        if not imagen:
            raise SolicitudInvalida("La imagen está vacía")
        return await self._evidencias.subir(cliente_id, tipo, imagen, nombre, content_type)

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        return await self._evidencias.estado(cliente_id)


class PolizasService:
    """Solicitud y consulta de pólizas. svc-core decide qué falta y cuándo emitir; aquí solo se
    pasan las llamadas con el ``clienteId`` del token."""

    def __init__(self, polizas: PolizasPort) -> None:
        self._polizas = polizas

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        return await self._polizas.solicitar(cliente_id, cuerpo)

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]:
        return await self._polizas.listar(cliente_id, estado)

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        return await self._polizas.obtener(cliente_id, poliza_id)

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        return await self._polizas.documento(cliente_id, poliza_id)


class DispositivosService:
    """Dispositivos para avisos push. svc-core es el dueño del cliente y guarda los tokens; aquí
    solo se pasan las llamadas con el ``clienteId`` del token."""

    def __init__(self, dispositivos: DispositivosPort) -> None:
        self._dispositivos = dispositivos

    async def registrar(self, cliente_id: str, cuerpo: dict) -> dict:
        return await self._dispositivos.registrar_dispositivo(cliente_id, cuerpo)

    async def quitar(self, cliente_id: str, dispositivo_id: str) -> None:
        await self._dispositivos.quitar_dispositivo(cliente_id, dispositivo_id)


class DocumentosLegalesService:
    """Textos legales. El BFF los reenvía sin transformarlos: el versionado y el contenido son
    de Productos y Configuración de Mercado, y la aceptación la registra svc-core con lo que
    envía la aplicación."""

    def __init__(self, documentos: DocumentosLegalesPort) -> None:
        self._documentos = documentos

    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        return await self._documentos.listar_vigentes(mercado, idioma, tipo)

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        return await self._documentos.obtener_version(tipo, version, mercado, idioma)
