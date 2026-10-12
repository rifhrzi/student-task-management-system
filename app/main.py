from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.dependencies import DB
from app.routers import auth, submissions, tasks
from app.services.files import MAX_FILE_SIZE

app = FastAPI(
    title="Student Task Management System",
    version="1.0.0",
    description="JWT authentication, lecturer assignments, student submissions and grading.",
)
# Allow multipart overhead while limiting the entire stream, including chunked uploads.
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_FILE_SIZE + 1024 * 1024)
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(submissions.router)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(
        static_dir / "index.html",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic input/context can contain passwords; expose locations and messages only.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]
        },
    )


@app.get("/health", tags=["Health"])
def health(db: DB):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ok"}
