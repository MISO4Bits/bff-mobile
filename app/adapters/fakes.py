"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas.

No traen credenciales: el usuario de demostración del modo standalone se configura por
variables de entorno (ver ``app/adapters/factory.py``), no vive en el código.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.domain import (
    ESTADO_DISPONIBLE,
    ClienteCore,
    CoberturaPrecio,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    DatosCreditoInput,
    Desglose,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    HipotecaReportada,
    NoAutorizado,
    Oferta,
    RecursoNoEncontrado,
    ResultadoEvidencia,
    Vigencia,
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


class FakeEvidencias:
    """Evidencias en memoria. ``veredictos`` se recorre en rotación, una entrada por foto:
    ``ACEPTADA``, ``REVISION_MANUAL`` o un motivo de rechazo (``BORROSA``, ...)."""

    _TIPOS = ("cedula_frente", "cedula_reverso", "selfie")

    def __init__(self, veredictos=("ACEPTADA",), intentos_max: int = 3) -> None:
        self._veredictos = itertools.cycle(tuple(veredictos))
        self._intentos_max = intentos_max
        self._vigentes: dict[tuple[str, str], EstadoEvidenciaTipo] = {}
        self._rechazos: dict[tuple[str, str], int] = {}

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        clave = (cliente_id, tipo)
        veredicto = next(self._veredictos)
        if veredicto not in ("ACEPTADA", "REVISION_MANUAL"):
            rechazos = self._rechazos.get(clave, 0) + 1
            self._rechazos[clave] = rechazos
            if rechazos < self._intentos_max:
                return ResultadoEvidencia(
                    tipo=tipo,
                    estado="REINTENTAR",
                    motivo=veredicto,
                    intentos_restantes=self._intentos_max - rechazos,
                )
            veredicto = "REVISION_MANUAL"  # tercer rechazo: lo revisa una persona
        self._rechazos.pop(clave, None)
        evidencia_id = str(uuid.uuid4())
        self._vigentes[clave] = EstadoEvidenciaTipo(
            tipo=tipo,
            estado=veredicto,
            intentos_restantes=self._intentos_max,
            evidencia_id=evidencia_id,
            creada_en=datetime.now(UTC),
        )
        return ResultadoEvidencia(
            tipo=tipo,
            estado=veredicto,
            evidencia_id=evidencia_id,
            intentos_restantes=self._intentos_max,
        )

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        return [
            self._vigentes.get((cliente_id, tipo))
            or EstadoEvidenciaTipo(
                tipo=tipo,
                estado="PENDIENTE",
                intentos_restantes=self._intentos_max - self._rechazos.get((cliente_id, tipo), 0),
            )
            for tipo in self._TIPOS
        ]


class FakeCotizacion:
    """Rating simplificado, sin llamar a Perfilamiento — solo para modo standalone/pruebas.

    Guarda una sola cotización activa por cliente: crear otra reemplaza la anterior.
    """

    _TASA_MENSUAL = Decimal("0.0005")
    _VIGENCIA = timedelta(days=30)
    _HOGAR_TOPE = Decimal("240000000")
    _HOGAR_TASA_POR_MIL = Decimal("0.099")
    _IVA = Decimal("0.19")

    def __init__(self) -> None:
        self._activas: dict[str, Cotizacion] = {}
        self.ultima_edad: int | None = None

    @classmethod
    def _desglose(cls, datos: DatosCreditoInput, prima_vida: float) -> Desglose:
        suma_hogar = min(cls._HOGAR_TOPE, datos.valor_credito)
        neta = (suma_hogar * cls._HOGAR_TASA_POR_MIL / 1000).quantize(Decimal("1"), ROUND_HALF_UP)
        iva = (neta * cls._IVA).quantize(Decimal("1"), ROUND_HALF_UP)
        return Desglose(
            coberturas=(
                CoberturaPrecio(
                    "VIDA", True, float(datos.saldo_insoluto), prima_vida, 0, prima_vida
                ),
                CoberturaPrecio(
                    "HOGAR", False, float(suma_hogar), float(neta), float(iva), float(neta + iva)
                ),
            )
        )

    async def crear_cotizacion(
        self, cliente_id: str, edad: int, entrada: CotizacionInput
    ) -> Cotizacion:
        self.ultima_edad = edad
        saldo = entrada.datos_credito.saldo_insoluto
        prima = float((saldo * self._TASA_MENSUAL).quantize(Decimal("1")))
        ahora = datetime.now(UTC)
        cotizacion = Cotizacion(
            estado="VIGENTE",
            producto="VIDA_HIPOTECARIO",
            datos_credito=entrada.datos_credito,
            origen_datos="DECLARADO",
            oferta=Oferta(
                prima_mensual=prima,
                prima_base_mensual=prima,
                suma_asegurada=float(saldo),
                cobertura_meses=entrada.datos_credito.plazo_restante_meses,
                moneda="COP",
                personalizado=False,
                desglose=self._desglose(entrada.datos_credito, prima),
                fuentes_no_disponibles=("perfilamiento",),
            ),
            vigencia_cotizacion=Vigencia(desde=ahora, hasta=ahora + self._VIGENCIA),
            creada_en=ahora,
        )
        self._activas[cliente_id] = cotizacion
        return cotizacion

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion:
        cotizacion = self._activas.get(cliente_id)
        if cotizacion is None:
            raise RecursoNoEncontrado("El cliente no tiene una cotización activa")
        return cotizacion

    async def eliminar_cotizacion_activa(self, cliente_id: str) -> None:
        if self._activas.pop(cliente_id, None) is None:
            raise RecursoNoEncontrado("El cliente no tiene una cotización activa")
