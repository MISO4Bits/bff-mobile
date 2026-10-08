from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.schemas import CredencialesRequest, RefrescoRequest, SesionOut
from app.services import SesionService

logger = logging.getLogger("bff_mobile.api")
router = APIRouter(prefix="/v1")


def get_service(request: Request) -> SesionService:
    return request.app.state.service


ServiceDep = Annotated[SesionService, Depends(get_service)]


@router.post("/sesiones", response_model=SesionOut, tags=["Sesión"])
async def iniciar_sesion(payload: CredencialesRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones: solicitud recibida")
    sesion = await service.iniciar_sesion(payload.email, payload.password)
    return SesionOut.model_validate(sesion)


@router.post("/sesiones/refresco", response_model=SesionOut, tags=["Sesión"])
async def refrescar_sesion(payload: RefrescoRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones/refresco: solicitud recibida")
    return SesionOut.model_validate(service.refrescar(payload.refresh_token))
