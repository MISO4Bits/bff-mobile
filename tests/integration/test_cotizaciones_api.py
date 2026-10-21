"""Cotización activa del cliente móvil: API completa con adaptadores fake."""

from __future__ import annotations

import time

import jwt
import pytest_asyncio

from tests.conftest import CREDENCIALES_DEMO

SOLICITUD = {
    "datosCredito": {
        "valorCredito": 120000000,
        "plazoRestanteMeses": 120,
        "entidadAcreedora": "Banco Solventa",
        "saldoInsoluto": 100000000,
    },
}


@pytest_asyncio.fixture
async def autenticado(client):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


async def test_crear_cotizacion_devuelve_201_sin_id(client, autenticado):
    resp = await client.post("/v1/cotizaciones", headers=autenticado, json=SOLICITUD)

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert "id" not in cuerpo
    assert cuerpo["estado"] == "VIGENTE"
    assert cuerpo["origenDatos"] == "DECLARADO"
    assert cuerpo["datosCredito"]["entidadAcreedora"] == "Banco Solventa"
    assert cuerpo["oferta"]["primaMensual"] > 0
    vida, hogar = cuerpo["oferta"]["desglose"]["coberturas"]
    assert (vida["codigo"], vida["iva"]) == ("VIDA", 0)
    assert vida["primaTotal"] == cuerpo["oferta"]["primaMensual"]
    assert (hogar["codigo"], hogar["obligatoria"]) == ("HOGAR", False)
    assert hogar["primaTotal"] == hogar["primaNeta"] + hogar["iva"]


async def test_consultar_la_activa_devuelve_la_que_se_creo(client, autenticado):
    creada = (await client.post("/v1/cotizaciones", headers=autenticado, json=SOLICITUD)).json()

    activa = await client.get("/v1/cotizaciones/actual", headers=autenticado)

    assert activa.status_code == 200
    assert activa.json() == creada


async def test_una_cotizacion_nueva_reemplaza_a_la_activa(client, autenticado):
    await client.post("/v1/cotizaciones", headers=autenticado, json=SOLICITUD)
    otra = {"datosCredito": {**SOLICITUD["datosCredito"], "entidadAcreedora": "Otro Banco"}}

    await client.post("/v1/cotizaciones", headers=autenticado, json=otra)

    activa = (await client.get("/v1/cotizaciones/actual", headers=autenticado)).json()
    assert activa["datosCredito"]["entidadAcreedora"] == "Otro Banco"


async def test_sin_cotizacion_activa_devuelve_404(client, autenticado):
    resp = await client.get("/v1/cotizaciones/actual", headers=autenticado)

    assert resp.status_code == 404


async def test_declinar_elimina_la_cotizacion_activa(client, autenticado):
    await client.post("/v1/cotizaciones", headers=autenticado, json=SOLICITUD)

    resp = await client.delete("/v1/cotizaciones/actual", headers=autenticado)

    assert resp.status_code == 204
    assert resp.content == b""
    assert (await client.get("/v1/cotizaciones/actual", headers=autenticado)).status_code == 404


async def test_declinar_sin_cotizacion_devuelve_404(client, autenticado):
    resp = await client.delete("/v1/cotizaciones/actual", headers=autenticado)

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_la_solicitud_no_acepta_edad_ni_cuestionario(client, autenticado):
    for extra in ({"edad": 35}, {"cuestionarioHabitos": {"consumeTabaco": False}}):
        resp = await client.post(
            "/v1/cotizaciones", headers=autenticado, json={**SOLICITUD, **extra}
        )
        assert resp.status_code == 400


async def test_sin_token_devuelve_401(client):
    assert (await client.post("/v1/cotizaciones", json=SOLICITUD)).status_code == 401
    assert (await client.get("/v1/cotizaciones/actual")).status_code == 401
    assert (await client.delete("/v1/cotizaciones/actual")).status_code == 401


async def test_una_sesion_sin_fecha_de_nacimiento_pide_iniciar_sesion_de_nuevo(client, settings):
    token = jwt.encode(
        {
            "sub": "s",
            "clienteId": "cli-1",
            "email": "a@b.com",
            "typ": "access",
            "exp": int(time.time()) + 60,
        },
        settings.session_secret,
        algorithm="HS256",
    )

    resp = await client.post(
        "/v1/cotizaciones", headers={"Authorization": f"Bearer {token}"}, json=SOLICITUD
    )

    assert resp.status_code == 401
