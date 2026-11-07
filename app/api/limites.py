"""Límite de tamaño de las subidas de fotos, antes de procesar el cuerpo."""

from __future__ import annotations

from fastapi import FastAPI, Request

from app.api.errors import problema

RUTA_SUBIDAS = "/v1/cuenta/evidencias-identidad"
# El reporte de un siniestro lleva hasta MAX_ARCHIVOS_SINIESTRO imágenes en un solo envío.
RUTA_SINIESTROS = "/v1/cuenta/siniestros"
MAX_ARCHIVOS_SINIESTRO = 10
# El multipart agrega nombres de campo y delimitadores al peso de la imagen.
HOLGURA_MULTIPART = 64 * 1024


def install_limite_de_subidas(app: FastAPI, max_bytes: int) -> None:
    # Ruta -> (límite del cuerpo, mensaje). Una foto, o hasta MAX_ARCHIVOS_SINIESTRO en el reporte.
    limites = {
        RUTA_SUBIDAS: (
            max_bytes + HOLGURA_MULTIPART,
            f"La imagen supera el máximo de {max_bytes} bytes",
        ),
        RUTA_SINIESTROS: (
            max_bytes * MAX_ARCHIVOS_SINIESTRO + HOLGURA_MULTIPART,
            f"Cada imagen puede pesar hasta {max_bytes} bytes y el reporte lleva a lo sumo "
            f"{MAX_ARCHIVOS_SINIESTRO}",
        ),
    }

    @app.middleware("http")
    async def _limitar_subidas(request: Request, call_next):
        if request.method == "POST":
            for ruta, (limite, detalle) in limites.items():
                if request.url.path.endswith(ruta):
                    largo = request.headers.get("content-length", "")
                    if largo.isdigit() and int(largo) > limite:
                        return problema(
                            413,
                            "Imagen demasiado grande",
                            detail=detalle,
                            instance=str(request.url),
                        )
                    break
        return await call_next(request)
