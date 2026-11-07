"""BITS-381: ``POST /v1/cuenta/siniestros`` (BFF móvil) contra el contrato OpenAPI."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator

from app.adapters.fakes import FakeSiniestros
from app.domain import DependenciaNoDisponible, SolicitudInvalida
from app.services import SiniestrosService
from tests.conftest import CREDENCIALES_DEMO

RUTA = "/v1/cuenta/siniestros"
FOTO = b"\xff\xd8\xff\xe0 una foto"
TRANSFERENCIA = {
    "tipo": "TRANSFERENCIA",
    "banco": "Bancolombia",
    "tipoCuenta": "AHORROS",
    "numeroCuenta": "48210012345",
}


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    """(client, headers) con una sesión móvil válida del cliente de demostración."""
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return client, {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


def _datos(**cambios) -> dict:
    base = {
        "polizaId": "poliza-1",
        "tipo": "INCAPACIDAD_TOTAL",
        "fechaOcurrencia": "2026-11-10",
        "archivos": [{"clase": "DOCUMENTO", "capturadoEn": "2026-11-10T15:00:00Z"}],
    }
    return {**base, **cambios}


def _incendio(**cambios) -> dict:
    base = {
        "tipo": "INCENDIO_TERREMOTO",
        "narracion": "Se incendió la cocina",
        "destinoPago": TRANSFERENCIA,
        "archivos": [
            {
                "clase": "FOTO",
                "capturadoEn": "2026-11-10T15:00:00Z",
                "latitud": 4.65,
                "longitud": -74.06,
            }
        ],
    }
    return _datos(**{**base, **cambios})


def _con_siniestros(app, siniestros) -> FakeSiniestros:
    app.state.siniestros = SiniestrosService(
        siniestros, max_bytes=app.state.settings.evidencia_max_bytes
    )
    return siniestros


async def _reportar(client, headers, datos, *, archivos=1, llave=None, contenido=FOTO):
    cabeceras = {**headers, **({"Idempotency-Key": llave} if llave else {})}
    return await client.post(
        RUTA,
        headers=cabeceras,
        data={"datos": json.dumps(datos)},
        files=[("archivos", (f"a{i}.jpg", contenido, "image/jpeg")) for i in range(archivos)],
    )


async def test_reportar_responde_201_y_cumple_el_contrato(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())

    respuesta = await _reportar(client, headers, _incendio())

    assert respuesta.status_code == 201
    _validar(openapi_spec, "SiniestroReportado", respuesta.json())
    assert respuesta.json()["estado"] == "EN_ESTUDIO"
    assert respuesta.headers["Cache-Control"] == "no-store"
    cliente_id, datos, archivos, llave = siniestros.llamadas[0]
    assert cliente_id == "cliente-demo"  # sale de la sesión, no del cuerpo
    assert datos["tipo"] == "INCENDIO_TERREMOTO"
    assert datos["destinoPago"] == TRANSFERENCIA
    assert datos["archivos"][0]["latitud"] == 4.65
    assert archivos == [("a0.jpg", FOTO, "image/jpeg")]
    assert llave is None


async def test_la_llave_de_idempotencia_llega_a_svc_core(app, cliente_autenticado):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())

    primero = await _reportar(client, headers, _datos(), llave="llave-1")
    segundo = await _reportar(client, headers, _datos(), llave="llave-1")

    assert primero.json() == segundo.json()
    assert [llamada[3] for llamada in siniestros.llamadas] == ["llave-1", "llave-1"]


async def test_un_segundo_reporte_de_la_cobertura_en_curso_pasa_el_409_tal_cual(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())
    await _reportar(client, headers, _datos(), llave="a")

    respuesta = await _reportar(client, headers, _datos(), llave="b")

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "SINIESTRO_EN_CURSO"
    assert respuesta.headers["content-type"].startswith("application/problem+json")
    _validar(openapi_spec, "ProblemaSiniestro", respuesta.json())


class _Falla:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def reportar(self, *args, **kwargs):
        raise self._error


@pytest.mark.parametrize(
    ("error", "estado", "codigo"),
    [
        (
            SolicitudInvalida("Falta el dictamen", errores=[{"campo": "archivos", "mensaje": "x"}]),
            400,
            None,
        ),
        (DependenciaNoDisponible("no responde", codigo="IDENTIDAD_NO_DISPONIBLE"), 503, None),
    ],
)
async def test_los_errores_de_svc_core_salen_en_problem_details(
    app, cliente_autenticado, error, estado, codigo
):
    client, headers = cliente_autenticado
    _con_siniestros(app, _Falla(error))

    respuesta = await _reportar(client, headers, _datos())

    assert respuesta.status_code == estado
    assert respuesta.headers["content-type"].startswith("application/problem+json")


async def test_sin_sesion_es_401(app, client):
    _con_siniestros(app, FakeSiniestros())

    respuesta = await client.post(
        RUTA,
        data={"datos": json.dumps(_datos())},
        files=[("archivos", ("a.jpg", FOTO, "image/jpeg"))],
    )

    assert respuesta.status_code == 401


async def test_un_dato_por_archivo_y_viceversa(app, cliente_autenticado):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())

    respuesta = await _reportar(client, headers, _datos(), archivos=2)

    assert respuesta.status_code == 400
    assert respuesta.json()["errores"][0]["campo"] == "archivos"
    assert siniestros.llamadas == []


@pytest.mark.parametrize(
    "crudo",
    [
        "esto no es json",
        json.dumps(_datos(tipo="SISMO_PARAMETRICO")),
        json.dumps(_datos(fechaOcurrencia="mañana")),
        json.dumps(_datos(archivos=[{"clase": "FOTO", "latitud": 120.0}])),
        json.dumps(_datos(esAdmin=True)),
        json.dumps(_incendio(destinoPago={**TRANSFERENCIA, "numeroCuenta": "12ab"})),
        json.dumps(_incendio(destinoPago={"tipo": "EFECTIVO"})),
        json.dumps(_datos(narracion="x" * 4001)),
        json.dumps(_datos(archivos=[{"clase": "DOCUMENTO"}] * 11)),
    ],
)
async def test_datos_invalidos_son_400_con_el_campo_y_sin_llegar_a_svc_core(
    app, cliente_autenticado, crudo
):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())

    respuesta = await client.post(
        RUTA,
        headers=headers,
        data={"datos": crudo},
        files=[("archivos", ("a.jpg", FOTO, "image/jpeg"))],
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["errores"][0]["campo"].startswith("datos")
    assert "12ab" not in respuesta.text
    assert siniestros.llamadas == []


async def test_sin_la_parte_de_archivos_es_400(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())

    respuesta = await client.post(RUTA, headers=headers, data={"datos": json.dumps(_datos())})

    assert respuesta.status_code == 400


async def test_una_imagen_demasiado_grande_es_413_y_no_llega_a_svc_core(
    app, cliente_autenticado, settings
):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    grande = FOTO + b"\0" * settings.evidencia_max_bytes

    respuesta = await _reportar(client, headers, _datos(), contenido=grande)

    assert respuesta.status_code == 413
    assert siniestros.llamadas == []


async def test_un_cuerpo_enorme_se_corta_antes_de_leerlo(app, cliente_autenticado, settings):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    enorme = b"\0" * (settings.evidencia_max_bytes * 10 + 100_000)

    respuesta = await client.post(
        RUTA,
        headers=headers,
        data={"datos": json.dumps(_datos())},
        files=[("archivos", ("a.jpg", enorme, "image/jpeg"))],
    )

    assert respuesta.status_code == 413
    assert siniestros.llamadas == []


async def test_el_limite_de_las_fotos_de_cedula_no_cambia(app, cliente_autenticado, settings):
    client, headers = cliente_autenticado
    enorme = b"\0" * (settings.evidencia_max_bytes + 100_000)

    respuesta = await client.post(
        "/v1/cuenta/evidencias-identidad",
        headers=headers,
        data={"tipo": "cedula_frente"},
        files={"imagen": ("a.jpg", enorme, "image/jpeg")},
    )

    assert respuesta.status_code == 413


async def test_el_log_no_incluye_el_destino_del_pago(app, cliente_autenticado, caplog):
    import logging

    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())

    with caplog.at_level(logging.INFO):
        await _reportar(client, headers, _incendio())

    assert TRANSFERENCIA["numeroCuenta"] not in caplog.text


async def test_el_doble_acepta_la_misma_cobertura_de_otra_poliza(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())

    primero = await _reportar(client, headers, _datos(polizaId="poliza-1"))
    segundo = await _reportar(client, headers, _datos(polizaId="poliza-2"))

    assert (primero.status_code, segundo.status_code) == (201, 201)
