from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import get_settings, project_root
from .db import get_engine
from .errors import error_envelope, http_exception_handler
from .health import health_detail
from .localization import LANGUAGE_HEADER, OUTPUT_LANGUAGE_HEADER, language_context, normalize_language, resolve_output_language
from .release_info import release_info
from .routers import admin, assistant, generation, history, import_routes, learners, learning, mastery_variants, practice, questions, recovery, recovery_agent, reports, settings, tasks
from .security import parse_allowed_origins, unsafe_browser_request_rejection
from .sqlite_dev_schema import ensure_sqlite_dev_schema
from .structured_logging import configure_request_logger, elapsed_ms, log_request_entry, request_log_entry, request_log_path, trace_id_for_request


def create_app() -> FastAPI:
    app_settings = get_settings()
    ensure_sqlite_dev_schema(get_engine(app_settings.database_url))
    app = FastAPI(title=app_settings.app_name)
    request_logger = configure_request_logger(
        path=request_log_path(app_settings.api_request_log_path),
        max_bytes=app_settings.api_request_log_max_bytes,
        backup_count=app_settings.api_request_log_backup_count,
        retention_days=app_settings.api_request_log_retention_days,
    )
    release_summary = release_info(project_root())
    app_version = str(release_summary.get("app_version") or "")
    app.state.app_version = app_version
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    allowed_origins = parse_allowed_origins(app_settings.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def local_browser_write_guard(request: Request, call_next):
        started_at = perf_counter()
        trace_id = trace_id_for_request(request)
        request.state.trace_id = trace_id
        status_code = 500
        error_code = None
        rejection = unsafe_browser_request_rejection(
            method=request.method,
            path=request.url.path,
            headers=request.headers,
            allowed_origins=allowed_origins,
        )
        if rejection:
            status_code = 403
            error_code = rejection.error_code
            payload = error_envelope(status_code=403, detail={"error_code": rejection.error_code, "detail": rejection.detail}, trace_id=trace_id)
            response = JSONResponse(payload, status_code=403, headers={"X-Trace-Id": trace_id})
            log_request_entry(
                request_logger,
                request_log_entry(
                    request=request,
                    trace_id=trace_id,
                    status_code=status_code,
                    duration_ms=elapsed_ms(started_at),
                    slow_request_ms=app_settings.api_slow_request_ms,
                    app_version=app_version,
                    error_code=error_code,
                ),
            )
            return response
        try:
            response = await call_next(request)
            status_code = response.status_code
            error_code = response.headers.get("X-Error-Code") if status_code >= 400 else None
            response.headers["X-Trace-Id"] = trace_id
            log_request_entry(
                request_logger,
                request_log_entry(
                    request=request,
                    trace_id=trace_id,
                    status_code=status_code,
                    duration_ms=elapsed_ms(started_at),
                    slow_request_ms=app_settings.api_slow_request_ms,
                    app_version=app_version,
                    error_code=error_code,
                ),
            )
            return response
        except Exception as exc:
            log_request_entry(
                request_logger,
                request_log_entry(
                    request=request,
                    trace_id=trace_id,
                    status_code=status_code,
                    duration_ms=elapsed_ms(started_at),
                    slow_request_ms=app_settings.api_slow_request_ms,
                    app_version=app_version,
                    error_code="unhandled_exception",
                    error_class=exc.__class__.__name__,
                ),
            )
            raise

    @app.middleware("http")
    async def request_language_context(request: Request, call_next):
        ui_language = normalize_language(request.headers.get(LANGUAGE_HEADER))
        explicit = None
        if request.method in {"POST", "PUT", "PATCH"} and "application/json" in request.headers.get("content-type", ""):
            try:
                body = await request.json()
                if isinstance(body, dict):
                    explicit = body.get("output_language")
            except ValueError:
                pass
        with language_context(ui_language, ui_language=ui_language):
            try:
                output = resolve_output_language(explicit, request.headers.get(OUTPUT_LANGUAGE_HEADER), ui_language)
            except ValueError:
                return JSONResponse(error_envelope(status_code=422, detail={"error_code": "UNSUPPORTED_OUTPUT_LANGUAGE", "message_key": "errors.unsupported_output_language.message", "message_params": {}}), status_code=422)
            with language_context(output):
                request.state.language = output
                request.state.output_language = output
                request.state.ui_language = ui_language
                response = await call_next(request)
                response.headers[LANGUAGE_HEADER] = ui_language
                response.headers[OUTPUT_LANGUAGE_HEADER] = output
                return response

    app.include_router(import_routes.router)
    app.include_router(learners.router)
    app.include_router(admin.router)
    app.include_router(questions.router)
    app.include_router(practice.router)
    app.include_router(learning.router)
    app.include_router(generation.router)
    app.include_router(generation.batch_router)
    app.include_router(mastery_variants.router)
    app.include_router(history.router)
    app.include_router(reports.router)
    app.include_router(assistant.router)
    app.include_router(settings.router)
    app.include_router(recovery.router)
    app.include_router(recovery_agent.router)
    app.include_router(tasks.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/details")
    def health_details() -> dict:
        settings_snapshot = get_settings()
        with Session(get_engine(settings_snapshot.database_url)) as db:
            return health_detail(db, project_root(), settings_snapshot)

    @app.get("/api/health/doctor")
    def health_doctor() -> dict:
        settings_snapshot = get_settings()
        with Session(get_engine(settings_snapshot.database_url)) as db:
            return health_detail(db, project_root(), settings_snapshot)

    return app


app = create_app()
