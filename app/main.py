"""WanderAI Backend application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import internal_router, router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

# Initialize logging before anything else
setup_logging()
logger = get_logger(__name__)

APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    settings = get_settings()
    logger.info(
        "WanderAI Backend starting",
        extra={"env": settings.app_env, "url": settings.public_api_url},
    )

    # Clean up temp files older than 24 hours on startup
    from app.services.temp_storage_service import cleanup_old_temp_files
    cleaned = cleanup_old_temp_files()
    if cleaned:
        logger.info("Startup cleanup completed", extra={"removed": cleaned})

    yield

    logger.info("WanderAI Backend shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="WanderAI Backend",
        description="Travel podcast generation API",
        version=APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware
    if settings.cors_origins == "*":
        origins = ["*"]
    else:
        origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": "WanderAI Backend",
            "status": "healthy",
            "version": APP_VERSION,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
        }

    # Include routers
    app.include_router(router)
    app.include_router(internal_router)

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception",
            extra={
                "path": request.url.path,
                "method": request.method,
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
