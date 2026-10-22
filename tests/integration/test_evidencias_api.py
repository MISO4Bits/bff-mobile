"""Evidencias de identidad del cliente móvil: API con adaptadores fake y un svc-core simulado."""

from __future__ import annotations

from datetime import date

import httpx
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.adapters.fakes import FakeEvidencias
from app.api.app import create_app
from app.config import Settings
from app.services import EvidenciasIdentidadService
from tests.conftest import CREDENCIALES_DEMO

RUTA = "/v1/cuenta/evidencias-identidad"
IMAGEN = b"\xff\xd8\xff" + b"x" * 200


@pytest_asyncio.fixture
async def autenticado(client):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _subir(client, headers, tipo="cedula_frente", imagen=IMAGEN):
    return client.post(
        RUTA,
        headers=headers,
        data={"tipo": tipo},
        files={"imagen": ("foto.jpg", imagen, "image/jpeg")},
    )


async def test_una_foto_aceptada_responde_201(client, autenticado):
    respuesta = await _subir(client, autenticado)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ACEPTADA"
    assert cuerpo["tipo"] == "cedula_frente"
    assert cuerpo["evidenciaId"]
    assert cuerpo["motivo"] is None
    assert cuerpo["intentosRestantes"] == 3
    assert "numeroDetectado" not in cuerpo
    assert respuesta.headers["cache-control"] == "no-store"


async def test_un_rechazo_responde_200_con_el_motivo(app, client, autenticado):
    app.state.evidencias = EvidenciasIdentidadService(
        FakeEvidencias(("REFLEJO",)), max_bytes=3_000_000
    )

    respuesta = await _subir(client, autenticado, "cedula_reverso")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "evidenciaId": None,
        "tipo": "cedula_reverso",
        "estado": "REINTENTAR",
        "motivo": "REFLEJO",
        "intentosRestantes": 2,
    }


async def test_el_estado_lista_los_tres_tipos_y_refleja_lo_subido(client, autenticado):
    antes = (await client.get(RUTA, headers=autenticado)).json()
    await _subir(client, autenticado, "cedula_frente")
    respuesta = await client.get(RUTA, headers=autenticado)
    despues = {e["tipo"]: e for e in respuesta.json()}

    assert [e["tipo"] for e in antes] == ["cedula_frente", "cedula_reverso", "selfie"]
    assert {e["estado"] for e in antes} == {"PENDIENTE"}
    assert despues["cedula_frente"]["estado"] == "ACEPTADA"
    assert despues["cedula_frente"]["creadaEn"]
    assert despues["cedula_reverso"]["estado"] == "PENDIENTE"
    assert respuesta.headers["cache-control"] == "no-store"


async def test_sin_token_responde_401(client):
    subida = await client.post(
        RUTA, data={"tipo": "selfie"}, files={"imagen": ("f.jpg", IMAGEN, "image/jpeg")}
    )
    estado = await client.get(RUTA)

    assert subida.status_code == estado.status_code == 401


async def test_un_tipo_fuera_de_la_lista_responde_400(client, autenticado):
    respuesta = await _subir(client, autenticado, "pasaporte")

    assert respuesta.status_code == 400
    assert any("tipo" in e["campo"] for e in respuesta.json()["errores"])


async def test_sin_tipo_o_sin_imagen_responde_400(client, autenticado):
    sin_tipo = await client.post(
        RUTA, headers=autenticado, files={"imagen": ("f.jpg", IMAGEN, "image/jpeg")}
    )
    sin_imagen = await client.post(RUTA, headers=autenticado, data={"tipo": "selfie"})

    assert sin_tipo.status_code == sin_imagen.status_code == 400


async def test_una_imagen_vacia_responde_400(client, autenticado):
    assert (await _subir(client, autenticado, imagen=b"")).status_code == 400


@pytest_asyncio.fixture
async def cliente_con_limite_bajo(settings):
    app = create_app(settings.model_copy(update={"evidencia_max_bytes": 100}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
        yield client, {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


async def test_una_imagen_sobre_el_limite_responde_413(cliente_con_limite_bajo):
    client, headers = cliente_con_limite_bajo

    respuesta = await _subir(
        client, headers, imagen=b"x" * 200
    )  # cabe en el multipart, no en el límite

    assert respuesta.status_code == 413
    assert respuesta.json()["title"] == "Imagen demasiado grande"


async def test_un_cuerpo_enorme_se_rechaza_sin_procesarlo(cliente_con_limite_bajo):
    client, headers = cliente_con_limite_bajo

    respuesta = await _subir(client, headers, imagen=b"x" * 200_000)

    assert respuesta.status_code == 413
    assert respuesta.headers["content-type"].startswith("application/problem+json")


# ---------- de punta a punta contra un svc-core simulado ----------

CORE = "http://core.test"


@pytest_asyncio.fixture
async def http_client_con_sesion():
    app = create_app(
        Settings(adapters="http", core_base_url=CORE, session_secret="t" * 32, http_retries=1)
    )
    sesion = app.state.sessions.emitir(
        sub="sub-1", cliente_id="c-7", email="ana@example.com", fecha_nacimiento=date(1991, 5, 20)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, {"Authorization": f"Bearer {sesion.access_token}"}


@respx.mock
async def test_la_subida_llega_a_core_con_el_cliente_del_token(http_client_con_sesion):
    client, headers = http_client_con_sesion
    en_core = respx.post(f"{CORE}/v1/clientes/c-7/evidencias-identidad").mock(
        return_value=httpx.Response(
            201,
            json={
                "evidenciaId": "e9",
                "tipo": "selfie",
                "estado": "REVISION_MANUAL",
                "motivo": None,
                "intentosRestantes": 3,
            },
        )
    )

    respuesta = await _subir(client, headers, "selfie")

    assert respuesta.status_code == 201
    assert respuesta.json()["estado"] == "REVISION_MANUAL"
    assert b'name="tipo"' in en_core.calls.last.request.content
    assert IMAGEN in en_core.calls.last.request.content


@respx.mock
async def test_si_core_no_responde_la_subida_falla_con_503_sin_reintentar(http_client_con_sesion):
    client, headers = http_client_con_sesion
    en_core = respx.post(f"{CORE}/v1/clientes/c-7/evidencias-identidad").mock(
        return_value=httpx.Response(503)
    )

    respuesta = await _subir(client, headers)

    assert respuesta.status_code == 503
    assert en_core.call_count == 1
