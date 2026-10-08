"""Modelo de dominio del BFF móvil: DTOs de orquestación y errores de aplicación."""

from __future__ import annotations

from dataclasses import dataclass


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
