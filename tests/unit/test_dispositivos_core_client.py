"""BITS-229: cliente de svc-core para los dispositivos de avisos push (contra respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.domain import DependenciaNoDisponible, RecursoNoEncontrado, SolicitudInvalida
from app.resilience import ResilientHttpClient, build_breaker

CORE = "http://core.local"
DISPOSITIVOS = f"{CORE}/v1/clientes/c1/dispositivos"
CUERPO = {"token": "fcm-token-de-prueba", "plataforma": "android", "versionApp": "1.0.0"}


def _core(*, retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_registrar_envia_el_cuerpo_a_la_ruta_del_cliente():
    ruta = respx.put(DISPOSITIVOS).mock(
        return_value=httpx.Response(200, json={"dispositivoId": "d1"})
    )

    resultado = await _core().registrar_dispositivo("c1", CUERPO)

    assert resultado == {"dispositivoId": "d1"}
    assert ruta.calls[0].request.content == httpx.Request("PUT", DISPOSITIVOS, json=CUERPO).content


@respx.mock
async def test_registrar_se_reintenta_ante_un_fallo_porque_es_idempotente():
    ruta = respx.put(DISPOSITIVOS).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"dispositivoId": "d1"})]
    )

    resultado = await _core(retries=2).registrar_dispositivo("c1", CUERPO)

    assert resultado == {"dispositivoId": "d1"}
    assert ruta.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    ("estado", "error"),
    [(400, SolicitudInvalida), (404, RecursoNoEncontrado), (503, DependenciaNoDisponible)],
)
async def test_registrar_traduce_los_errores_de_core(estado, error):
    respx.put(DISPOSITIVOS).mock(return_value=httpx.Response(estado))

    with pytest.raises(error):
        await _core(retries=0).registrar_dispositivo("c1", CUERPO)


@respx.mock
async def test_quitar_llama_a_la_ruta_del_dispositivo():
    ruta = respx.delete(f"{DISPOSITIVOS}/d1").mock(return_value=httpx.Response(204))

    await _core().quitar_dispositivo("c1", "d1")

    assert ruta.call_count == 1


@respx.mock
async def test_quitar_traduce_los_errores_de_core():
    respx.delete(f"{DISPOSITIVOS}/d1").mock(return_value=httpx.Response(503))

    with pytest.raises(DependenciaNoDisponible):
        await _core(retries=0).quitar_dispositivo("c1", "d1")
