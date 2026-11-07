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


def _es_envio_de_siniestro(ruta: str) -> bool:
    """El reporte o el envío de lo que falta de un siniestro."""
    return ruta.endswith(RUTA_SINIESTROS) or (
        RUTA_SINIESTROS + "/" in ruta and ruta.endswith("/evidencias")
    )


def install_limite_de_subidas(app: FastAPI, max_bytes: int) -> None:
    @app.middleware("http")
    async def _limitar_subidas(request: Request, call_next):
        if request.method == "POST":
            ruta = request.url.path
            if ruta.endswith(RUTA_SUBIDAS):
                limite = max_bytes + HOLGURA_MULTIPART
                detalle = f"La imagen supera el máximo de {max_bytes} bytes"
            elif _es_envio_de_siniestro(ruta):
                # Una foto por archivo, hasta MAX_ARCHIVOS_SINIESTRO en un solo envío.
                limite = max_bytes * MAX_ARCHIVOS_SINIESTRO + HOLGURA_MULTIPART
                detalle = (
                    f"Cada imagen puede pesar hasta {max_bytes} bytes y el envío lleva a lo "
                    f"sumo {MAX_ARCHIVOS_SINIESTRO}"
                )
            else:
                return await call_next(request)
            largo = request.headers.get("content-length", "")
            if largo.isdigit() and int(largo) > limite:
                return problema(
                    413,
                    "Imagen demasiado grande",
                    detail=detalle,
                    instance=str(request.url),
                )
        return await call_next(request)
