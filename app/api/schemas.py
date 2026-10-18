"""Modelos Pydantic de la API del BFF móvil. Reflejan ``openapi/openapi.yaml``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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


# --- cotización (la edad la deriva el BFF de la fecha de nacimiento de la sesión) ---


class DatosCreditoRequest(_Model):
    valor_credito: Decimal = Field(ge=10_000_000)
    plazo_restante_meses: int = Field(ge=1, le=480)
    entidad_acreedora: str = Field(min_length=2, max_length=80)
    saldo_insoluto: Decimal = Field(gt=0)


class CotizacionRequest(_Model):
    datos_credito: DatosCreditoRequest


class FactorRiesgoOut(_Model):
    descripcion: str
    efecto: Literal["POSITIVO", "NEGATIVO"]
    peso_relativo: float | None = None


class PerfilRiesgoOut(_Model):
    nivel_riesgo: Literal["BAJO", "MEDIO", "ALTO"]
    factores: list[FactorRiesgoOut]


class CoberturaPrecioOut(_Model):
    codigo: Literal["VIDA", "HOGAR"]
    obligatoria: bool
    suma_asegurada: float
    prima_neta: float
    iva: float
    prima_total: float


class DesgloseOut(_Model):
    coberturas: list[CoberturaPrecioOut]


class OfertaOut(_Model):
    prima_mensual: float
    prima_base_mensual: float
    suma_asegurada: float
    cobertura_meses: int
    moneda: Literal["COP"]
    personalizado: bool
    fuentes_no_disponibles: list[str] = Field(default_factory=list)
    desglose: DesgloseOut


class VigenciaOut(_Model):
    desde: datetime
    hasta: datetime


class DatosCreditoOut(_Model):
    valor_credito: float
    plazo_restante_meses: int
    entidad_acreedora: str
    saldo_insoluto: float


class CotizacionOut(_Model):
    estado: Literal["VIGENTE", "EXPIRADA"]
    producto: Literal["VIDA_HIPOTECARIO"]
    datos_credito: DatosCreditoOut
    origen_datos: Literal["DECLARADO"]
    oferta: OfertaOut
    perfil_riesgo: PerfilRiesgoOut | None = None
    vigencia_cotizacion: VigenciaOut
    creada_en: datetime
