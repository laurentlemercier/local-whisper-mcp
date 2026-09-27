import contextlib
import json
import logging
import shutil

from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    FastAPI,
    Depends,
    File,
    Header,
    HTTPException,
    UploadFile,
)

from .config import INPUT_DIR, JOBS_DIR, MAX_INPUT_SIZE
from .jobs import JobManager
from .models import *
from .security import (
    require_api_token,
    validate_callback_url,
    validate_source_url,
)
from .source import *
from .whisper import ModelManager, Transcriber
from .worker import Worker


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("speech.main")


# ============================================================
# Application components
# ============================================================

manager = JobManager(JOBS_DIR)
models = ModelManager()
transcriber = Transcriber(models)
worker = Worker(
    manager,
    transcriber,
    INPUT_DIR,
)


# ============================================================
# FastAPI lifespan
#
# IMPORTANT :
# - mcp.py importe manager et create_job depuis main.py
# - il ne faut donc PAS importer mcp au niveau du module
#   avant que main.py ait terminé son initialisation.
#
# L'import de mcp est volontairement différé ici.
# ============================================================

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from .mcp import mcp

    logger.info("Starting background transcription worker")
    worker.start()

    logger.info("Starting MCP Streamable HTTP session manager")

    try:
        async with mcp.session_manager.run():
            yield
    finally:
        logger.info("Application shutdown")


# ============================================================
# FastAPI application
# ============================================================

app = FastAPI(
    title="Local Speech-to-Text Service",
    version="2.3.0",
    lifespan=lifespan,
)


# ============================================================
# Authentication
# ============================================================

def auth(
    authorization: str | None = Header(default=None),
):
    require_api_token(authorization)


# ============================================================
# Health
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "speech-to-text",
        "version": "2.3.0",
    }


# ============================================================
# Models
# ============================================================

@app.get(
    "/v1/models",
    dependencies=[Depends(auth)],
)
def models_list():
    return {
        "available": [
            "small",
            "medium",
        ],
        "loaded": models.loaded_models(),
    }


# ============================================================
# Job creation helper
# ============================================================

def create_job(
    source,
    model,
    language,
    debug,
    callback_url,
    headers,
    job_id=None,
):
    if callback_url:
        try:
            validate_callback_url(callback_url)
        except ValueError as e:
            raise HTTPException(
                400,
                str(e),
            )

    jid = job_id or manager.new_id()

    return manager.create(
        Job(
            id=jid,
            status=JobStatus.QUEUED,
            source=source,
            model=model,
            language=language,
            debug=debug,
            created_at=datetime.now(timezone.utc),
            callback_url=callback_url,
            callback_headers=headers,
        )
    )


# ============================================================
# REST - URL
# ============================================================

@app.post(
    "/v1/transcriptions",
    status_code=202,
    dependencies=[Depends(auth)],
)
def create(req: TranscriptionRequest):

    if req.source.type != "url" or not req.source.url:
        raise HTTPException(
            400,
            "source must be a URL",
        )

    jid = manager.new_id()

    d = INPUT_DIR / jid

    d.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        validate_source_url(
            req.source.url
        )

        (
            d / "source.json"
        ).write_text(
            json.dumps(
                {
                    "url": req.source.url,
                }
            ),
            encoding="utf-8",
        )

        return create_job(
            JobSource(
                type="url",
                url=req.source.url,
            ),
            req.model,
            req.language,
            req.debug,
            req.callback_url,
            req.callback_headers,
            jid,
        )

    except ValueError as e:

        shutil.rmtree(
            d,
            ignore_errors=True,
        )

        raise HTTPException(
            400,
            str(e),
        )


# ============================================================
# REST - Upload
# ============================================================

