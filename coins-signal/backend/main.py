from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import init_db
from routers import health, signals


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Coins Signal API",
    description="Public Donchian signal feed (equity % only, no volume)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(signals.router)
