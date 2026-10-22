"""Adaptador HTTP hacia svc-core (``ICustomerIdentity``)."""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from app.domain import (
    BffError,
    ClienteCore,
    EstadoEvidenciaTipo,
    ImagenMuyGrande,
    RecursoNoEncontrado,
    ResultadoEvidencia,
    SolicitudInvalida,
)
from app.logging_utils import sanear_para_log
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_mobile.adapters.core")

# Rutas de svc-core: todo vive bajo /v1 desde BITS-275.
CLIENTES = "/v1/clientes"


class CoreClientAdapter:
    def __init__(
        self,
        http: ResilientHttpClient,
        *,
        timeout_subida: httpx.Timeout | None = None,
    ) -> None:
        self._http = http
        # Subir una foto incluye el análisis de svc-core: no cabe en los 700 ms de las
        # demás llamadas.
        self._timeout_subida = timeout_subida or httpx.Timeout(5.0, pool=0.1)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        resp = await self._http.request("GET", CLIENTES, params={"identityRef": identity_ref})
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        data = resp.json()
        return ClienteCore(
            id=data["id"],
            email=data["email"],
            fecha_nacimiento=date.fromisoformat(data["fechaNacimiento"]),
        )

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        resp = await self._http.request(
            "POST",
            f"{CLIENTES}/{cliente_id}/evidencias-identidad",
            data={"tipo": tipo},
            files={"imagen": (nombre, imagen, content_type)},
            timeout=self._timeout_subida,
            reintentos=0,  # cada envío cuenta como un intento en svc-core
        )
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        if resp.status_code == 413:
            raise ImagenMuyGrande()
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        if resp.status_code not in (200, 201):
            raise BffError(f"svc-core respondió {resp.status_code}")
        datos = resp.json()
        logger.info(
            "evidencia procesada cliente_id=%s tipo=%s estado=%s",
            cliente_id,
            sanear_para_log(tipo),
            datos["estado"],
        )
        return ResultadoEvidencia(
            tipo=datos["tipo"],
            estado=datos["estado"],
            intentos_restantes=datos["intentosRestantes"],
            evidencia_id=datos.get("evidenciaId"),
            motivo=datos.get("motivo"),
        )

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        resp = await self._http.request("GET", f"{CLIENTES}/{cliente_id}/evidencias-identidad")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return [
            EstadoEvidenciaTipo(
                tipo=item["tipo"],
                estado=item["estado"],
                intentos_restantes=item["intentosRestantes"],
                evidencia_id=item.get("evidenciaId"),
                creada_en=_fecha(item.get("creadaEn")),
            )
            for item in resp.json()
        ]


def _fecha(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""


def _asegurar_ok(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise BffError(f"svc-core respondió {resp.status_code}")
