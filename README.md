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
| `MOBILE_IDENTITY_BASE_URL` / `MOBILE_IDENTITY_API_KEY` | — | Identity Platform (modo `http`); la clave se monta como archivo en `/var/secrets` |
| `MOBILE_SESSION_SECRET` | `dev-only-change-me` | firma del JWT de sesión; **cambiar fuera de local** |
| `MOBILE_SESSION_TTL_SECONDS` / `MOBILE_REFRESH_TTL_SECONDS` | `3600` / `86400` | vigencia de acceso y refresco |
| `MOBILE_HTTP_TIMEOUT_SECONDS` | `0.7` | timeout duro por dependencia |
| `MOBILE_HTTP_RETRIES` | `2` | reintentos ante fallo transitorio |
| `MOBILE_CIRCUIT_FAIL_MAX` | `5` | fallos antes de abrir el circuito |
| `MOBILE_OTEL_ENABLED` | `false` | trazas/métricas/logs hacia Alloy (DI-008) |

## Pruebas

```bash
pytest --cov=app        # gate de cobertura: 80 %
ruff check . && ruff format --check .
```
