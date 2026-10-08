"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas.

El modo standalone trae un usuario de demostración (solo para desarrollo del canal).
"""

from __future__ import annotations

from app.domain import ClienteCore, NoAutorizado, RecursoNoEncontrado

USUARIO_DEMO_EMAIL = "ana.rios@example.com"
USUARIO_DEMO_PASSWORD = "unaClaveSegura1"
USUARIO_DEMO_SUB = "sub-demo-ana"


class FakeIdentityProvider:
    def __init__(self, usuarios: dict[str, tuple[str, str]] | None = None) -> None:
        # email -> (sub, password)
        self._por_email = (
            {USUARIO_DEMO_EMAIL: (USUARIO_DEMO_SUB, USUARIO_DEMO_PASSWORD)}
            if usuarios is None
            else dict(usuarios)
        )

    async def autenticar(self, email: str, password: str) -> str:
        registro = self._por_email.get(email)
        if registro is None or registro[1] != password:
            raise NoAutorizado("credenciales inválidas")
        return registro[0]


class FakeCoreIdentity:
    def __init__(self, clientes: dict[str, ClienteCore] | None = None) -> None:
        # identity_ref (sub) -> cliente
        self._por_identidad = (
            {USUARIO_DEMO_SUB: ClienteCore(id="cliente-demo-ana", email=USUARIO_DEMO_EMAIL)}
            if clientes is None
            else dict(clientes)
        )

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        cliente = self._por_identidad.get(identity_ref)
        if cliente is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return cliente
