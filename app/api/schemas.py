"""Modelos Pydantic de la API del BFF móvil. Reflejan ``openapi/openapi.yaml``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class _Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


class CredencialesRequest(_Model):
    email: str = Field(pattern=_EMAIL)
    password: str


class RefrescoRequest(_Model):
    refresh_token: str


class SesionOut(_Model):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str


# --- créditos hipotecarios (pantalla previa a la cotización) ---


class EntidadFinancieraOut(_Model):
    id: str
    nombre: str


class CreditoHipotecarioOut(_Model):
    entidad_id: str | None = None
    entidad_nombre: str
    valor_credito: float
    saldo_insoluto: float
    plazo_restante_meses: int
    cuota_mensual: float


class CreditosHipotecariosOut(_Model):
    estado: Literal["DISPONIBLE", "SIN_HIPOTECAS", "SIN_CONSENTIMIENTO", "NO_DISPONIBLE"]
    creditos: list[CreditoHipotecarioOut]
    entidades: list[EntidadFinancieraOut]
    origen: Literal["OPEN_FINANCE"] = "OPEN_FINANCE"
    fecha_consulta: datetime | None = None
