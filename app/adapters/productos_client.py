"""Adaptador HTTP hacia svc-productos (``EntidadesFinancierasPort``)."""

from __future__ import annotations

import httpx

from app.domain import BffError, EntidadFinanciera, SolicitudInvalida
from app.resilience import ResilientHttpClient


class ProductosClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]:
        resp = await self._http.request(
            "GET", "/entidades-financieras", params={"mercado": mercado}
        )
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        if resp.status_code >= 400:
            raise BffError(f"svc-productos respondió {resp.status_code}")
        return [
            EntidadFinanciera(
                id=item["id"], nombre=item["nombre"], alias=tuple(item.get("alias", ()))
            )
            for item in resp.json()
        ]


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""
