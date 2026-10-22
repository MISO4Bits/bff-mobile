from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.api.schemas import (
    CotizacionOut,
    CotizacionRequest,
    CredencialesRequest,
    CreditosHipotecariosOut,
    EstadoEvidenciaOut,
    EvidenciaSubidaOut,
    RefrescoRequest,
    SesionOut,
    TipoEvidencia,
)
from app.domain import Claims, CotizacionInput, DatosCreditoInput, NoAutorizado
from app.services import (
    CotizacionesService,
    CreditosHipotecariosService,
    EvidenciasIdentidadService,
    SesionService,
)

logger = logging.getLogger("bff_mobile.api")
router = APIRouter(prefix="/v1")


def get_service(request: Request) -> SesionService:
    return request.app.state.service


ServiceDep = Annotated[SesionService, Depends(get_service)]


def get_creditos(request: Request) -> CreditosHipotecariosService:
    return request.app.state.creditos


CreditosDep = Annotated[CreditosHipotecariosService, Depends(get_creditos)]


def get_cotizaciones(request: Request) -> CotizacionesService:
    return request.app.state.cotizaciones


CotizacionesDep = Annotated[CotizacionesService, Depends(get_cotizaciones)]


def get_evidencias(request: Request) -> EvidenciasIdentidadService:
    return request.app.state.evidencias


EvidenciasDep = Annotated[EvidenciasIdentidadService, Depends(get_evidencias)]


async def claims_actuales(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Claims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NoAutorizado("falta el encabezado Authorization")
    token = authorization.split(" ", 1)[1]
    return request.app.state.sessions.verificar(token)


ClaimsDep = Annotated[Claims, Depends(claims_actuales)]


@router.post("/sesiones", response_model=SesionOut, tags=["Sesión"])
async def iniciar_sesion(payload: CredencialesRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones: solicitud recibida")
    sesion = await service.iniciar_sesion(payload.email, payload.password)
    return SesionOut.model_validate(sesion)


@router.post("/sesiones/refresco", response_model=SesionOut, tags=["Sesión"])
async def refrescar_sesion(payload: RefrescoRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones/refresco: solicitud recibida")
    return SesionOut.model_validate(service.refrescar(payload.refresh_token))


@router.get(
    "/creditos-hipotecarios",
    response_model=CreditosHipotecariosOut,
    response_model_exclude_none=True,
    tags=["Cotización"],
)
async def obtener_creditos_hipotecarios(
    claims: ClaimsDep,
    service: CreditosDep,
    response: Response,
    mercado: Annotated[Literal["CO"], Query()] = "CO",
) -> CreditosHipotecariosOut:
    logger.info(
        "GET /v1/creditos-hipotecarios: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    creditos = await service.obtener(claims.cliente_id, mercado)
    response.headers["Cache-Control"] = "no-store"  # datos financieros del cliente
    return CreditosHipotecariosOut.model_validate(creditos)


def _a_dominio_cotizacion(payload: CotizacionRequest) -> CotizacionInput:
    dc = payload.datos_credito
    return CotizacionInput(
        datos_credito=DatosCreditoInput(
            valor_credito=dc.valor_credito,
            plazo_restante_meses=dc.plazo_restante_meses,
            entidad_acreedora=dc.entidad_acreedora,
            saldo_insoluto=dc.saldo_insoluto,
        ),
    )


@router.post(
    "/cotizaciones",
    response_model=CotizacionOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    tags=["Cotización"],
)
async def crear_cotizacion(
    payload: CotizacionRequest, claims: ClaimsDep, service: CotizacionesDep
) -> CotizacionOut:
    logger.info("POST /v1/cotizaciones: solicitud recibida cliente_id=%s", claims.cliente_id)
    cotizacion = await service.crear(
        claims.cliente_id, claims.fecha_nacimiento, _a_dominio_cotizacion(payload)
    )
    return CotizacionOut.model_validate(cotizacion)


@router.get(
    "/cotizaciones/actual",
    response_model=CotizacionOut,
    response_model_exclude_none=True,
    tags=["Cotización"],
)
async def obtener_cotizacion_activa(claims: ClaimsDep, service: CotizacionesDep) -> CotizacionOut:
    logger.info("GET /v1/cotizaciones/actual: solicitud recibida cliente_id=%s", claims.cliente_id)
    cotizacion = await service.obtener_activa(claims.cliente_id)
    return CotizacionOut.model_validate(cotizacion)


@router.delete(
    "/cotizaciones/actual",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Cotización"],
)
async def eliminar_cotizacion_activa(claims: ClaimsDep, service: CotizacionesDep) -> Response:
    logger.info(
        "DELETE /v1/cotizaciones/actual: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    await service.eliminar_activa(claims.cliente_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/cuenta/evidencias-identidad",
    response_model=EvidenciaSubidaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Evidencias de identidad"],
)
async def subir_mi_evidencia(
    tipo: Annotated[TipoEvidencia, Form()],
    imagen: Annotated[UploadFile, File()],
    claims: ClaimsDep,
    service: EvidenciasDep,
    response: Response,
) -> EvidenciaSubidaOut:
    logger.info(
        "POST /v1/cuenta/evidencias-identidad: solicitud recibida cliente_id=%s tipo=%s",
        claims.cliente_id,
        tipo,
    )
    # Se lee un byte de más para detectar el exceso sin cargar un archivo sin límite.
    datos = await imagen.read(service.max_bytes + 1)
    resultado = await service.subir(
        claims.cliente_id,
        tipo,
        datos,
        imagen.filename or "imagen",
        imagen.content_type or "application/octet-stream",
    )
    if resultado.estado == "REINTENTAR":
        response.status_code = status.HTTP_200_OK  # veredicto normal: no se creó nada
    response.headers["Cache-Control"] = "no-store"
    return EvidenciaSubidaOut.model_validate(resultado)


@router.get(
    "/cuenta/evidencias-identidad",
    response_model=list[EstadoEvidenciaOut],
    tags=["Evidencias de identidad"],
)
async def estado_mis_evidencias(
    claims: ClaimsDep, service: EvidenciasDep, response: Response
) -> list[EstadoEvidenciaOut]:
    logger.info(
        "GET /v1/cuenta/evidencias-identidad: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    items = await service.estado(claims.cliente_id)
    response.headers["Cache-Control"] = "no-store"
    return [EstadoEvidenciaOut.model_validate(i) for i in items]
