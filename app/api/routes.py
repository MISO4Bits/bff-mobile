from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.api.schemas import (
    PATRON_IDIOMA,
    PATRON_VERSION,
    AsistenciaOut,
    CatalogoSiniestrosOut,
    CotizacionOut,
    CotizacionRequest,
    CredencialesRequest,
    CreditosHipotecariosOut,
    DispositivoOut,
    DocumentoLegalOut,
    DocumentoPolizaOut,
    EntregarEvidenciasDatos,
    EstadoEvidenciaOut,
    EstadoPoliza,
    EvidenciaSubidaOut,
    PedirAyudaRequest,
    PolizaOut,
    RefrescoRequest,
    RegistrarDispositivoRequest,
    ReportarSiniestroDatos,
    SesionOut,
    SiniestroOut,
    SiniestroReportadoOut,
    SiniestroResumenOut,
    SolicitarPolizaRequest,
    TipoDocumentoLegal,
    TipoEvidencia,
)
from app.domain import (
    Claims,
    CotizacionInput,
    DatosCreditoInput,
    ImagenMuyGrande,
    NoAutorizado,
    SolicitudInvalida,
)
from app.logging_utils import sanear_para_log
from app.services import (
    CatalogoSiniestrosService,
    CotizacionesService,
    CreditosHipotecariosService,
    DispositivosService,
    DocumentosLegalesService,
    EvidenciasIdentidadService,
    PolizasService,
    SesionService,
    SiniestrosService,
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


def get_documentos_legales(request: Request) -> DocumentosLegalesService:
    return request.app.state.documentos_legales


DocumentosLegalesDep = Annotated[DocumentosLegalesService, Depends(get_documentos_legales)]

MercadoQuery = Annotated[Literal["CO"], Query()]
IdiomaQuery = Annotated[str, Query(pattern=PATRON_IDIOMA)]
CACHE_VERSION_INMUTABLE = "public, max-age=31536000, immutable"


def get_polizas(request: Request) -> PolizasService:
    return request.app.state.polizas


PolizasDep = Annotated[PolizasService, Depends(get_polizas)]


def get_dispositivos(request: Request) -> DispositivosService:
    return request.app.state.dispositivos


DispositivosDep = Annotated[DispositivosService, Depends(get_dispositivos)]


def get_siniestros(request: Request) -> SiniestrosService:
    return request.app.state.siniestros


SiniestrosDep = Annotated[SiniestrosService, Depends(get_siniestros)]


def get_catalogo_siniestros(request: Request) -> CatalogoSiniestrosService:
    return request.app.state.catalogo_siniestros


CatalogoSiniestrosDep = Annotated[CatalogoSiniestrosService, Depends(get_catalogo_siniestros)]


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


REINTENTO_DOCUMENTO_S = 2


@router.post(
    "/cuenta/polizas",
    response_model=PolizaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Pólizas"],
)
async def solicitar_mi_poliza(
    payload: SolicitarPolizaRequest, claims: ClaimsDep, service: PolizasDep, response: Response
) -> PolizaOut:
    logger.info("POST /v1/cuenta/polizas: solicitud recibida cliente_id=%s", claims.cliente_id)
    poliza, creada = await service.solicitar(
        claims.cliente_id, payload.model_dump(by_alias=True, mode="json")
    )
    if not creada:
        response.status_code = status.HTTP_200_OK
    response.headers["Cache-Control"] = "no-store"
    return PolizaOut.model_validate(poliza)


@router.get("/cuenta/polizas", response_model=list[PolizaOut], tags=["Pólizas"])
async def listar_mis_polizas(
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
    estado: Annotated[EstadoPoliza | None, Query()] = None,
) -> list[PolizaOut]:
    logger.info("GET /v1/cuenta/polizas: solicitud recibida cliente_id=%s", claims.cliente_id)
    response.headers["Cache-Control"] = "no-store"
    return [PolizaOut.model_validate(p) for p in await service.listar(claims.cliente_id, estado)]


@router.get("/cuenta/polizas/{poliza_id}", response_model=PolizaOut, tags=["Pólizas"])
async def obtener_mi_poliza(
    poliza_id: Annotated[str, Path(min_length=1, max_length=64)],
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
) -> PolizaOut:
    logger.info(
        "GET /v1/cuenta/polizas/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(poliza_id),
        claims.cliente_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return PolizaOut.model_validate(await service.obtener(claims.cliente_id, poliza_id))


@router.get(
    "/cuenta/polizas/{poliza_id}/documento",
    response_model=DocumentoPolizaOut,
    tags=["Pólizas"],
)
async def documento_de_mi_poliza(
    poliza_id: Annotated[str, Path(min_length=1, max_length=64)],
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
) -> DocumentoPolizaOut:
    logger.info(
        "GET /v1/cuenta/polizas/%s/documento: solicitud recibida cliente_id=%s",
        sanear_para_log(poliza_id),
        claims.cliente_id,
    )
    cuerpo, listo = await service.documento(claims.cliente_id, poliza_id)
    if not listo:
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Retry-After"] = str(REINTENTO_DOCUMENTO_S)
    response.headers["Cache-Control"] = "no-store"
    return DocumentoPolizaOut.model_validate(cuerpo)


@router.put("/cuenta/dispositivos", response_model=DispositivoOut, tags=["Avisos"])
async def registrar_mi_dispositivo(
    payload: RegistrarDispositivoRequest,
    claims: ClaimsDep,
    service: DispositivosDep,
    response: Response,
) -> DispositivoOut:
    # Nunca se escribe el token: identifica al teléfono.
    logger.info("PUT /v1/cuenta/dispositivos: solicitud recibida cliente_id=%s", claims.cliente_id)
    dispositivo = await service.registrar(
        claims.cliente_id, payload.model_dump(by_alias=True, mode="json")
    )
    response.headers["Cache-Control"] = "no-store"
    return DispositivoOut.model_validate(dispositivo)


@router.delete(
    "/cuenta/dispositivos/{dispositivo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Avisos"],
)
async def quitar_mi_dispositivo(
    dispositivo_id: Annotated[str, Path(min_length=1, max_length=64)],
    claims: ClaimsDep,
    service: DispositivosDep,
) -> Response:
    logger.info(
        "DELETE /v1/cuenta/dispositivos/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(dispositivo_id),
        claims.cliente_id,
    )
    await service.quitar(claims.cliente_id, dispositivo_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/documentos-legales",
    response_model=list[DocumentoLegalOut],
    response_model_exclude_none=True,
    tags=["Documentos legales"],
)
async def listar_documentos_legales(
    service: DocumentosLegalesDep,
    mercado: MercadoQuery,
    idioma: IdiomaQuery = "es-CO",
    tipo: Annotated[TipoDocumentoLegal | None, Query()] = None,
) -> list[DocumentoLegalOut]:
    logger.info("GET /v1/documentos-legales: solicitud recibida mercado=%s tipo=%s", mercado, tipo)
    documentos = await service.listar_vigentes(mercado, idioma, tipo)
    return [DocumentoLegalOut.model_validate(d) for d in documentos]


@router.get(
    "/documentos-legales/{tipo}/versiones/{version}",
    response_model=DocumentoLegalOut,
    response_model_exclude_none=True,
    tags=["Documentos legales"],
)
async def obtener_version_documento_legal(
    tipo: TipoDocumentoLegal,
    version: Annotated[str, Path(pattern=PATRON_VERSION)],
    service: DocumentosLegalesDep,
    response: Response,
    mercado: MercadoQuery,
    idioma: IdiomaQuery = "es-CO",
) -> DocumentoLegalOut:
    logger.info(
        "GET /v1/documentos-legales/%s/versiones/%s: solicitud recibida mercado=%s",
        tipo,
        version,
        mercado,
    )
    documento = await service.obtener_version(tipo, version, mercado, idioma)
    response.headers["Cache-Control"] = CACHE_VERSION_INMUTABLE
    return DocumentoLegalOut.model_validate(documento)


def _leer_datos[T: BaseModel](modelo: type[T], crudo: str) -> T:
    """Valida la parte ``datos``. Los errores salen con el campo (``datos.tipo``, ...) y sin el
    valor recibido: ahí puede venir un número de cuenta."""
    try:
        return modelo.model_validate_json(crudo)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {"loc": ("datos", *e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ]
        ) from None


async def _leer_archivos(
    partes: list[UploadFile], esperados: int, service: SiniestrosService
) -> list[tuple[str, bytes, str]]:
    """Las imágenes del envío, en orden. Debe haber un dato por cada una (``datos.archivos``)."""
    if len(partes) != esperados:
        raise SolicitudInvalida(
            "Debe haber un dato por cada archivo, en el mismo orden",
            errores=[{"campo": "archivos", "mensaje": "no coincide con datos.archivos"}],
        )
    contenidos: list[tuple[str, bytes, str]] = []
    for parte in partes:
        # Se lee un byte de más para detectar el exceso sin cargar un archivo sin límite.
        contenido = await parte.read(service.max_bytes + 1)
        if len(contenido) > service.max_bytes:
            raise ImagenMuyGrande(f"La imagen supera el máximo de {service.max_bytes} bytes")
        contenidos.append(
            (
                parte.filename or "archivo",
                contenido,
                parte.content_type or "application/octet-stream",
            )
        )
    return contenidos


LlaveDeIdempotencia = Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)]


