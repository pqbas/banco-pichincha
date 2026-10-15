"""Crea la instancia de FastAPI y registra los routers."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import logging  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.routers import health, onboarding  # noqa: E402

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Onboarding de clientes")

# El front se sirve desde otro dominio (S3)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["content-type"])

app.include_router(health.router)
app.include_router(onboarding.router)

# En local el front queda en /ui, en Lambda no se empaqueta
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/ui", StaticFiles(directory=FRONTEND, html=True), name="ui")
