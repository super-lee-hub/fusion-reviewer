from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .providers import ProviderRegistry
from .service import JobService


def create_app() -> FastAPI:
    app = FastAPI(title="fusion-reviewer")
    service = JobService()
    registry = ProviderRegistry()
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {"profiles": registry.names(), "recent_jobs": service.list_jobs(limit=20)},
        )

    @app.post("/ui/jobs")
    async def create_job_from_form(
        paper: UploadFile = File(...),
        title: str | None = Form(default=None),
        provider_profile: str | None = Form(default=None),
        mode: str = Form(default="backend"),
        journal_text: str | None = Form(default=None),
        journal_file: UploadFile | None = File(default=None),
        revision_text: str | None = Form(default=None),
        revision_file: UploadFile | None = File(default=None),
    ):
        job = service.submit_document(
            file_bytes=await paper.read(),
            filename=paper.filename or "paper.pdf",
            title=title,
            provider_override=provider_profile or None,
            mode=mode,
            journal_text=journal_text,
            journal_file_bytes=await journal_file.read() if journal_file and journal_file.filename else None,
            journal_filename=journal_file.filename if journal_file and journal_file.filename else None,
            revision_text=revision_text,
            revision_file_bytes=await revision_file.read() if revision_file and revision_file.filename else None,
            revision_filename=revision_file.filename if revision_file and revision_file.filename else None,
        )
        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    @app.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
    async def job_view(request: Request, job_id: str):
        job = service.get_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse(request, "job.html", {"job": job, "job_id": job_id})

    @app.get("/ui/jobs/{job_id}/panel", response_class=HTMLResponse)
    async def job_panel(request: Request, job_id: str):
        job = service.get_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse(
            request,
            "job_panel.html",
            {"job": job, "result": service.result(job_id), "artifacts": service.artifacts(job_id)},
        )

    @app.post("/jobs")
    async def create_job(
        paper: UploadFile = File(...),
        title: str | None = Form(default=None),
        provider_profile: str | None = Form(default=None),
        mode: str = Form(default="backend"),
        journal_text: str | None = Form(default=None),
        journal_file: UploadFile | None = File(default=None),
        revision_text: str | None = Form(default=None),
        revision_file: UploadFile | None = File(default=None),
    ):
        job = service.submit_document(
            file_bytes=await paper.read(),
            filename=paper.filename or "paper.pdf",
            title=title,
            provider_override=provider_profile or None,
            mode=mode,
            journal_text=journal_text,
            journal_file_bytes=await journal_file.read() if journal_file and journal_file.filename else None,
            journal_filename=journal_file.filename if journal_file and journal_file.filename else None,
            revision_text=revision_text,
            revision_file_bytes=await revision_file.read() if revision_file and revision_file.filename else None,
            revision_filename=revision_file.filename if revision_file and revision_file.filename else None,
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        job = service.get_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JSONResponse(job.model_dump(mode="json"))

    @app.get("/jobs/{job_id}/artifacts")
    async def get_artifacts(job_id: str):
        try:
            return JSONResponse(service.artifacts(job_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}/reviews/{agent_id}")
    async def get_review(job_id: str, agent_id: str):
        try:
            return JSONResponse(service.get_review(job_id, agent_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/providers/health")
    async def providers_health():
        return JSONResponse({"profiles": registry.health_report()})

    @app.get("/downloads/{job_id}/final-report.pdf")
    async def download_final_pdf(job_id: str):
        job = service.get_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        artifacts = service.artifacts(job_id)
        record = artifacts["artifacts"].get("final_report.pdf")
        if not record:
            raise HTTPException(status_code=404, detail="PDF artifact not found")
        download_name = f"{job.run_label or job_id}__审稿总报告.pdf"
        return FileResponse(record["path"], filename=download_name)

    return app
