"""BITS-229: dispositivos para avisos push del cliente autenticado (BFF móvil)."""

from __future__ import annotations

import pytest_asyncio
from jsonschema import Draft202012Validator

from app.adapters.fakes import FakeDispositivos
from app.domain import DependenciaNoDisponible
from app.services import DispositivosService
from tests.conftest import CREDENCIALES_DEMO

RUTA = "/v1/cuenta/dispositivos"
CUERPO = {"token": "fcm-token-de-prueba", "plataforma": "android", "versionApp": "1.0.0"}


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return client, {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


def _con_dispositivos(app, dispositivos) -> FakeDispositivos:
    app.state.dispositivos = DispositivosService(dispositivos)
    return dispositivos


async def test_registrar_devuelve_el_id_y_cumple_el_contrato(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    dispositivos = _con_dispositivos(app, FakeDispositivos())

    respuesta = await client.put(RUTA, headers=headers, json=CUERPO)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "Dispositivo", respuesta.json())
    assert respuesta.headers["cache-control"] == "no-store"
    # El BFF pone el clienteId del token; el cuerpo viaja tal cual.
    assert dispositivos.llamadas == [("registrar", "cliente-demo", CUERPO)]


async def test_registrar_dos_veces_el_mismo_token_da_el_mismo_id(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_dispositivos(app, FakeDispositivos())

    primero = await client.put(RUTA, headers=headers, json=CUERPO)
    segundo = await client.put(RUTA, headers=headers, json=CUERPO)

    assert primero.json() == segundo.json()


async def test_el_token_no_se_escribe_en_los_logs(app, cliente_autenticado, caplog):
    client, headers = cliente_autenticado
    _con_dispositivos(app, FakeDispositivos())

    with caplog.at_level("INFO"):
        await client.put(RUTA, headers=headers, json=CUERPO)

    assert CUERPO["token"] not in caplog.text


async def test_quitar_es_204_con_el_cliente_del_token(app, cliente_autenticado):
    client, headers = cliente_autenticado
    dispositivos = _con_dispositivos(app, FakeDispositivos())

    respuesta = await client.delete(f"{RUTA}/d1", headers=headers)

    assert respuesta.status_code == 204
    assert respuesta.content == b""
    assert dispositivos.llamadas == [("quitar", "cliente-demo", "d1")]


async def test_sin_sesion_es_401(client):
    assert (await client.put(RUTA, json=CUERPO)).status_code == 401
    assert (await client.delete(f"{RUTA}/d1")).status_code == 401


async def test_un_cuerpo_invalido_es_400(app, cliente_autenticado):
    client, headers = cliente_autenticado
    dispositivos = _con_dispositivos(app, FakeDispositivos())

    for malo in (
        {**CUERPO, "plataforma": "ios"},
        {**CUERPO, "token": "con espacios"},
        {**CUERPO, "token": "corto"},
        {"token": CUERPO["token"]},
        {**CUERPO, "clienteId": "otro"},
    ):
        assert (await client.put(RUTA, headers=headers, json=malo)).status_code == 400

    assert dispositivos.llamadas == []


async def test_si_core_no_responde_es_503(app, cliente_autenticado):
    client, headers = cliente_autenticado

    class _Caido:
        async def registrar_dispositivo(self, cliente_id, cuerpo):
            raise DependenciaNoDisponible()

        async def quitar_dispositivo(self, cliente_id, dispositivo_id):
            raise DependenciaNoDisponible()

    _con_dispositivos(app, _Caido())

    assert (await client.put(RUTA, headers=headers, json=CUERPO)).status_code == 503
    assert (await client.delete(f"{RUTA}/d1", headers=headers)).status_code == 503
