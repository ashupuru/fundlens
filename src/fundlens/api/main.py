from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from fundlens.api.routers import (
    allocation,
    assumptions,
    benchmark,
    data_quality,
    dezerv,
    funds,
    health,
    ingestion,
    market_data,
    portfolio,
    reports,
    verdict,
)
from fundlens.db.migrate import run_migrations
from fundlens.db.session import get_engine
from fundlens.market_data.service import refresh_all_held_schemes
from fundlens.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    run_migrations()
    with Session(get_engine()) as session:
        refresh_all_held_schemes(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="FundLens API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(ingestion.router)
    app.include_router(dezerv.router)
    app.include_router(funds.router)
    app.include_router(market_data.router)
    app.include_router(portfolio.router)
    app.include_router(reports.router)
    app.include_router(data_quality.router)
    app.include_router(benchmark.router)
    app.include_router(assumptions.router)
    app.include_router(allocation.router)
    app.include_router(verdict.router)
    return app


app = create_app()
