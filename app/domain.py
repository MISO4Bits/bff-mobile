"""Modelo de dominio del BFF móvil: DTOs de orquestación y errores de aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Sesion:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


@dataclass(frozen=True)
class ClienteCore:
    id: str
    email: str


@dataclass(frozen=True)
class Claims:
    sub: str
    cliente_id: str
    email: str


# --- créditos hipotecarios (lo que Perfilamiento guardó de Open Finance) ---

ESTADO_DISPONIBLE = "DISPONIBLE"
ESTADO_SIN_HIPOTECAS = "SIN_HIPOTECAS"
ESTADO_SIN_CONSENTIMIENTO = "SIN_CONSENTIMIENTO"
ESTADO_NO_DISPONIBLE = "NO_DISPONIBLE"


@dataclass(frozen=True)
class EntidadFinanciera:
    """Banco o entidad del mercado. ``alias`` son los nombres con los que las
    fuentes (Open Finance) la reportan."""

    id: str
    nombre: str
    alias: tuple[str, ...] = ()


@dataclass(frozen=True)
class HipotecaReportada:
    """Hipoteca abierta como la reporta la fuente (nombre de la entidad sin normalizar)."""

    entidad_acreedora: str
    valor_credito: Decimal
    saldo_insoluto: Decimal
    plazo_restante_meses: int
    cuota_mensual: Decimal


@dataclass(frozen=True)
class CreditosReportados:
    estado: str
    hipotecas: tuple[HipotecaReportada, ...] = ()
    fecha_consulta: datetime | None = None


@dataclass(frozen=True)
class CreditoHipotecario:
    """Hipoteca lista para precargar el formulario. ``entidad_id`` es la entidad del
    mercado a la que corresponde, o ``None`` si el banco no está en la lista."""

    entidad_id: str | None
    entidad_nombre: str
    valor_credito: Decimal
    saldo_insoluto: Decimal
    plazo_restante_meses: int
    cuota_mensual: Decimal


@dataclass(frozen=True)
class CreditosHipotecarios:
    estado: str
    creditos: tuple[CreditoHipotecario, ...]
    entidades: tuple[EntidadFinanciera, ...]
    fecha_consulta: datetime | None = None


# --- errores de aplicación (se traducen a RFC 9457 en la capa API) ---


class BffError(Exception):
    status = 500
    title = "Error interno"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail


class SolicitudInvalida(BffError):
    status = 400
    title = "Solicitud inválida"


class NoAutorizado(BffError):
    status = 401
    title = "No autorizado"


class RecursoNoEncontrado(BffError):
    status = 404
    title = "Recurso no encontrado"


class DependenciaNoDisponible(BffError):
    status = 503
    title = "Dependencia no disponible"
