"""API de sesión: modo fake (standalone) y modo http (Identity Platform y svc-core mockeados)."""

from __future__ import annotations

import httpx
import jwt
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings
from tests.conftest import CREDENCIALES_DEMO

SECRETO = "test-secret-de-al-menos-32-bytes!!"
IDP = "http://idp.test"
CORE = "http://core.test"


# --- modo fake ---


async def test_inicia_sesion_sin_token_y_devuelve_la_sesion(client):
    resp = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert set(cuerpo) == {"accessToken", "tokenType", "expiresIn", "refreshToken"}
    assert cuerpo["tokenType"] == "Bearer"
    assert cuerpo["expiresIn"] == 3600
    claims = jwt.decode(cuerpo["accessToken"], SECRETO, algorithms=["HS256"])
    assert claims["clienteId"] == "cliente-demo"
    assert claims["email"] == CREDENCIALES_DEMO["email"]


async def test_credenciales_incorrectas_son_401_problem_details(client):
    resp = await client.post("/v1/sesiones", json={**CREDENCIALES_DEMO, "password": "mala"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["detail"] == "credenciales inválidas"


async def test_el_cuerpo_invalido_es_400_con_detalle_por_campo(client):
    resp = await client.post("/v1/sesiones", json={"email": "no-es-correo", "password": "x"})
    assert resp.status_code == 400
    assert [e["campo"] for e in resp.json()["errores"]] == ["body.email"]

    faltante = await client.post("/v1/sesiones", json={})
    assert faltante.status_code == 400
    assert {e["campo"] for e in faltante.json()["errores"]} == {"body.email", "body.password"}


async def test_no_se_aceptan_campos_extra(client):
    resp = await client.post("/v1/sesiones", json={**CREDENCIALES_DEMO, "otro": 1})
    assert resp.status_code == 400


async def test_refresco_emite_una_sesion_nueva(client):
    inicial = (await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)).json()
    resp = await client.post(
        "/v1/sesiones/refresco", json={"refreshToken": inicial["refreshToken"]}
    )
    assert resp.status_code == 200
    claims = jwt.decode(resp.json()["accessToken"], SECRETO, algorithms=["HS256"])
    assert claims["clienteId"] == "cliente-demo"


async def test_refresco_rechaza_un_access_token(client):
    inicial = (await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)).json()
    resp = await client.post("/v1/sesiones/refresco", json={"refreshToken": inicial["accessToken"]})
    assert resp.status_code == 401


@pytest.mark.parametrize("token", ["basura", ""])
async def test_refresco_rechaza_un_token_invalido(client, token):
    resp = await client.post("/v1/sesiones/refresco", json={"refreshToken": token})
    assert resp.status_code == 401


async def test_refresco_sin_cuerpo_es_400(client):
    assert (await client.post("/v1/sesiones/refresco", json={})).status_code == 400


async def test_un_refresh_de_otro_canal_no_vale(client):
    ajeno = jwt.encode(
        {"sub": "s", "clienteId": "c", "email": "a@b.com", "typ": "refresh", "exp": 9999999999},
        "secreto-del-bff-web-de-al-menos-32-bytes",
        algorithm="HS256",
    )
    resp = await client.post("/v1/sesiones/refresco", json={"refreshToken": ajeno})
    assert resp.status_code == 401


async def test_health_y_contrato(client):
    assert (await client.get("/health")).json() == {"status": "ok", "service": "bff-mobile"}
    assert (await client.get("/openapi.yaml")).status_code == 200


# --- modo http ---


@pytest_asyncio.fixture
async def http_client():
    settings = Settings(
        adapters="http",
        identity_base_url=IDP,
        core_base_url=CORE,
        session_secret=SECRETO,
        http_retries=1,
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@respx.mock
async def test_el_inicio_de_sesion_atraviesa_identity_y_core(http_client):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9"})
    )
    respx.get(f"{CORE}/clientes").mock(
        return_value=httpx.Response(200, json={"id": "c-9", "email": "ana@example.com"})
    )
    resp = await http_client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    assert resp.status_code == 200
    claims = jwt.decode(resp.json()["accessToken"], SECRETO, algorithms=["HS256"])
    assert (claims["sub"], claims["clienteId"]) == ("sub-9", "c-9")


@respx.mock
async def test_identidad_sin_cliente_en_core_es_404(http_client):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9"})
    )
    respx.get(f"{CORE}/clientes").mock(return_value=httpx.Response(404))
    resp = await http_client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    assert resp.status_code == 404


@respx.mock
async def test_credenciales_rechazadas_por_identity_son_401(http_client):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(400, json={"error": {"message": "INVALID_PASSWORD"}})
    )
    assert (await http_client.post("/v1/sesiones", json=CREDENCIALES_DEMO)).status_code == 401


@respx.mock
async def test_si_core_no_responde_el_bff_devuelve_503(http_client):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9"})
    )
    respx.get(f"{CORE}/clientes").mock(return_value=httpx.Response(503))
    resp = await http_client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
