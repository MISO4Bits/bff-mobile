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
    Conflicto,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    DatosCreditoInput,
    Desglose,
    DocumentoLegal,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    HipotecaReportada,
    NoAutorizado,
    Oferta,
    RecursoNoEncontrado,
    ReglaNegocio,
    ResultadoEvidencia,
    Vigencia,
)

MENSAJE_CEDULA_INCOMPLETA = (
    "Para subir la selfie primero deben estar vigentes ambas caras de la cédula"
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
        if tipo == "selfie" and not all(
            (cliente_id, cara) in self._vigentes for cara in ("cedula_frente", "cedula_reverso")
        ):
            # Como svc-core: la selfie se compara con la cédula, que debe estar ya vigente.
            raise ReglaNegocio(MENSAJE_CEDULA_INCOMPLETA)
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


class FakePolizas:
    """Pólizas en memoria con la forma que entrega svc-core. ``pendientes`` define qué falta:
    vacío, la póliza sale emitida; si no, ``PENDIENTE`` con esos códigos."""

    _PRECIOS = {"VIDA": (140000000, 24696, 0), "HOGAR": (150000000, 14850, 2822)}
    _MENSAJES = {
        "CORREO_SIN_CONFIRMAR": ("USUARIO", "Confirma tu cuenta con el enlace que enviamos."),
        "EVIDENCIA_FALTANTE": ("USUARIO", "Sube la evidencia de identidad que falta."),
        "EVIDENCIA_EN_REVISION": ("AGENTE", "Un agente está revisando tus documentos."),
        "CREDITO_NO_VERIFICADO": ("AGENTE", "Un agente debe verificar los datos de tu crédito."),
    }

    def __init__(self, pendientes: tuple[str, ...] = ()) -> None:
        self.pendientes = pendientes
        self.documento_listo = True
        self.llamadas: list[tuple] = []
        self._polizas: dict[str, dict] = {}

    def _poliza(self, cliente_id: str, cuerpo: dict) -> dict:
        ahora = datetime.now(UTC).isoformat()
        coberturas = [
            {
                "codigo": codigo,
                "obligatoria": codigo == "VIDA",
                "sumaAsegurada": self._PRECIOS[codigo][0],
                "primaNeta": self._PRECIOS[codigo][1],
                "iva": self._PRECIOS[codigo][2],
                "primaTotal": self._PRECIOS[codigo][1] + self._PRECIOS[codigo][2],
            }
            for codigo in ("VIDA", "HOGAR")
            if codigo in cuerpo["coberturas"]
        ]
        emitida = not self.pendientes
        return {
            "polizaId": str(uuid.uuid4()),
            "clienteId": cliente_id,
            "numero": "SOL-VH-2026-FAKE01" if emitida else None,
            "estado": "EMITIDA" if emitida else "PENDIENTE",
            "producto": "VIDA_HIPOTECARIO",
            "creadaEn": ahora,
            "emitidaEn": ahora if emitida else None,
            "solicitudVigenteHasta": None if emitida else ahora,
            "vigencia": {"desde": ahora, "hasta": ahora} if emitida else None,
            "coberturas": coberturas,
            "primaTotalMensual": sum(c["primaTotal"] for c in coberturas),
            "moneda": "COP",
            "datosCredito": {
                "valorCredito": 150000000,
                "plazoRestanteMeses": 240,
                "entidadAcreedora": "Bancolombia",
                "saldoInsoluto": 140000000,
            },
            "origenDatos": "CONFIRMADO",
            "cotizacionCreadaEn": cuerpo["cotizacionCreadaEn"],
            "pendientes": [
                {"codigo": c, "tipo": self._MENSAJES[c][0], "mensaje": self._MENSAJES[c][1]}
                for c in self.pendientes
            ],
            "documento": {"estado": "DISPONIBLE" if emitida else "NO_DISPONIBLE"},
            "documentosAceptados": [
                {**d, "aceptadoEn": ahora} for d in cuerpo["documentosAceptados"]
            ],
            "rechazo": None,
        }

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        self.llamadas.append(("solicitar", cliente_id, cuerpo))
        for existente in self._polizas.values():
            if (
                existente["clienteId"] == cliente_id
                and existente["cotizacionCreadaEn"] == cuerpo["cotizacionCreadaEn"]
            ):
                return existente, False
        poliza = self._poliza(cliente_id, cuerpo)
        self._polizas[poliza["polizaId"]] = poliza
        return poliza, True

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]:
        return [
            p
            for p in self._polizas.values()
            if p["clienteId"] == cliente_id and (estado is None or p["estado"] == estado)
        ]

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        poliza = self._polizas.get(poliza_id)
        if poliza is None or poliza["clienteId"] != cliente_id:
            raise RecursoNoEncontrado("Póliza no encontrada")
        return poliza

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        poliza = await self.obtener(cliente_id, poliza_id)
        if poliza["estado"] != "EMITIDA":
            raise Conflicto("La póliza todavía no está emitida", codigo="POLIZA_NO_EMITIDA")
        if not self.documento_listo:
            return {"estado": "GENERANDO", "url": None, "expiraEn": None}, False
        return {
            "estado": "LISTO",
            "url": f"https://documentos.local/{poliza['numero']}.pdf",
            "expiraEn": datetime.now(UTC).isoformat(),
        }, True


