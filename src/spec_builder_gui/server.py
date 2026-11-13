"""Local aiohttp backend for the spec builder GUI."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any
from aiohttp import web
from src.spec_builder_gui.api_models import (
    CreateJobRequest,
    CreateRevisionRequest,
    SubmitReviewRequest,
)
from src.spec_builder_gui.job_store import GuiJobStore
from src.spec_builder_gui.service import (
    GuiServiceError,
    SpecBuilderGuiService,
    TemporalSpecBuilderGateway,
)

DEFAULT_FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
SERVICE_APP_KEY: web.AppKey[SpecBuilderGuiService] = web.AppKey("service")
FRONTEND_DIST_APP_KEY: web.AppKey[Path] = web.AppKey("frontend_dist_dir")


def model_payload(value: Any) -> Any:
    """Convert pydantic models into JSON-safe payloads."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def json_response(value: Any, *, status: int = 200) -> web.Response:
    """Return a JSON response from a pydantic model or plain payload."""
    return web.json_response(model_payload(value), status=status)


@web.middleware
async def error_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Translate known backend errors into JSON responses."""
    try:
        return await handler(request)
    except GuiServiceError as exc:
        return json_response({"error": str(exc)}, status=exc.status_code)


def frontend_dist_exists(frontend_dist_dir: Path) -> bool:
    """Return whether the compiled frontend assets exist."""
    return frontend_dist_dir.joinpath("index.html").exists()


async def serve_frontend(request: web.Request) -> web.StreamResponse:
    """Serve the compiled frontend or a local setup hint."""
    frontend_dist_dir = request.app[FRONTEND_DIST_APP_KEY]
    index_path = frontend_dist_dir / "index.html"
    if index_path.exists():
        return web.FileResponse(index_path)
    return web.Response(
        text=(
            "The React frontend has not been built yet.\n\n"
            "Run `npm install` and `npm run build` inside "
            "`src/spec_builder_gui/frontend/`, then reload this page."
        ),
        content_type="text/plain",
    )


async def get_service(request: web.Request) -> SpecBuilderGuiService:
    """Return the application service instance."""
    return request.app[SERVICE_APP_KEY]


async def upload_artifact(request: web.Request) -> web.Response:
    """Handle artifact upload requests."""
    service = await get_service(request)
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.filename is None:
        raise GuiServiceError("Multipart field `file` is required.")
    payload = await part.read(decode=False)
    uploaded = await service.upload_artifact(
        filename=part.filename,
        content=payload,
        mime_type=part.headers.get("Content-Type"),
    )
    return json_response(uploaded, status=201)


async def create_job(request: web.Request) -> web.Response:
    """Handle job creation requests."""
    service = await get_service(request)
    payload = CreateJobRequest.model_validate(await request.json())
    created = await service.create_job(payload)
    return json_response(created, status=201)


async def list_jobs(request: web.Request) -> web.Response:
    """Handle list jobs requests."""
    service = await get_service(request)
    status = request.query.get("status")
    search = request.query.get("search")
    page = int(request.query.get("page", "1"))
    page_size = int(request.query.get("pageSize", "20"))
    result = await service.list_jobs(
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )
    return json_response(result)


async def get_job_detail(request: web.Request) -> web.Response:
    """Handle get job detail requests."""
    service = await get_service(request)
    detail = await service.get_job_detail(request.match_info["job_id"])
    return json_response(detail)


async def submit_review(request: web.Request) -> web.Response:
    """Handle review requests for active jobs."""
    service = await get_service(request)
    payload = SubmitReviewRequest.model_validate(await request.json())
    detail = await service.submit_review(request.match_info["job_id"], payload)
    return json_response(detail)


async def create_revision(request: web.Request) -> web.Response:
    """Handle revision creation requests."""
    service = await get_service(request)
    payload = CreateRevisionRequest.model_validate(await request.json())
    created = await service.create_revision(request.match_info["job_id"], payload)
    return json_response(created, status=201)


async def approve_revision_as_final(request: web.Request) -> web.Response:
    """Handle closed revision approval requests."""
    service = await get_service(request)
    detail = await service.approve_revision_as_final(request.match_info["job_id"])
    return json_response(detail)


async def download_spec(request: web.Request) -> web.StreamResponse:
    """Handle final markdown downloads."""
    service = await get_service(request)
    path, filename = await service.get_download_path(request.match_info["job_id"])
    response = web.FileResponse(path)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


async def get_features(request: web.Request) -> web.Response:
    """Handle system feature discovery requests."""
    service = await get_service(request)
    return json_response(service.get_features())


async def pick_output_directory(request: web.Request) -> web.Response:
    """Handle optional native directory picker requests."""
    service = await get_service(request)
    picked = await service.pick_output_directory()
    return json_response(picked)


def create_app(
    *,
    service: SpecBuilderGuiService | None = None,
    frontend_dist_dir: Path | None = None,
) -> web.Application:
    """Create the aiohttp application."""
    app = web.Application(
        middlewares=[error_middleware],
        client_max_size=30 * 1024 * 1024,
    )
    app[SERVICE_APP_KEY] = service
    app[FRONTEND_DIST_APP_KEY] = frontend_dist_dir or DEFAULT_FRONTEND_DIST

    async def startup(application: web.Application) -> None:
        if application[SERVICE_APP_KEY] is None:
            gateway = await TemporalSpecBuilderGateway.connect()
            application[SERVICE_APP_KEY] = SpecBuilderGuiService(
                job_store=GuiJobStore(),
                temporal_gateway=gateway,
            )

    app.on_startup.append(startup)
    app.router.add_post("/api/artifacts", upload_artifact)
    app.router.add_post("/api/jobs", create_job)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_get("/api/jobs/{job_id}", get_job_detail)
    app.router.add_post("/api/jobs/{job_id}/review", submit_review)
    app.router.add_post("/api/jobs/{job_id}/revise", create_revision)
    app.router.add_post("/api/jobs/{job_id}/approve-revision", approve_revision_as_final)
    app.router.add_get("/api/jobs/{job_id}/download", download_spec)
    app.router.add_get("/api/system/features", get_features)
    app.router.add_post("/api/system/pick-output-directory", pick_output_directory)

    frontend_dir = app[FRONTEND_DIST_APP_KEY]
    assets_dir = frontend_dir / "assets"
    if assets_dir.exists():
        app.router.add_static("/assets", assets_dir)
    app.router.add_get("/", serve_frontend)
    app.router.add_get("/{tail:.*}", serve_frontend)
    return app


async def main() -> None:
    """Run the local GUI backend."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8080)
    await site.start()
    print("Spec Builder GUI available at http://127.0.0.1:8080")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
