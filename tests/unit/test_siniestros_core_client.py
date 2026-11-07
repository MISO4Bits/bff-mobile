"""BITS-381: el adaptador de svc-core envía el reporte multipart y traduce sus errores."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.domain import (
    BffError,
    Conflicto,
    DependenciaNoDisponible,
    ImagenMuyGrande,
    RecursoNoEncontrado,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker

CORE = "http://core.local"
RUTA = f"{CORE}/v1/clientes/c-1/siniestros"
DATOS = {"polizaId": "p-1", "tipo": "INCAPACIDAD_TOTAL", "fechaOcurrencia": "2026-11-10"}
ARCHIVOS = [("a.jpg", b"\xff\xd8\xff una foto", "image/jpeg")]
SINIESTRO = {"id": "s-1", "numero": "SIN-2026-ABC123", "estado": "EN_ESTUDIO"}


def _core(retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=9, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_envia_datos_y_archivos_como_multipart_con_la_llave():
    ruta = respx.post(RUTA).mock(return_value=httpx.Response(201, json=SINIESTRO))
    adaptador = _core()
    try:
        siniestro = await adaptador.reportar("c-1", DATOS, ARCHIVOS, "llave-1")
    finally:
        await adaptador.aclose()

    assert siniestro == SINIESTRO
    pedido = ruta.calls.last.request
    assert pedido.headers["Idempotency-Key"] == "llave-1"
    assert pedido.headers["content-type"].startswith("multipart/form-data")
    cuerpo = pedido.content
    assert json.dumps(DATOS).encode() in cuerpo
    assert b'name="archivos"; filename="a.jpg"' in cuerpo
    assert ARCHIVOS[0][1] in cuerpo


@respx.mock
async def test_sin_llave_no_envia_el_encabezado():
    ruta = respx.post(RUTA).mock(return_value=httpx.Response(201, json=SINIESTRO))
    adaptador = _core()
    try:
        await adaptador.reportar("c-1", DATOS, ARCHIVOS, None)
    finally:
        await adaptador.aclose()

    assert "Idempotency-Key" not in ruta.calls.last.request.headers


@respx.mock
async def test_no_reintenta_aunque_el_cliente_reintente():
    ruta = respx.post(RUTA).mock(return_value=httpx.Response(201, json=SINIESTRO))
    adaptador = _core(retries=3)
    try:
        await adaptador.reportar("c-1", DATOS, ARCHIVOS, "llave-1")
    finally:
        await adaptador.aclose()

    assert ruta.call_count == 1


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado"),
    [
        (
            400,
            {"detail": "Falta el dictamen", "errores": [{"campo": "archivos", "mensaje": "x"}]},
            SolicitudInvalida,
        ),
        (400, {"detail": "no cubierto", "codigo": "TIPO_NO_CUBIERTO"}, SolicitudInvalida),
        (404, {"detail": "Póliza no encontrada"}, RecursoNoEncontrado),
        (409, {"detail": "en curso", "codigo": "SINIESTRO_EN_CURSO"}, Conflicto),
        (409, {"detail": "fuera", "codigo": "FUERA_DE_VIGENCIA"}, Conflicto),
        (413, {"detail": "grande"}, ImagenMuyGrande),
        (418, {}, BffError),
    ],
)
@respx.mock
async def test_traduce_los_errores_de_svc_core_conservando_el_codigo(estado, cuerpo, esperado):
    respx.post(RUTA).mock(return_value=httpx.Response(estado, json=cuerpo))
    adaptador = _core()
    try:
        with pytest.raises(esperado) as error:
            await adaptador.reportar("c-1", DATOS, ARCHIVOS, None)
    finally:
        await adaptador.aclose()

    assert getattr(error.value, "codigo", None) == cuerpo.get("codigo")
    assert getattr(error.value, "errores", None) == cuerpo.get("errores")


@respx.mock
async def test_si_svc_core_no_esta_disponible_falla_sin_reintentar():
    ruta = respx.post(RUTA).mock(return_value=httpx.Response(503, json={}))
    adaptador = _core()
    try:
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.reportar("c-1", DATOS, ARCHIVOS, "llave-1")
    finally:
        await adaptador.aclose()

    assert ruta.call_count == 1
