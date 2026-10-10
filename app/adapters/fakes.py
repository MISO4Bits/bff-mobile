"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas.

No traen credenciales: el usuario de demostración del modo standalone se configura por
variables de entorno (ver ``app/adapters/factory.py``), no vive en el código.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain import (
    ESTADO_DISPONIBLE,
    ClienteCore,
    CreditosReportados,
    EntidadFinanciera,
    HipotecaReportada,
    NoAutorizado,
    RecursoNoEncontrado,
)


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


class FakeCreditosHipotecarios:
    """Perfilamiento simulado: devuelve ``respuesta`` (por defecto, una hipoteca de BBVA)."""

    def __init__(self, respuesta: CreditosReportados | None = None) -> None:
        self.respuesta = respuesta or CreditosReportados(
            estado=ESTADO_DISPONIBLE,
            hipotecas=(
                HipotecaReportada(
                    entidad_acreedora="BBVA COLOMBIA S.A.",
                    valor_credito=Decimal("200000000"),
                    saldo_insoluto=Decimal("160000000"),
                    plazo_restante_meses=180,
                    cuota_mensual=Decimal("2100000"),
                ),
            ),
            fecha_consulta=datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
        )
        self.llamadas: list[str] = []

    async def obtener(self, cliente_id: str) -> CreditosReportados:
        self.llamadas.append(cliente_id)
        return self.respuesta


_ENTIDADES_DE_EJEMPLO = (
    EntidadFinanciera("bancolombia", "Bancolombia", ("BANCOLOMBIA S.A.",)),
    EntidadFinanciera("davivienda", "Davivienda", ("DAVIVIENDA S.A.",)),
    EntidadFinanciera("banco-de-bogota", "Banco de Bogotá", ("BANCO DE BOGOTÁ S.A.",)),
    EntidadFinanciera("bbva-colombia", "BBVA Colombia", ("BBVA COLOMBIA S.A.",)),
)


class FakeEntidadesFinancieras:
    """Lista del mercado ``CO`` para correr el BFF standalone."""

    def __init__(self, entidades: tuple[EntidadFinanciera, ...] = _ENTIDADES_DE_EJEMPLO) -> None:
        self._entidades = entidades
        self.llamadas = 0

    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]:
        self.llamadas += 1
        return list(self._entidades) if mercado == "CO" else []
