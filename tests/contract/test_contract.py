"""Pruebas de contrato: la implementación del BFF móvil cumple ``openapi/openapi.yaml``."""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator

from tests.conftest import CREDENCIALES_DEMO

_PATH_PARAM = re.compile(r"\{[^}]+\}")
_METODOS = {"get", "post", "put", "patch", "delete"}


def _normalize(method: str, path: str) -> tuple[str, str]:
    return method.upper(), _PATH_PARAM.sub("{}", path)


def _spec_ops(spec: dict) -> set[tuple[str, str]]:
    return {
        _normalize(m, p) for p, item in spec["paths"].items() for m in item if m.lower() in _METODOS
    }


def _app_ops(app) -> set[tuple[str, str]]:
    return {
        _normalize(m, p)
        for p, item in app.openapi()["paths"].items()
        for m in item
        if m.lower() in _METODOS
    }


def test_contrato_y_codigo_exponen_las_mismas_operaciones(app, openapi_spec):
    assert _spec_ops(openapi_spec) == _app_ops(app)
    assert _spec_ops(openapi_spec) == {
        ("POST", "/v1/sesiones"),
        ("POST", "/v1/sesiones/refresco"),
        ("GET", "/v1/creditos-hipotecarios"),
    }


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


async def test_respuestas_cumplen_el_contrato(client, openapi_spec):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    assert sesion.status_code == 200
    _validar(openapi_spec, "Sesion", sesion.json())

    refresco = await client.post(
        "/v1/sesiones/refresco", json={"refreshToken": sesion.json()["refreshToken"]}
    )
    assert refresco.status_code == 200
    _validar(openapi_spec, "Sesion", refresco.json())


async def test_errores_cumplen_problem_details(client, openapi_spec):
    sin_acceso = await client.post("/v1/sesiones", json={**CREDENCIALES_DEMO, "password": "x"})
    assert sin_acceso.status_code == 401
    assert sin_acceso.headers["content-type"].startswith("application/problem+json")
    _validar(openapi_spec, "Problema", sin_acceso.json())

    malo = await client.post("/v1/sesiones", json={"email": "x"})
    assert malo.status_code == 400
    _validar(openapi_spec, "ProblemaValidacion", malo.json())

    refresco_malo = await client.post("/v1/sesiones/refresco", json={"refreshToken": "basura"})
    assert refresco_malo.status_code == 401
    _validar(openapi_spec, "Problema", refresco_malo.json())


def test_los_ejemplos_cumplen_su_esquema(openapi_spec):
    for nombre, esquema in openapi_spec["components"]["schemas"].items():
        for ejemplo in esquema.get("examples", []):
            _validar(openapi_spec, nombre, ejemplo)

    for respuesta in openapi_spec["components"]["responses"].values():
        for media in respuesta["content"].values():
            ref = media["schema"]["$ref"].rsplit("/", 1)[-1]
            for ejemplo in media.get("examples", {}).values():
                _validar(openapi_spec, ref, ejemplo["value"])


async def test_creditos_hipotecarios_cumplen_el_contrato(client, openapi_spec):
    sesion = await client.post("/v1/sesiones", json=CREDENCIALES_DEMO)
    headers = {"Authorization": f"Bearer {sesion.json()['accessToken']}"}

    resp = await client.get("/v1/creditos-hipotecarios", headers=headers)

    assert resp.status_code == 200
    _validar(openapi_spec, "CreditosHipotecarios", resp.json())
