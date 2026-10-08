"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas.

No traen credenciales: el usuario de demostración del modo standalone se configura por
variables de entorno (ver ``app/adapters/factory.py``), no vive en el código.
"""

from __future__ import annotations

from app.domain import ClienteCore, NoAutorizado, RecursoNoEncontrado


class FakeIdentityProvider:
    def __init__(self, usuarios: dict[str, tuple[str, str]] | None = None) -> None:
        # email -> (sub, password)
        self._por_email = dict(usuarios or {})

    async def autenticar(self, email: str, password: str) -> str:
        registro = self._por_email.get(email)
        if registro is None or registro[1] != password:
            raise NoAutorizado("credenciales inválidas")
        return registro[0]


class FakeCoreIdentity:
    def __init__(self, clientes: dict[str, ClienteCore] | None = None) -> None:
        # identity_ref (sub) -> cliente
        self._por_identidad = dict(clientes or {})

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        cliente = self._por_identidad.get(identity_ref)
        if cliente is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return cliente
