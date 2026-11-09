from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.api.integration import router as integration_router, fridge_app
app = FastAPI(
    title="CookQuest + Gastrolabe",
    version="1.0.0",
    description="Fridge recognition, recipe discovery and a personal culinary exploration map.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)
app.include_router(integration_router)
app.mount("/fridge", fridge_app(), name="fridge")
from app.gastrolabe.app import create_app as gastrolabe_app
from starlette.middleware.wsgi import WSGIMiddleware
app.mount("/gastrolabe", WSGIMiddleware(gastrolabe_app()), name="gastrolabe")


@app.middleware("http")
async def disable_demo_cache(request: Request, call_next):
    response = await call_next(request)
    from app.config import get_settings
    if not get_settings().cache_enabled or request.url.path == "/" or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        error = detail
    else:
        error = {"code": "http_error", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": "Request validation failed."}},
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # HTTPException remains handled by FastAPI's more specific handler.
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Unexpected server error."}})


static_dir = Path(__file__).resolve().parent / "frontend"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="demo")

