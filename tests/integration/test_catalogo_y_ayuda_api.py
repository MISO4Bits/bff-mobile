"""BITS-385: catálogo de siniestros y solicitud de ayuda en sitio en el BFF móvil."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio
import respx
from jsonschema import Draft202012Validator

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeCatalogoSiniestros, FakeSiniestros
from app.adapters.productos_client import ProductosClientAdapter
from app.domain import (
    BffError,
    Conflicto,
    DependenciaNoDisponible,
    RecursoNoEncontrado,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.services import CatalogoSiniestrosService, SiniestrosService
from tests.conftest import CREDENCIALES_DEMO
from tests.integration.test_siniestros_api import (
    _datos,
    _incendio,
    _reportar,
)

CATALOGO = "/v1/catalogos/siniestros"
RUTA = "/v1/cuenta/siniestros"
PRESTADOR = {
    "id": "pr-001",
    "nombre": "Hogar Reparaciones Cedritos",
    "direccion": "Calle 123 # 1-87",
    "tiempoLlegadaMin": 20,
}
DESDE_FOTOS = {
    "tipo": "plomeria",
    "prestador": PRESTADOR,
    "origen": "FOTOS",
    "latitud": 4.65,
    "longitud": -74.06,
}
DESDE_DIRECCION = {
    "tipo": "electricidad",
    "prestador": PRESTADOR,
    "origen": "DIRECCION",
    "direccion": "Carrera 7 # 71-21",
    "localidad": "chapinero",
}


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return client, {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


@pytest.fixture
def siniestros(app) -> FakeSiniestros:
    fake = FakeSiniestros()
    app.state.siniestros = SiniestrosService(fake, max_bytes=app.state.settings.evidencia_max_bytes)
    return fake


async def _siniestro_de_incendio(client, headers) -> str:
    respuesta = await _reportar(client, headers, _incendio())
    assert respuesta.status_code == 201
    return respuesta.json()["id"]


def _pedir(client, headers, siniestro_id, cuerpo=None, llave=None):
    cabeceras = {**headers, **({"Idempotency-Key": llave} if llave else {})}
    return client.post(
        f"{RUTA}/{siniestro_id}/asistencias",
        json=cuerpo if cuerpo is not None else DESDE_FOTOS,
        headers=cabeceras,
    )


# --- catálogo ---


async def test_el_catalogo_responde_y_cumple_el_contrato(cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado

    respuesta = await client.get(CATALOGO, headers=headers)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "CatalogoSiniestros", cuerpo)
    assert [t["id"] for t in cuerpo["tiposAyuda"]][:2] == ["plomeria", "electricidad"]
    assert cuerpo["prestadores"][0]["tiempoLlegadaMin"] == 20
    assert {o["banco"] for o in cuerpo["oficinas"]} == {"Bancolombia", "BBVA"}
    assert respuesta.headers["cache-control"] == "private, max-age=3600"


async def test_el_catalogo_exige_sesion(client):
    assert (await client.get(CATALOGO)).status_code == 401


async def test_un_mercado_desconocido_es_400(cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await client.get(CATALOGO, params={"mercado": "MX"}, headers=headers)

    assert respuesta.status_code == 400


async def test_el_catalogo_se_guarda_en_memoria_y_no_se_pide_de_nuevo(app, cliente_autenticado):
    client, headers = cliente_autenticado
    fake = FakeCatalogoSiniestros()
    app.state.catalogo_siniestros = CatalogoSiniestrosService(fake)

    for _ in range(3):
        assert (await client.get(CATALOGO, headers=headers)).status_code == 200

    assert fake.llamadas == 1


async def test_el_catalogo_vuelve_a_pedirse_cuando_vence_el_cache():
    fake = FakeCatalogoSiniestros()
    ahora = [0.0]
    servicio = CatalogoSiniestrosService(fake, cache_segundos=60, reloj=lambda: ahora[0])

    await servicio.obtener("CO")
    ahora[0] = 59
    await servicio.obtener("CO")
    ahora[0] = 61
    await servicio.obtener("CO")

    assert fake.llamadas == 2


async def test_el_catalogo_de_un_mercado_sin_catalogo_no_se_guarda():
    servicio = CatalogoSiniestrosService(FakeCatalogoSiniestros())

    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener("MX")


# --- pedir ayuda ---


async def test_pedir_ayuda_responde_201_y_cumple_el_contrato(
    app, cliente_autenticado, siniestros, openapi_spec
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    respuesta = await _pedir(client, headers, siniestro_id)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Asistencia", cuerpo)
    assert cuerpo["numero"].startswith("ASI-")
    assert cuerpo["prestador"] == PRESTADOR
    assert cuerpo["tiempoLlegadaMin"] == 20
    assert respuesta.headers["cache-control"] == "no-store"
    cliente_id, id_siniestro, enviado, llave = siniestros.ayudas[0]
    assert cliente_id == "cliente-demo"  # sale de la sesión, no del cuerpo
    assert id_siniestro == siniestro_id
    assert enviado == DESDE_FOTOS
    assert llave is None


async def test_pedir_ayuda_con_una_direccion_pasa_la_direccion_y_la_localidad(
    app, cliente_autenticado, siniestros, openapi_spec
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    respuesta = await _pedir(client, headers, siniestro_id, DESDE_DIRECCION)

    assert respuesta.status_code == 201
    _validar(openapi_spec, "Asistencia", respuesta.json())
    assert siniestros.ayudas[0][2] == DESDE_DIRECCION


async def test_el_detalle_del_siniestro_trae_las_solicitudes(
    app, cliente_autenticado, siniestros, openapi_spec
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)
    creada = (await _pedir(client, headers, siniestro_id)).json()

    detalle = await client.get(f"{RUTA}/{siniestro_id}", headers=headers)

    assert detalle.status_code == 200
    _validar(openapi_spec, "Siniestro", detalle.json())
    assert detalle.json()["asistencias"] == [creada]


async def test_la_llave_de_idempotencia_llega_a_svc_core_y_repite_la_misma_solicitud(
    app, cliente_autenticado, siniestros
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    primera = await _pedir(client, headers, siniestro_id, llave="ayuda-1")
    repetida = await _pedir(client, headers, siniestro_id, llave="ayuda-1")

    assert primera.json() == repetida.json()
    assert [a[3] for a in siniestros.ayudas] == ["ayuda-1", "ayuda-1"]


async def test_un_siniestro_de_incapacidad_total_responde_409_tal_cual(
    app, cliente_autenticado, siniestros, openapi_spec
):
    client, headers = cliente_autenticado
    respuesta = await _reportar(client, headers, _datos())
    siniestro_id = respuesta.json()["id"]

    ayuda = await _pedir(client, headers, siniestro_id)

    assert ayuda.status_code == 409
    assert ayuda.json()["codigo"] == "AYUDA_NO_DISPONIBLE"
    _validar(openapi_spec, "ProblemaSiniestro", ayuda.json())


async def test_un_siniestro_cerrado_responde_409_estado_no_permite(
    app, cliente_autenticado, siniestros
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)
    siniestros._items[siniestro_id]["estado"] = "RECHAZADO"

    ayuda = await _pedir(client, headers, siniestro_id)

    assert ayuda.status_code == 409
    assert ayuda.json()["codigo"] == "ESTADO_NO_PERMITE"


async def test_el_siniestro_de_otro_cliente_o_inexistente_es_404(
    app, cliente_autenticado, siniestros
):
    client, headers = cliente_autenticado
    siniestros._items["ajeno"] = {
        "id": "ajeno",
        "tipo": "INCENDIO_TERREMOTO",
        "estado": "EN_ESTUDIO",
    }
    siniestros._duenos["ajeno"] = "otro-cliente"

    assert (await _pedir(client, headers, "ajeno")).status_code == 404
    assert (await _pedir(client, headers, "no-existe")).status_code == 404


@pytest.mark.parametrize(
    "cambios",
    [
        {"latitud": None, "longitud": None},
        {"longitud": None},
        {"direccion": "Calle 1"},
        {"origen": "DIRECCION"},
        {"latitud": 91},
        {"tipo": "Plomería"},
        {"origen": "OTRO"},
        {"prestador": {**PRESTADOR, "tiempoLlegadaMin": 0}},
        {"prestador": {"id": "pr-001"}},
        {"agente": "alguien"},
    ],
)
async def test_un_cuerpo_incoherente_es_400_y_no_llega_a_svc_core(
    app, cliente_autenticado, siniestros, cambios
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    respuesta = await _pedir(client, headers, siniestro_id, {**DESDE_FOTOS, **cambios})

    assert respuesta.status_code == 400
    assert siniestros.ayudas == []


async def test_con_origen_direccion_no_van_coordenadas(app, cliente_autenticado, siniestros):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    respuesta = await _pedir(
        client, headers, siniestro_id, {**DESDE_DIRECCION, "latitud": 4.6, "longitud": -74.0}
    )

    assert respuesta.status_code == 400


async def test_pedir_ayuda_exige_sesion(client):
    respuesta = await client.post(f"{RUTA}/s-1/asistencias", json=DESDE_FOTOS)

    assert respuesta.status_code == 401


async def test_el_log_no_escribe_la_ubicacion_ni_la_direccion(
    app, cliente_autenticado, siniestros, caplog
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)

    with caplog.at_level("INFO"):
        await _pedir(client, headers, siniestro_id, DESDE_DIRECCION)
        await _pedir(client, headers, siniestro_id, DESDE_FOTOS)

    assert "Carrera 7 # 71-21" not in caplog.text
    assert "4.65" not in caplog.text


async def test_si_svc_core_es_anterior_el_detalle_sigue_funcionando_sin_asistencias(
    app, cliente_autenticado, siniestros, openapi_spec
):
    client, headers = cliente_autenticado
    siniestro_id = await _siniestro_de_incendio(client, headers)
    del siniestros._items[siniestro_id]["asistencias"]

    detalle = await client.get(f"{RUTA}/{siniestro_id}", headers=headers)

    assert detalle.status_code == 200
    assert detalle.json()["asistencias"] == []


# --- adaptadores ---

CORE = "http://core.local"
PRODUCTOS = "http://productos.local"


def _http(base: str, *, retries: int = 2) -> ResilientHttpClient:
    return ResilientHttpClient(
        base,
        breaker=build_breaker("x", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )


@respx.mock
async def test_el_adaptador_de_productos_pide_el_catalogo_del_mercado():
    ruta = respx.get(f"{PRODUCTOS}/v1/catalogo-siniestros").mock(
        return_value=httpx.Response(200, json={"mercado": "CO"})
    )
    adaptador = ProductosClientAdapter(_http(PRODUCTOS))
    try:
        catalogo = await adaptador.obtener_catalogo("CO")
    finally:
        await adaptador.aclose()

    assert catalogo == {"mercado": "CO"}
    assert dict(ruta.calls.last.request.url.params) == {"mercado": "CO"}


@respx.mock
@pytest.mark.parametrize(
    ("estado", "error"),
    [
        (404, RecursoNoEncontrado),
        (400, SolicitudInvalida),
        (422, SolicitudInvalida),
        (500, BffError),
    ],
)
async def test_el_adaptador_de_productos_traduce_los_errores(estado, error):
    respx.get(f"{PRODUCTOS}/v1/catalogo-siniestros").mock(
        return_value=httpx.Response(estado, json={"detail": "x"})
    )
    adaptador = ProductosClientAdapter(_http(PRODUCTOS, retries=0))
    try:
        with pytest.raises(error):
            await adaptador.obtener_catalogo("CO")
    finally:
        await adaptador.aclose()


@respx.mock
async def test_pedir_ayuda_va_como_json_con_la_llave_y_se_reintenta_solo_con_llave():
    ruta = respx.post(f"{CORE}/v1/clientes/c-1/siniestros/s-1/asistencias").mock(
        return_value=httpx.Response(503, json={})
    )
    adaptador = CoreClientAdapter(_http(CORE, retries=3))
    try:
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.pedir_ayuda("c-1", "s-1", DESDE_FOTOS, None)
        sin_llave = ruta.call_count
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.pedir_ayuda("c-1", "s-1", DESDE_FOTOS, "ayuda-1")
    finally:
        await adaptador.aclose()

    assert sin_llave == 1  # sin llave, repetir crearía otra solicitud
    assert ruta.call_count > 2  # con llave, se puede reintentar
    ultima = ruta.calls.last.request
    assert ultima.headers["Idempotency-Key"] == "ayuda-1"
    assert json.loads(ultima.content) == DESDE_FOTOS


@respx.mock
async def test_pedir_ayuda_devuelve_el_cuerpo_de_svc_core():
    respx.post(f"{CORE}/v1/clientes/c-1/siniestros/s%2F1/asistencias").mock(
        return_value=httpx.Response(201, json={"id": "a-1"})
    )
    adaptador = CoreClientAdapter(_http(CORE))
    try:
        resultado = await adaptador.pedir_ayuda("c-1", "s/1", DESDE_FOTOS, None)
    finally:
        await adaptador.aclose()

    assert resultado == {"id": "a-1"}


@respx.mock
@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado"),
    [
        (409, {"detail": "no", "codigo": "AYUDA_NO_DISPONIBLE"}, Conflicto),
        (409, {"detail": "no", "codigo": "ESTADO_NO_PERMITE"}, Conflicto),
        (404, {"detail": "no existe"}, RecursoNoEncontrado),
        (
            400,
            {"detail": "mal", "errores": [{"campo": "origen", "mensaje": "x"}]},
            SolicitudInvalida,
        ),
    ],
)
async def test_pedir_ayuda_traduce_los_errores_conservando_el_codigo(estado, cuerpo, esperado):
    respx.post(f"{CORE}/v1/clientes/c-1/siniestros/s-1/asistencias").mock(
        return_value=httpx.Response(estado, json=cuerpo)
    )
    adaptador = CoreClientAdapter(_http(CORE))
    try:
        with pytest.raises(esperado) as error:
            await adaptador.pedir_ayuda("c-1", "s-1", DESDE_FOTOS, "k")
    finally:
        await adaptador.aclose()

    assert getattr(error.value, "codigo", None) == cuerpo.get("codigo")