_DOCUMENTOS_LEGALES_DE_EJEMPLO = (
    DocumentoLegal(
        tipo="terminos",
        version="V1",
        titulo="Términos y condiciones",
        subtitulo="Las reglas de uso de Solventa. Está corto a propósito.",
        base_legal="Ley 527 de 1999",
        contenido=(
            "<h3>1 · Quién te presta el servicio</h3>"
            "<p>Solventa Colombia S.A.S. Somos intermediarios de seguros.</p>"
            "<h3>2 · Tu firma electrónica</h3>"
            "<p>Cuando aceptas desde tu cuenta, esa aceptación vale como tu firma.</p>"
        ),
        nota_pie="Aceptar estos términos no te compromete a comprar.",
    ),
    DocumentoLegal(
        tipo="open-data",
        version="V1",
        titulo="Tratamiento de datos personales",
        subtitulo="Qué datos tuyos usamos y para qué. Ley 1581 de 2012.",
        base_legal="Ley 1581 de 2012",
        contenido=(
            "<h3>1 · Qué datos recogemos</h3>"
            "<p>Tu nombre, documento, fecha de nacimiento, correo y celular.</p>"
            "<h3>2 · Tus derechos</h3>"
            "<p>Conocer, actualizar, corregir y borrar tus datos, o revocar esta autorización.</p>"
        ),
        nota_pie="Por ley del sector conservamos tu información mientras tengas póliza.",
    ),
    DocumentoLegal(
        tipo="open-finance",
        version="V1",
        titulo="Consulta en centrales de riesgo",
        subtitulo="Es opcional. Si la das, tu precio puede bajar.",
        base_legal="Ley 1266 de 2008",
        contenido=(
            "<h3>1 · Qué autorizas</h3>"
            "<p>Consultar y reportar tu comportamiento crediticio ante los operadores.</p>"
            "<h3>2 · Puedes retirarla</h3>"
            "<p>Quitas esta autorización cuando quieras desde tu perfil.</p>"
        ),
        nota_pie="Es opcional. Si no la autorizas puedes seguir igual.",
    ),
    DocumentoLegal(
        tipo="poliza",
        version="V1",
        titulo="Condiciones de tu póliza",
        subtitulo="Lo que contratas, en pocas líneas. Léelo antes de solicitar.",
        base_legal="Código de Comercio (contrato de seguro) y Ley 527 de 1999",
        contenido=(
            "<h3>1 · Qué contratas</h3>"
            "<p>Un seguro de vida deudores asociado a tu crédito de vivienda.</p>"
            "<h3>2 · Tu firma electrónica</h3>"
            "<p>Al aceptar desde tu cuenta, esa aceptación vale como tu firma.</p>"
        ),
        nota_pie="Solicitar la póliza no te cobra nada todavía.",
    ),
)


class FakeDocumentosLegales:
    """Textos de ejemplo abreviados (mercado ``CO``, idioma ``es-CO``) para correr
    el BFF standalone. Los textos reales viven en svc-productos."""

    def __init__(self, documentos: tuple[DocumentoLegal, ...] = _DOCUMENTOS_LEGALES_DE_EJEMPLO):
        self._documentos = documentos

    @staticmethod
    def _hay_textos(mercado: str, idioma: str) -> bool:
        return (mercado, idioma) == ("CO", "es-CO")

    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        if not self._hay_textos(mercado, idioma):
            return []
        if tipo is not None:
            return [d for d in self._documentos if d.tipo == tipo]
        return [d for d in self._documentos if d.tipo != "poliza"]

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        if self._hay_textos(mercado, idioma):
            for documento in self._documentos:
                if (documento.tipo, documento.version) == (tipo, version):
                    return documento
        raise RecursoNoEncontrado("Documento legal no encontrado")


class FakeDispositivos:
    """Dispositivos en memoria con la forma que entrega svc-core: el mismo token da el mismo id."""

    def __init__(self) -> None:
        self.llamadas: list[tuple] = []
        self._ids: dict[str, str] = {}

    async def registrar_dispositivo(self, cliente_id: str, cuerpo: dict) -> dict:
        self.llamadas.append(("registrar", cliente_id, cuerpo))
        return {"dispositivoId": self._ids.setdefault(cuerpo["token"], str(uuid.uuid4()))}

    async def quitar_dispositivo(self, cliente_id: str, dispositivo_id: str) -> None:
        self.llamadas.append(("quitar", cliente_id, dispositivo_id))
