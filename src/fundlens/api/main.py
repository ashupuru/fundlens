from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fundlens.api.routers import health, ingestion
from fundlens.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="FundLens API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(ingestion.router)
    return app


app = create_app()