@app.post(
    "/v1/transcriptions/upload",
    status_code=202,
    dependencies=[Depends(auth)],
)
async def upload(
    file: Annotated[
        UploadFile,
        File(),
    ],
    model="small",
    language="auto",
    debug=False,
    callback_url=None,
    callback_authorization=None,
):

    if model not in {
        "small",
        "medium",
    }:
        raise HTTPException(
            400,
            "Unsupported model",
        )

    name = safe_filename(
        file.filename
    )

    try:
        validate_extension(name)

    except ValueError as e:

        raise HTTPException(
            400,
            str(e),
        )

    jid = manager.new_id()

    d = INPUT_DIR / jid

    d.mkdir(
        parents=True,
        exist_ok=True,
    )

    p = d / name

    size = 0

    try:

        with open(
            p,
            "wb",
        ) as out:

            while chunk := await file.read(
                1024 * 1024
            ):

                size += len(chunk)

                if size > MAX_INPUT_SIZE:

                    raise HTTPException(
                        413,
                        "File exceeds maximum size",
                    )

                out.write(chunk)

        content_type = file.content_type

    finally:

        await file.close()

    try:

        return create_job(
            JobSource(
                type="upload",
                filename=name,
                content_type=content_type,
                size=size,
            ),
            model,
            language,
            debug,
            callback_url,
            (
                {
                    "Authorization":
                    callback_authorization
                }
                if callback_authorization
                else {}
            ),
            jid,
        )

    except Exception:

        shutil.rmtree(
            d,
            ignore_errors=True,
        )

        raise


# ============================================================
# REST - Base64 data
# ============================================================

@app.post(
    "/v1/transcriptions/data",
    status_code=202,
    dependencies=[Depends(auth)],
)
def data(
    req: Base64TranscriptionRequest,
):

    jid = manager.new_id()

    d = INPUT_DIR / jid

    d.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        n, s = decode_base64_data(
            req.data,
            d,
            req.filename,
            req.content_type,
        )

        return create_job(
            JobSource(
                type="upload",
                filename=n,
                content_type=req.content_type,
                size=s,
            ),
            req.model,
            req.language,
            req.debug,
            req.callback_url,
            req.callback_headers,
            jid,
        )

    except ValueError as e:

        shutil.rmtree(
            d,
            ignore_errors=True,
        )

        raise HTTPException(
            400,
            str(e),
        )

    except Exception:

        shutil.rmtree(
            d,
            ignore_errors=True,
        )

        raise


# ============================================================
# REST - Job status
# ============================================================

@app.get(
    "/v1/transcriptions/{job_id}",
    dependencies=[Depends(auth)],
)
def get(job_id):

    j = manager.get(job_id)

    if not j:

        raise HTTPException(
            404,
            "Job not found",
        )

    return j


# ============================================================
# REST - Job result
# ============================================================

@app.get(
    "/v1/transcriptions/{job_id}/result",
    dependencies=[Depends(auth)],
)
def result(job_id):

    j = manager.get(job_id)

    if not j:

        raise HTTPException(
            404,
            "Job not found",
        )

    if j.status == JobStatus.FAILED:

        raise HTTPException(
            500,
            {
                "status": j.status,
                "error": j.error,
            },
        )

    if j.status != JobStatus.COMPLETED:

        raise HTTPException(
            409,
            {
                "status": j.status,
            },
        )

    return j.result


# ============================================================
# REST - Delete job
# ============================================================

@app.delete(
    "/v1/transcriptions/{job_id}",
    dependencies=[Depends(auth)],
)
def delete(job_id):

    if not manager.get(job_id):

        raise HTTPException(
            404,
            "Job not found",
        )

    e = manager.delete(
        job_id
    )

    shutil.rmtree(
        INPUT_DIR / job_id,
        ignore_errors=True,
    )

    return {
        "id": job_id,
        "deleted": e,
    }


# ============================================================
# MCP Streamable HTTP
#
# IMPORTANT :
# Cet import est volontairement placé à la FIN du module.
#
# À ce stade :
#   manager existe
#   create_job existe
#   toutes les routes REST existent
#
# mcp.py peut donc faire :
#
#   from .main import manager, create_job
#
# sans circular import bloquant.
# ============================================================

from .mcp import mcp


mcp_app = mcp.streamable_http_app()


app.mount(
    "/mcp",
    mcp_app,
)


logger.info(
    "MCP Streamable HTTP mounted at /mcp"
)
