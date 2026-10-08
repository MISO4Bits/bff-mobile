"""Sesión: emisión y refresco de tokens, y orquestación del inicio de sesión."""

from __future__ import annotations

import time

import jwt
import pytest

from app.adapters.fakes import FakeCoreIdentity, FakeIdentityProvider
from app.domain import ClienteCore, NoAutorizado, RecursoNoEncontrado
from app.security import SessionIssuer
from app.services import SesionService

SECRETO = "secreto-de-pruebas-de-al-menos-32-bytes"


def _servicio(usuarios=None, clientes=None) -> SesionService:
    return SesionService(
        FakeIdentityProvider(usuarios),
        FakeCoreIdentity(clientes),
        SessionIssuer(SECRETO),
    )


# --- SessionIssuer ---


def test_emitir_devuelve_tokens_con_los_claims_de_negocio():
    sesion = SessionIssuer(SECRETO, ttl_seconds=600).emitir(
        sub="s1", cliente_id="c1", email="a@b.com"
    )
    assert sesion.token_type == "Bearer"
    assert sesion.expires_in == 600
    acceso = jwt.decode(sesion.access_token, SECRETO, algorithms=["HS256"])
    assert (acceso["sub"], acceso["clienteId"], acceso["email"]) == ("s1", "c1", "a@b.com")
    assert acceso["typ"] == "access"
    refresco = jwt.decode(sesion.refresh_token, SECRETO, algorithms=["HS256"])
    assert refresco["typ"] == "refresh"
    assert refresco["exp"] > acceso["exp"]


def test_verificar_devuelve_los_claims():
    issuer = SessionIssuer(SECRETO)
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com")
    claims = issuer.verificar(sesion.access_token)
    assert (claims.sub, claims.cliente_id, claims.email) == ("s1", "c1", "a@b.com")


def test_refrescar_emite_una_sesion_nueva_para_el_mismo_cliente():
    issuer = SessionIssuer(SECRETO)
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com")
    nueva = issuer.refrescar(sesion.refresh_token)
    assert issuer.verificar(nueva.access_token).cliente_id == "c1"


def test_un_token_de_otro_secreto_se_rechaza():
    # El BFF web y el móvil firman con secretos distintos: un token no cruza de canal.
    issuer = SessionIssuer(SECRETO)
    ajeno = SessionIssuer("otro-secreto-de-al-menos-32-bytes!!").emitir(
        sub="s", cliente_id="c", email="a@b.com"
    )
    with pytest.raises(NoAutorizado):
        issuer.verificar(ajeno.access_token)
    with pytest.raises(NoAutorizado):
        issuer.refrescar(ajeno.refresh_token)


def test_un_token_expirado_se_rechaza():
    vencido = jwt.encode(
        {
            "sub": "s",
            "clienteId": "c",
            "email": "a@b.com",
            "typ": "refresh",
            "exp": int(time.time()) - 10,
        },
        SECRETO,
        algorithm="HS256",
    )
    with pytest.raises(NoAutorizado, match="expirado"):
        SessionIssuer(SECRETO).refrescar(vencido)


def test_el_tipo_de_token_debe_coincidir():
    issuer = SessionIssuer(SECRETO)
    sesion = issuer.emitir(sub="s", cliente_id="c", email="a@b.com")
    with pytest.raises(NoAutorizado):
        issuer.verificar(sesion.refresh_token)  # el refresco no sirve como acceso
    with pytest.raises(NoAutorizado):
        issuer.refrescar(sesion.access_token)  # el acceso no sirve como refresco


@pytest.mark.parametrize("token", ["", "no-es-un-jwt", "a.b.c"])
def test_un_token_malformado_se_rechaza(token):
    with pytest.raises(NoAutorizado, match="inválido"):
        SessionIssuer(SECRETO).refrescar(token)


# --- SesionService ---


async def test_iniciar_sesion_con_credenciales_validas():
    servicio = _servicio(
        usuarios={"ana@x.com": ("sub-1", "clave")},
        clientes={"sub-1": ClienteCore(id="cli-1", email="ana@x.com")},
    )
    sesion = await servicio.iniciar_sesion("ana@x.com", "clave")
    claims = SessionIssuer(SECRETO).verificar(sesion.access_token)
    assert (claims.sub, claims.cliente_id, claims.email) == ("sub-1", "cli-1", "ana@x.com")


async def test_iniciar_sesion_con_clave_incorrecta_es_no_autorizado():
    servicio = _servicio(usuarios={"ana@x.com": ("sub-1", "clave")}, clientes={})
    with pytest.raises(NoAutorizado):
        await servicio.iniciar_sesion("ana@x.com", "otra")


async def test_iniciar_sesion_con_correo_desconocido_es_no_autorizado():
    with pytest.raises(NoAutorizado):
        await _servicio().iniciar_sesion("nadie@x.com", "clave")


async def test_iniciar_sesion_sin_cliente_asociado_es_no_encontrado():
    servicio = _servicio(usuarios={"ana@x.com": ("sub-1", "clave")}, clientes={})
    with pytest.raises(RecursoNoEncontrado):
        await servicio.iniciar_sesion("ana@x.com", "clave")


async def test_refrescar_acepta_el_refresh_de_una_sesion_iniciada():
    servicio = _servicio()
    sesion = await servicio.iniciar_sesion("ana.rios@example.com", "unaClaveSegura1")
    nueva = servicio.refrescar(sesion.refresh_token)
    assert SessionIssuer(SECRETO).verificar(nueva.access_token).email == "ana.rios@example.com"
