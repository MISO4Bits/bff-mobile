"""Modelos Pydantic de la API del BFF móvil. Reflejan ``openapi/openapi.yaml``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PATRON_VERSION = r"^V[1-9][0-9]*$"
PATRON_IDIOMA = r"^[a-z]{2}-[A-Z]{2}$"
TipoDocumentoLegal = Literal["terminos", "open-data", "open-finance", "poliza"]


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
    origen_datos: Literal["CONFIRMADO", "DECLARADO"]
    oferta: OfertaOut
    perfil_riesgo: PerfilRiesgoOut | None = None
    vigencia_cotizacion: VigenciaOut
    creada_en: datetime


TipoEvidencia = Literal["cedula_frente", "cedula_reverso", "selfie"]
EstadoVeredicto = Literal["ACEPTADA", "REINTENTAR", "REVISION_MANUAL"]
MotivoRechazo = Literal[
    "BORROSA",
    "OSCURA",
    "REFLEJO",
    "RECORTADA",
    "NO_ES_CEDULA",
    "SIN_ROSTRO",
    "VARIOS_ROSTROS",
    "POSIBLE_SUPLANTACION",
    "NO_COINCIDE_CON_CEDULA",
]


class EvidenciaSubidaOut(_Model):
    evidencia_id: str | None = None
    tipo: TipoEvidencia
    estado: EstadoVeredicto
    motivo: MotivoRechazo | None = None
    intentos_restantes: int


class EstadoEvidenciaOut(_Model):
    tipo: TipoEvidencia
    estado: Literal["ACEPTADA", "REVISION_MANUAL", "PENDIENTE"]
    evidencia_id: str | None = None
    creada_en: datetime | None = None
    intentos_restantes: int


# --- pólizas (BITS-101) ---

CodigoCobertura = Literal["VIDA", "HOGAR"]
CodigoPendiente = Literal[
    "CORREO_SIN_CONFIRMAR",
    "EVIDENCIA_FALTANTE",
    "EVIDENCIA_EN_REVISION",
    "CREDITO_NO_VERIFICADO",
]
EstadoPoliza = Literal["PENDIENTE", "EMITIDA", "RECHAZADA", "VENCIDA"]


class DocumentoAceptadoIn(_Model):
    tipo: Literal["poliza"]
    version: str = Field(pattern=PATRON_VERSION, max_length=20)


class SolicitarPolizaRequest(_Model):
    coberturas: list[CodigoCobertura] = Field(min_length=1, max_length=2)
    cotizacion_creada_en: AwareDatetime
    documentos_aceptados: list[DocumentoAceptadoIn] = Field(min_length=1, max_length=5)


class _Respuesta(BaseModel):
    """Lo que entrega svc-core: si agrega un campo, el BFF no se cae por no conocerlo."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class CoberturaContratadaOut(_Respuesta):
    codigo: CodigoCobertura
    obligatoria: bool
    suma_asegurada: float
    prima_neta: float
    iva: float
    prima_total: float


class DatosCreditoPolizaOut(_Respuesta):
    valor_credito: float
    plazo_restante_meses: int
    entidad_acreedora: str
    saldo_insoluto: float


class VigenciaPolizaOut(_Respuesta):
    desde: datetime
    hasta: datetime


class DetallePendienteOut(_Respuesta):
    evidencias: list[TipoEvidencia]


class PendienteOut(_Respuesta):
    codigo: CodigoPendiente
    tipo: Literal["USUARIO", "AGENTE"]
    mensaje: str
    detalle: DetallePendienteOut | None = None


class EstadoDocumentoOut(_Respuesta):
    estado: Literal["NO_DISPONIBLE", "DISPONIBLE"]


class DocumentoAceptadoOut(_Respuesta):
    tipo: str
    version: str
    aceptado_en: datetime


class MotivoRechazoOut(_Respuesta):
    codigo: CodigoPendiente
    mensaje: str


class RechazoOut(_Respuesta):
    motivos: list[MotivoRechazoOut]
    rechazada_en: datetime


class PolizaOut(_Respuesta):
    poliza_id: str
    numero: str | None = None
    estado: EstadoPoliza
    producto: Literal["VIDA_HIPOTECARIO"]
    creada_en: datetime
    emitida_en: datetime | None = None
    solicitud_vigente_hasta: datetime | None = None
    vigencia: VigenciaPolizaOut | None = None
    coberturas: list[CoberturaContratadaOut]
    prima_total_mensual: float
    moneda: Literal["COP"]
    datos_credito: DatosCreditoPolizaOut
    origen_datos: Literal["CONFIRMADO", "DECLARADO"]
    cotizacion_creada_en: datetime
    pendientes: list[PendienteOut]
    documento: EstadoDocumentoOut
    documentos_aceptados: list[DocumentoAceptadoOut] = Field(default_factory=list)
    rechazo: RechazoOut | None = None


class DocumentoPolizaOut(_Respuesta):
    estado: Literal["GENERANDO", "LISTO"]
    url: str | None = None
    expira_en: datetime | None = None


class DocumentoLegalOut(_Model):
    tipo: TipoDocumentoLegal
    version: str
    titulo: str
    subtitulo: str | None = None
    base_legal: str
    contenido: str
    nota_pie: str | None = None


# --- dispositivos para avisos push (BITS-229) ---


class RegistrarDispositivoRequest(_Model):
    token: str = Field(min_length=8, max_length=4096, pattern=r"^\S+$")
    plataforma: Literal["android"]
    version_app: str = Field(min_length=1, max_length=32)


class DispositivoOut(_Model):
    dispositivo_id: str
