# bff-mobile

Backend for Frontend del canal **móvil** (pod en GKE). Hoy cubre la **sesión** del cliente:
inicio de sesión con correo y contraseña, y refresco del token. Mismos endpoints y mismo
contrato que el canal web. Contrato: [`openapi/openapi.yaml`](openapi/openapi.yaml).

| Endpoint | Qué hace |
|---|---|
| `POST /v1/sesiones` | Valida credenciales en Identity Platform, busca al cliente en svc-core y emite la sesión. |
| `POST /v1/sesiones/refresco` | Valida el token de refresco y emite una sesión nueva (sin llamar a otros servicios). |

La sesión es un JWT propio firmado con `MOBILE_SESSION_SECRET`, **distinto al de bff-web**:
un token emitido por un canal no vale en el otro.

## Correr el servicio en local

Requiere Python 3.12+.

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash);  en Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"

uvicorn app.main:app --reload --port 8082
```

- Contrato: <http://localhost:8082/openapi.yaml> · salud: <http://localhost:8082/health>
- En modo `fake` (por defecto) no necesita svc-core ni Identity Platform. Trae un usuario de
  demostración **solo para desarrollo**: `ana.rios@example.com` / `unaClaveSegura1`.

```bash
curl -X POST http://localhost:8082/v1/sesiones -H 'content-type: application/json' \
  -d '{"email":"ana.rios@example.com","password":"unaClaveSegura1"}'
```

### Modos de adaptadores (`MOBILE_ADAPTERS`)

| Valor | Qué hace |
|---|---|
| `fake` (default) | Identity Platform y svc-core simulados en memoria. |
| `http` | Llama a los servicios reales con timeout, reintentos y circuit breaker. |

### Variables de entorno (prefijo `MOBILE_`)

| Variable | Default | Notas |
|---|---|---|
| `MOBILE_ADAPTERS` | `fake` | `fake` \| `http` |
| `MOBILE_CORE_BASE_URL` | `http://localhost:8080` | svc-core (modo `http`) |
| `MOBILE_FAKE_DEMO_EMAIL` / `MOBILE_FAKE_DEMO_PASSWORD` | — | usuario de demostración del modo `fake` (ambos o ninguno) |
| `MOBILE_IDENTITY_BASE_URL` / `MOBILE_IDENTITY_API_KEY` | — | Identity Platform (modo `http`); la clave se monta como archivo en `/var/secrets` |
| `MOBILE_SESSION_SECRET` | `dev-only-change-me` | firma del JWT de sesión; **cambiar fuera de local** |
| `MOBILE_SESSION_TTL_SECONDS` / `MOBILE_REFRESH_TTL_SECONDS` | `3600` / `86400` | vigencia de acceso y refresco |
| `MOBILE_HTTP_TIMEOUT_SECONDS` | `0.7` | timeout duro por dependencia |
| `MOBILE_HTTP_RETRIES` | `2` | reintentos ante fallo transitorio |
| `MOBILE_CIRCUIT_FAIL_MAX` | `5` | fallos antes de abrir el circuito |
| `MOBILE_EVIDENCIA_MAX_BYTES` | `3145728` | peso máximo de una foto de identidad (3 MB); lo que pase se rechaza con 413 antes de procesarlo |
| `MOBILE_EVIDENCIAS_TIMEOUT_SECONDS` | `5.0` | timeout de la subida a svc-core (incluye su análisis); las subidas **no se reintentan** |
| `MOBILE_OTEL_ENABLED` | `false` | trazas/métricas/logs hacia Alloy (DI-008) |

### Evidencias de identidad (cédula y selfie, BITS-275)

`POST /v1/cuenta/evidencias-identidad` (multipart: `tipo` ∈ `cedula_frente`, `cedula_reverso`, `selfie` e `imagen`)
sube **una foto** y devuelve el veredicto de svc-core: `201` si se guarda (`ACEPTADA` o `REVISION_MANUAL`), `200` con
`motivo` si hay que repetirla. `GET /v1/cuenta/evidencias-identidad` devuelve el estado de los tres tipos. El `clienteId`
sale de la sesión. El BFF no decide nada: reenvía la foto y descarta de entrada lo vacío o lo que supera el máximo.
Con `MOBILE_ADAPTERS=fake` el veredicto siempre es `ACEPTADA`; para ver los demás estados, correr contra svc-core con
`CORE_VERIFICADOR_BACKEND=mock` y `CORE_VERIFICADOR_MOCK_SECUENCIA` (ver el README de svc-core).

### Dispositivos para avisos push (BITS-229)

`PUT /v1/cuenta/dispositivos` (`{token, plataforma: "android", versionApp}`) registra el teléfono y
devuelve `{dispositivoId}`; `DELETE /v1/cuenta/dispositivos/{dispositivoId}` lo da de baja (`204`,
idempotente). El `clienteId` sale de la sesión y svc-core guarda los tokens. Ambas se reintentan
como las lecturas porque son idempotentes. El token no se escribe en los logs.

## Pruebas

```bash
pytest --cov=app        # gate de cobertura: 80 %
ruff check . && ruff format --check .
```
