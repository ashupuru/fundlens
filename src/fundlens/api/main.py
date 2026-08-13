from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fundlens.api.routers import (
    allocation,
    assumptions,
    benchmark,
    data_quality,
    health,
    ingestion,
    market_data,
    portfolio,
    reports,
)
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
    app.include_router(market_data.router)
    app.include_router(portfolio.router)
    app.include_router(reports.router)
    app.include_router(data_quality.router)
    app.include_router(benchmark.router)
    app.include_router(assumptions.router)
    app.include_router(allocation.router)
    return app


app = create_app()
