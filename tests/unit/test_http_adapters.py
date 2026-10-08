from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.adapters.factory import build_dependencias
from app.adapters.fakes import FakeCoreIdentity, FakeIdentityProvider
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.config import Settings
from app.domain import BffError, NoAutorizado, RecursoNoEncontrado
from app.ports import CoreIdentityPort, IdentityProviderPort
from app.resilience import ResilientHttpClient, build_breaker

IDP = "http://idp.local"
CORE = "http://core.local"


def _idp() -> IdentityPlatformAdapter:
    http = ResilientHttpClient(
        IDP, breaker=build_breaker("idp", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return IdentityPlatformAdapter(http, "k")


def _core() -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE, breaker=build_breaker("core", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return CoreClientAdapter(http)


# --- Identity Platform ---


@respx.mock
async def test_autenticar_devuelve_el_sub_y_envia_la_api_key():
    ruta = respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(200, json={"localId": "sub-1", "idToken": "t"})
    )
    adapter = _idp()
    try:
        assert await adapter.autenticar("a@b.com", "x" * 10) == "sub-1"
    finally:
        await adapter.aclose()
    assert ruta.calls.last.request.url.params["key"] == "k"


@respx.mock
async def test_autenticar_con_credenciales_malas_es_no_autorizado():
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(400, json={"error": {"message": "INVALID_PASSWORD"}})
    )
    adapter = _idp()
    try:
        with pytest.raises(NoAutorizado):
            await adapter.autenticar("a@b.com", "mala")
    finally:
        await adapter.aclose()


@respx.mock
@pytest.mark.parametrize("status", [403, 500])
async def test_autenticar_con_otro_error_del_proveedor_es_error_del_bff(status):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(status, json={"error": {"message": "X"}})
    )
    adapter = _idp()
    try:
        with pytest.raises(BffError):
            await adapter.autenticar("a@b.com", "x")
    finally:
        await adapter.aclose()


# --- svc-core ---


@respx.mock
async def test_buscar_cliente_por_identidad_envia_identity_ref_y_mapea():
    ruta = respx.get(f"{CORE}/clientes").mock(
        return_value=httpx.Response(
            200, json={"id": "cli-1", "email": "a@b.com", "primerNombre": "Ana"}
        )
    )
    adapter = _core()
    try:
        cliente = await adapter.buscar_cliente_por_identidad("sub-1")
    finally:
        await adapter.aclose()
    assert (cliente.id, cliente.email) == ("cli-1", "a@b.com")
    assert dict(ruta.calls.last.request.url.params) == {"identityRef": "sub-1"}


@respx.mock
async def test_cliente_inexistente_es_recurso_no_encontrado():
    respx.get(f"{CORE}/clientes").mock(return_value=httpx.Response(404))
    adapter = _core()
    try:
        with pytest.raises(RecursoNoEncontrado):
            await adapter.buscar_cliente_por_identidad("sub-x")
    finally:
        await adapter.aclose()


@respx.mock
async def test_error_de_core_es_error_del_bff():
    respx.get(f"{CORE}/clientes").mock(return_value=httpx.Response(500))
    adapter = _core()
    try:
        with pytest.raises(BffError):
            await adapter.buscar_cliente_por_identidad("sub-1")
    finally:
        await adapter.aclose()


# --- dobles y fábrica ---


def test_los_dobles_cumplen_los_puertos():
    assert isinstance(FakeIdentityProvider(), IdentityProviderPort)
    assert isinstance(FakeCoreIdentity(), CoreIdentityPort)


async def test_el_doble_de_core_sin_clientes_no_encuentra_a_nadie():
    with pytest.raises(RecursoNoEncontrado):
        await FakeCoreIdentity({}).buscar_cliente_por_identidad("sub-1")


async def test_la_fabrica_construye_los_adaptadores_segun_el_modo():
    fake = build_dependencias(Settings(adapters="fake"))
    assert type(fake.identity).__name__ == "FakeIdentityProvider"
    await fake.aclose()

    http = build_dependencias(Settings(adapters="http", identity_base_url=IDP, core_base_url=CORE))
    assert type(http.identity).__name__ == "IdentityPlatformAdapter"
    assert type(http.core).__name__ == "CoreClientAdapter"
    await http.aclose()

    with pytest.raises(ValueError):
        build_dependencias(Settings(adapters="otro"))


@pytest.mark.parametrize(
    "demo",
    [{}, {"fake_demo_email": "ana@x.com"}, {"fake_demo_password": "clave"}],
)
async def test_el_modo_fake_sin_usuario_de_demostracion_completo_arranca_sin_usuarios(demo):
    # No se versionan credenciales: sin email y clave configurados no hay nadie que entre.
    deps = build_dependencias(Settings(adapters="fake", **demo))
    with pytest.raises(NoAutorizado):
        await deps.identity.autenticar("ana@x.com", "clave")


async def test_el_modo_fake_crea_el_usuario_de_demostracion_configurado():
    deps = build_dependencias(
        Settings(adapters="fake", fake_demo_email="ana@x.com", fake_demo_password="clave")
    )
    sub = await deps.identity.autenticar("ana@x.com", "clave")
    cliente = await deps.core.buscar_cliente_por_identidad(sub)
    assert cliente.email == "ana@x.com"
