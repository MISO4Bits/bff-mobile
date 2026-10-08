"""Adaptador HTTP hacia svc-core (``ICustomerIdentity``)."""

from __future__ import annotations

import logging

import httpx

from app.domain import BffError, ClienteCore, RecursoNoEncontrado
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_mobile.adapters.core")


class CoreClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        resp = await self._http.request("GET", "/clientes", params={"identityRef": identity_ref})
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        data = resp.json()
        return ClienteCore(id=data["id"], email=data["email"])


def _asegurar_ok(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise BffError(f"svc-core respondió {resp.status_code}")