@router.post(
    "/cuenta/siniestros",
    response_model=SiniestroReportadoOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Siniestros"],
)
async def reportar_mi_siniestro(
    datos: Annotated[str, Form()],
    archivos: Annotated[list[UploadFile], File()],
    claims: ClaimsDep,
    service: SiniestrosDep,
    response: Response,
    idempotency_key: LlaveDeIdempotencia = None,
) -> SiniestroReportadoOut:
    reporte = _leer_datos(ReportarSiniestroDatos, datos)
    # Ni el destino del pago ni las imágenes se escriben en el log.
    logger.info(
        "POST /v1/cuenta/siniestros: solicitud recibida cliente_id=%s tipo=%s archivos=%d",
        claims.cliente_id,
        reporte.tipo,
        len(archivos),
    )
    contenidos = await _leer_archivos(archivos, len(reporte.archivos), service)
    siniestro = await service.reportar(
        claims.cliente_id,
        reporte.model_dump(by_alias=True, mode="json", exclude_none=True),
        contenidos,
        idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return SiniestroReportadoOut.model_validate(siniestro)


@router.get("/cuenta/siniestros", response_model=list[SiniestroResumenOut], tags=["Siniestros"])
async def listar_mis_siniestros(
    claims: ClaimsDep, service: SiniestrosDep, response: Response
) -> list[SiniestroResumenOut]:
    logger.info("GET /v1/cuenta/siniestros: solicitud recibida cliente_id=%s", claims.cliente_id)
    siniestros = await service.listar(claims.cliente_id)
    response.headers["Cache-Control"] = "no-store"
    return [SiniestroResumenOut.model_validate(s) for s in siniestros]


@router.get("/cuenta/siniestros/{siniestro_id}", response_model=SiniestroOut, tags=["Siniestros"])
async def obtener_mi_siniestro(
    siniestro_id: str, claims: ClaimsDep, service: SiniestrosDep, response: Response
) -> SiniestroOut:
    logger.info(
        "GET /v1/cuenta/siniestros/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(siniestro_id),
        claims.cliente_id,
    )
    siniestro = await service.obtener(claims.cliente_id, siniestro_id)
    response.headers["Cache-Control"] = "no-store"
    return SiniestroOut.model_validate(siniestro)


@router.post(
    "/cuenta/siniestros/{siniestro_id}/evidencias",
    response_model=SiniestroOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Siniestros"],
)
async def entregar_evidencias_de_mi_siniestro(
    siniestro_id: str,
    datos: Annotated[str, Form()],
    archivos: Annotated[list[UploadFile], File()],
    claims: ClaimsDep,
    service: SiniestrosDep,
    response: Response,
    idempotency_key: LlaveDeIdempotencia = None,
) -> SiniestroOut:
    entrega = _leer_datos(EntregarEvidenciasDatos, datos)
    logger.info(
        "POST /v1/cuenta/siniestros/%s/evidencias: solicitud recibida cliente_id=%s archivos=%d",
        sanear_para_log(siniestro_id),
        claims.cliente_id,
        len(archivos),
    )
    contenidos = await _leer_archivos(archivos, len(entrega.archivos), service)
    siniestro = await service.entregar_evidencias(
        claims.cliente_id,
        siniestro_id,
        entrega.model_dump(by_alias=True, mode="json", exclude_none=True),
        contenidos,
        idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return SiniestroOut.model_validate(siniestro)


@router.get(
    "/catalogos/siniestros",
    response_model=CatalogoSiniestrosOut,
    tags=["Siniestros"],
)
async def obtener_catalogo_de_siniestros(
    claims: ClaimsDep,
    service: CatalogoSiniestrosDep,
    response: Response,
    mercado: Annotated[Literal["CO"], Query()] = "CO",
) -> CatalogoSiniestrosOut:
    logger.info(
        "GET /v1/catalogos/siniestros: solicitud recibida cliente_id=%s mercado=%s",
        claims.cliente_id,
        mercado,
    )
    catalogo = await service.obtener(mercado)
    # Es el mismo para todos y casi no cambia, pero se pide con sesión: caché solo del dispositivo.
    response.headers["Cache-Control"] = "private, max-age=3600"
    return CatalogoSiniestrosOut.model_validate(catalogo)


@router.post(
    "/cuenta/siniestros/{siniestro_id}/asistencias",
    response_model=AsistenciaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Siniestros"],
)
async def pedir_ayuda_en_sitio(
    siniestro_id: str,
    payload: PedirAyudaRequest,
    claims: ClaimsDep,
    service: SiniestrosDep,
    response: Response,
    idempotency_key: LlaveDeIdempotencia = None,
) -> AsistenciaOut:
    # Ni la ubicación ni la dirección del cliente se escriben en el log.
    logger.info(
        "POST /v1/cuenta/siniestros/%s/asistencias: solicitud recibida cliente_id=%s "
        "tipo=%s origen=%s",
        sanear_para_log(siniestro_id),
        claims.cliente_id,
        payload.tipo,
        payload.origen,
    )
    asistencia = await service.pedir_ayuda(
        claims.cliente_id,
        siniestro_id,
        payload.model_dump(by_alias=True, mode="json", exclude_none=True),
        idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return AsistenciaOut.model_validate(asistencia)
