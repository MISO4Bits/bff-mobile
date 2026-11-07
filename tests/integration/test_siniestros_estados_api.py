"""BITS-383 y BITS-384: lista, detalle y envío de lo que falta en el BFF móvil."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator

from app.adapters.fakes import FakeSiniestros
from app.domain import RecursoNoEncontrado
from app.services import SiniestrosService
from tests.conftest import CREDENCIALES_DEMO
from tests.integration.test_siniestros_api import (
    FOTO,
    _con_siniestros,
    _datos,
    _incendio,
    _reportar,
)

RUTA = "/v1/cuenta/siniestros"


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    return client, {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


async def _radicar(client, headers, datos=None) -> str:
    respuesta = await _reportar(client, headers, datos or _incendio())
    assert respuesta.status_code == 201
    return respuesta.json()["id"]


async def _entregar(client, headers, siniestro_id, *, llave=None, contenido=FOTO, archivos=1):
    cabeceras = {**headers, **({"Idempotency-Key": llave} if llave else {})}
    datos = {"archivos": [{"clase": "DOCUMENTO"}] * archivos}
    return await client.post(
        f"{RUTA}/{siniestro_id}/evidencias",
        headers=cabeceras,
        data={"datos": json.dumps(datos)},
        files=[("archivos", (f"a{i}.jpg", contenido, "image/jpeg")) for i in range(archivos)],
    )


# --- lista ---


async def test_la_lista_esta_vacia_y_cumple_el_contrato(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())

    respuesta = await client.get(RUTA, headers=headers)

    assert respuesta.status_code == 200
    assert respuesta.json() == []
    assert respuesta.headers["Cache-Control"] == "no-store"


async def test_la_lista_trae_mis_siniestros_del_mas_reciente_al_mas_antiguo(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())
    vida = await _radicar(client, headers, _datos())
    casa = await _radicar(client, headers)

    respuesta = await client.get(RUTA, headers=headers)

    lista = respuesta.json()
    assert [s["id"] for s in lista] == [casa, vida]
    for item in lista:
        _validar(openapi_spec, "SiniestroResumen", item)
    assert lista[0]["tipo"] == "INCENDIO_TERREMOTO"
    assert lista[0]["automatico"] is False


async def test_sin_sesion_la_lista_el_detalle_y_la_entrega_son_401(app, client):
    _con_siniestros(app, FakeSiniestros())

    assert (await client.get(RUTA)).status_code == 401
    assert (await client.get(f"{RUTA}/x")).status_code == 401
    assert (await client.post(f"{RUTA}/x/evidencias", data={"datos": "{}"})).status_code == 401


# --- detalle ---


async def test_el_detalle_cumple_el_contrato_y_trae_el_plazo(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)

    respuesta = await client.get(f"{RUTA}/{siniestro_id}", headers=headers)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Siniestro", cuerpo)
    assert cuerpo["estado"] == "EN_ESTUDIO"
    assert cuerpo["fechaLimite"] is not None
    assert cuerpo["evidenciaPedida"] is None
    assert len(cuerpo["archivos"]) == 1
    assert respuesta.headers["Cache-Control"] == "no-store"


async def test_un_siniestro_ajeno_o_inexistente_es_404(app, cliente_autenticado):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    ajeno = await siniestros.reportar(
        "otro-cliente", _datos(), [("a.jpg", FOTO, "image/jpeg")], None
    )

    for siniestro_id in (ajeno["id"], "no-existe"):
        respuesta = await client.get(f"{RUTA}/{siniestro_id}", headers=headers)
        assert respuesta.status_code == 404
        assert respuesta.headers["content-type"].startswith("application/problem+json")


async def test_en_falta_informacion_el_detalle_muestra_lo_pedido(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta el recibo de la reparación")

    cuerpo = (await client.get(f"{RUTA}/{siniestro_id}", headers=headers)).json()

    _validar(openapi_spec, "Siniestro", cuerpo)
    assert cuerpo["estado"] == "FALTA_INFORMACION"
    assert cuerpo["fechaLimite"] is None
    assert cuerpo["evidenciaPedida"]["detalle"] == "Falta el recibo de la reparación"


# --- lo que falta ---


async def test_entregar_lo_que_falta_vuelve_a_estudio(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta el recibo")

    respuesta = await _entregar(client, headers, siniestro_id, llave="llave-1")

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Siniestro", cuerpo)
    assert cuerpo["estado"] == "EN_ESTUDIO"
    assert len(cuerpo["archivos"]) == 2
    assert respuesta.headers["Cache-Control"] == "no-store"
    cliente_id, id_, datos, archivos, llave = siniestros.entregas[0]
    assert (cliente_id, id_, llave) == ("cliente-demo", siniestro_id, "llave-1")
    assert datos == {"archivos": [{"clase": "DOCUMENTO"}]}
    assert archivos == [("a0.jpg", FOTO, "image/jpeg")]


async def test_sin_que_se_haya_pedido_nada_la_entrega_es_409_con_su_codigo(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)

    respuesta = await _entregar(client, headers, siniestro_id)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ESTADO_NO_PERMITE"
    _validar(openapi_spec, "ProblemaSiniestro", respuesta.json())


async def test_la_entrega_de_un_siniestro_ajeno_es_404(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_siniestros(app, FakeSiniestros())

    assert (await _entregar(client, headers, "no-existe")).status_code == 404


@pytest.mark.parametrize(
    "crudo",
    [
        "no es json",
        json.dumps({"archivos": []}),
        json.dumps({"archivos": [{"clase": "VIDEO"}]}),
        json.dumps({"archivos": [{"clase": "FOTO"}], "extra": 1}),
        json.dumps({"archivos": [{"clase": "FOTO"}] * 11}),
    ],
)
async def test_los_datos_invalidos_de_la_entrega_son_400_y_no_llegan_a_svc_core(
    app, cliente_autenticado, crudo
):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta algo")

    respuesta = await client.post(
        f"{RUTA}/{siniestro_id}/evidencias",
        headers=headers,
        data={"datos": crudo},
        files=[("archivos", ("a.jpg", FOTO, "image/jpeg"))],
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["errores"][0]["campo"].startswith("datos")
    assert siniestros.entregas == []


async def test_la_entrega_necesita_un_dato_por_archivo(app, cliente_autenticado):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta algo")

    respuesta = await client.post(
        f"{RUTA}/{siniestro_id}/evidencias",
        headers=headers,
        data={"datos": json.dumps({"archivos": [{"clase": "DOCUMENTO"}]})},
        files=[("archivos", ("a.jpg", FOTO, "image/jpeg"))] * 2,
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["errores"][0]["campo"] == "archivos"
    assert siniestros.entregas == []


async def test_una_imagen_demasiado_grande_en_la_entrega_es_413(app, cliente_autenticado, settings):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta algo")
    grande = FOTO + b"\0" * settings.evidencia_max_bytes

    respuesta = await _entregar(client, headers, siniestro_id, contenido=grande)

    assert respuesta.status_code == 413
    assert siniestros.entregas == []


async def test_un_cuerpo_enorme_en_la_entrega_se_corta_antes_de_leerlo(
    app, cliente_autenticado, settings
):
    client, headers = cliente_autenticado
    siniestros = _con_siniestros(app, FakeSiniestros())
    siniestro_id = await _radicar(client, headers)
    siniestros.pedir_evidencia(siniestro_id, "Falta algo")
    enorme = b"\0" * (settings.evidencia_max_bytes * 10 + 100_000)

    respuesta = await client.post(
        f"{RUTA}/{siniestro_id}/evidencias",
        headers=headers,
        data={"datos": json.dumps({"archivos": [{"clase": "DOCUMENTO"}]})},
        files=[("archivos", ("a.jpg", enorme, "image/jpeg"))],
    )

    assert respuesta.status_code == 413
    assert siniestros.entregas == []


# --- el doble ---


async def test_el_doble_repite_la_entrega_de_la_misma_llave_sin_duplicar():
    siniestros = FakeSiniestros()
    reporte = await siniestros.reportar("c", _datos(), [("a.jpg", FOTO, "image/jpeg")], "k")
    siniestros.pedir_evidencia(reporte["id"], "Falta algo")

    await siniestros.entregar_evidencias(
        "c", reporte["id"], {"archivos": [{"clase": "FOTO"}]}, [("b.jpg", FOTO, "image/jpeg")], None
    )
    detalle = await siniestros.obtener_siniestro("c", reporte["id"])

    assert detalle["estado"] == "EN_ESTUDIO"
    assert len(detalle["archivos"]) == 2
    with pytest.raises(RecursoNoEncontrado):
        await siniestros.obtener_siniestro("otro", reporte["id"])


async def test_el_servicio_pasa_las_llamadas_con_el_cliente_de_la_sesion():
    siniestros = FakeSiniestros()
    servicio = SiniestrosService(siniestros, max_bytes=1024)
    reporte = await servicio.reportar("c", _datos(), [("a.jpg", FOTO, "image/jpeg")], None)

    assert [s["id"] for s in await servicio.listar("c")] == [reporte["id"]]
    assert (await servicio.obtener("c", reporte["id"]))["id"] == reporte["id"]
