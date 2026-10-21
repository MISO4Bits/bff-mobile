"""Cotización del BFF móvil: edad derivada de la sesión y cliente HTTP de svc-cotizacion."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.cotizacion_client import CotizacionClientAdapter
from app.adapters.fakes import FakeCotizacion
from app.domain import (
    BffError,
    CotizacionInput,
    DatosCreditoInput,
    NoAutorizado,
    RecursoNoEncontrado,
    SolicitudInvalida,
    calcular_edad,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.services import CotizacionesService

COTIZACION = "http://cotizacion.local"
NACIMIENTO = date(1990, 6, 15)

ENTRADA = CotizacionInput(
    datos_credito=DatosCreditoInput(
        valor_credito=Decimal("120000000"),
        plazo_restante_meses=120,
        entidad_acreedora="Banco Solventa",
        saldo_insoluto=Decimal("100000000"),
    ),
)


def _servicio(hoy: date = date(2026, 10, 16)) -> tuple[CotizacionesService, FakeCotizacion]:
    fake = FakeCotizacion()
    return CotizacionesService(fake, hoy=lambda: hoy), fake


@pytest.mark.parametrize(
    ("hoy", "edad"),
    [
        (date(2026, 6, 14), 35),  # un día antes del cumpleaños 36
        (date(2026, 6, 15), 36),  # el día del cumpleaños ya los cumplió
        (date(2027, 1, 1), 36),
    ],
)
async def test_la_edad_se_deriva_de_la_fecha_de_nacimiento(hoy, edad):
    servicio, fake = _servicio(hoy)

    await servicio.crear("cli-1", NACIMIENTO, ENTRADA)

    assert fake.ultima_edad == edad


def test_calcular_edad_con_nacimiento_un_29_de_febrero():
    assert calcular_edad(date(2000, 2, 29), date(2026, 2, 28)) == 25
    assert calcular_edad(date(2000, 2, 29), date(2026, 3, 1)) == 26


async def test_sin_fecha_de_nacimiento_en_la_sesion_pide_iniciar_sesion_de_nuevo():
    servicio, _ = _servicio()

    with pytest.raises(NoAutorizado):
        await servicio.crear("cli-1", None, ENTRADA)


async def test_la_cotizacion_nueva_reemplaza_a_la_activa_de_cada_cliente():
    servicio, _ = _servicio()
    await servicio.crear("cli-1", NACIMIENTO, ENTRADA)
    otra = CotizacionInput(
        datos_credito=DatosCreditoInput(
            Decimal("300000000"), 60, "Otro Banco", Decimal("250000000")
        )
    )

    nueva = await servicio.crear("cli-1", NACIMIENTO, otra)

    assert await servicio.obtener_activa("cli-1") == nueva
    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_activa("cli-2")


async def test_eliminar_la_activa_deja_al_cliente_sin_cotizacion():
    servicio, _ = _servicio()
    await servicio.crear("cli-1", NACIMIENTO, ENTRADA)

    await servicio.eliminar_activa("cli-1")

    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_activa("cli-1")


async def test_eliminar_sin_cotizacion_activa_no_se_encuentra():
    servicio, _ = _servicio()

    with pytest.raises(RecursoNoEncontrado):
        await servicio.eliminar_activa("cli-1")


# --- cliente HTTP de svc-cotizacion ---


DESGLOSE_JSON = {
    "coberturas": [
        {
            "codigo": "VIDA",
            "obligatoria": True,
            "sumaAsegurada": 100000000.0,
            "primaNeta": 45000.0,
            "iva": 0,
            "primaTotal": 45000.0,
        },
        {
            "codigo": "HOGAR",
            "obligatoria": False,
            "sumaAsegurada": 100000000.0,
            "primaNeta": 9900.0,
            "iva": 1881.0,
            "primaTotal": 11781.0,
        },
    ]
}


def _json(**overrides) -> dict:
    base = {
        "estado": "VIGENTE",
        "producto": "VIDA_HIPOTECARIO",
        "datosCredito": {
            "valorCredito": 120000000,
            "plazoRestanteMeses": 120,
            "entidadAcreedora": "Banco Solventa",
            "saldoInsoluto": 100000000,
        },
        "origenDatos": "DECLARADO",
        "oferta": {
            "primaMensual": 45000.0,
            "primaBaseMensual": 45000.0,
            "sumaAsegurada": 100000000.0,
            "coberturaMeses": 120,
            "moneda": "COP",
            "personalizado": False,
            "desglose": DESGLOSE_JSON,
            "fuentesNoDisponibles": ["perfilamiento"],
        },
        "vigenciaCotizacion": {"desde": "2026-10-16T00:00:00Z", "hasta": "2026-11-15T00:00:00Z"},
        "creadaEn": "2026-10-16T00:00:00Z",
    }
    base.update(overrides)
    return base


def _adapter() -> CotizacionClientAdapter:
    http = ResilientHttpClient(
        COTIZACION, breaker=build_breaker("cot-test", fail_max=5, reset_timeout=30), timeout=1.0
    )
    return CotizacionClientAdapter(http)


@respx.mock
async def test_crear_envia_la_edad_el_cliente_y_los_decimales_como_texto():
    ruta = respx.post(f"{COTIZACION}/v1/cotizaciones").mock(
        return_value=httpx.Response(201, json=_json())
    )
    adapter = _adapter()
    try:
        cotizacion = await adapter.crear_cotizacion("cli-1", 35, ENTRADA)
    finally:
        await adapter.aclose()

    assert cotizacion.origen_datos == "DECLARADO"
    assert cotizacion.datos_credito.plazo_restante_meses == 120
    vida, hogar = cotizacion.oferta.desglose.coberturas
    assert (vida.codigo, vida.obligatoria, vida.iva, vida.prima_total) == (
        "VIDA",
        True,
        0.0,
        45000.0,
    )
    assert (hogar.codigo, hogar.obligatoria, hogar.suma_asegurada) == (
        "HOGAR",
        False,
        100000000.0,
    )
    assert (hogar.prima_neta, hogar.iva, hogar.prima_total) == (9900.0, 1881.0, 11781.0)
    enviado = ruta.calls.last.request
    assert enviado.headers["x-cliente-id"] == "cli-1"
    cuerpo = json.loads(enviado.content)
    assert cuerpo["edad"] == 35
    assert cuerpo["datosCredito"]["valorCredito"] == "120000000"
    assert "cuestionarioHabitos" not in cuerpo


@respx.mock
async def test_crear_con_perfil_lleva_los_factores():
    payload = _json(
        perfilRiesgo={
            "nivelRiesgo": "ALTO",
            "factores": [{"descripcion": "Endeudamiento alto", "efecto": "NEGATIVO"}],
        }
    )
    respx.post(f"{COTIZACION}/v1/cotizaciones").mock(return_value=httpx.Response(201, json=payload))
    adapter = _adapter()
    try:
        cotizacion = await adapter.crear_cotizacion("cli-1", 35, ENTRADA)
    finally:
        await adapter.aclose()

    assert cotizacion.perfil_riesgo.nivel_riesgo == "ALTO"
    assert cotizacion.perfil_riesgo.factores[0].descripcion == "Endeudamiento alto"


@respx.mock
@pytest.mark.parametrize("estado", [400, 422])
async def test_crear_solicitud_invalida_conserva_el_campo_y_el_rango(estado):
    errores = [{"campo": "edad", "mensaje": "Debe estar entre 18 y 65 años"}]
    respx.post(f"{COTIZACION}/v1/cotizaciones").mock(
        return_value=httpx.Response(estado, json={"detail": "fuera de rango", "errores": errores})
    )
    adapter = _adapter()
    try:
        with pytest.raises(SolicitudInvalida) as error:
            await adapter.crear_cotizacion("cli-1", 70, ENTRADA)
    finally:
        await adapter.aclose()

    assert error.value.errores == errores


@respx.mock
async def test_activa_ok_404_y_5xx():
    ruta = respx.get(f"{COTIZACION}/v1/cotizaciones/actual").mock(
        return_value=httpx.Response(200, json=_json())
    )
    adapter = _adapter()
    try:
        assert (await adapter.obtener_cotizacion_activa("cli-1")).estado == "VIGENTE"
        assert ruta.calls.last.request.headers["x-cliente-id"] == "cli-1"

        respx.get(f"{COTIZACION}/v1/cotizaciones/actual").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.obtener_cotizacion_activa("cli-1")

        respx.get(f"{COTIZACION}/v1/cotizaciones/actual").mock(
            return_value=httpx.Response(500, json={})
        )
        with pytest.raises(BffError):
            await adapter.obtener_cotizacion_activa("cli-1")
    finally:
        await adapter.aclose()


@respx.mock
async def test_eliminar_204_404_y_5xx():
    ruta = respx.delete(f"{COTIZACION}/v1/cotizaciones/actual").mock(
        return_value=httpx.Response(204)
    )
    adapter = _adapter()
    try:
        await adapter.eliminar_cotizacion_activa("cli-1")
        assert ruta.calls.last.request.headers["x-cliente-id"] == "cli-1"

        respx.delete(f"{COTIZACION}/v1/cotizaciones/actual").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.eliminar_cotizacion_activa("cli-1")

        respx.delete(f"{COTIZACION}/v1/cotizaciones/actual").mock(
            return_value=httpx.Response(500, json={})
        )
        with pytest.raises(BffError):
            await adapter.eliminar_cotizacion_activa("cli-1")
    finally:
        await adapter.aclose()
