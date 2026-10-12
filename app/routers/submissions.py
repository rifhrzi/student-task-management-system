from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.dependencies import DB, CurrentUser, Lecturer, Student
from app.models import Grade, Role, Submission
from app.schemas import GradeRead, GradeWrite, SubmissionRead
from app.services.access import active_task, owned_task, visible_submission
from app.services.files import save_upload

router = APIRouter(tags=["Submissions & Grades"])


@router.post("/tasks/{task_id}/submissions", response_model=SubmissionRead, status_code=201)
def submit(
    task_id: int,
    db: DB,
    user: Student,
    file: Annotated[UploadFile, File(description="PDF, DOCX or ZIP; maximum 10 MiB")],
    answer_text: Annotated[str | None, Form(max_length=20000)] = None,
):
    task = active_task(db, task_id, lock=True)
    if task.due_at:
        due = (
            task.due_at.replace(tzinfo=timezone.utc) if task.due_at.tzinfo is None else task.due_at
        )
        if datetime.now(timezone.utc) > due:
            raise HTTPException(409, "The submission deadline has passed")
    directory = get_settings().upload_dir
    metadata = save_upload(file, directory)
    submission = Submission(
        task_id=task.id, student_id=user.id, answer_text=answer_text, **metadata
    )
    db.add(submission)
    try:
        db.commit()
    except Exception:
        db.rollback()
        (directory / metadata["stored_filename"]).unlink(missing_ok=True)
        raise
    db.refresh(submission)
    return submission


@router.get("/tasks/{task_id}/submissions", response_model=list[SubmissionRead])
def list_submissions(
    task_id: int,
    db: DB,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    task = active_task(db, task_id)
    query = select(Submission).where(Submission.task_id == task_id)
    if user.role == Role.student:
        query = query.where(Submission.student_id == user.id)
    elif task.lecturer_id != user.id:
        raise HTTPException(403, "This task belongs to another lecturer")
    return db.scalars(query.order_by(Submission.id).offset(offset).limit(limit)).all()


@router.get("/submissions/{submission_id}", response_model=SubmissionRead)
def get_submission(submission_id: int, db: DB, user: CurrentUser):
    return visible_submission(db, submission_id, user)


@router.get("/submissions/{submission_id}/file")
def download(submission_id: int, db: DB, user: CurrentUser):
    submission = visible_submission(db, submission_id, user)
    path = get_settings().upload_dir / submission.stored_filename
    if not path.is_file():
        raise HTTPException(404, "Submission file is unavailable")
    return FileResponse(
        path,
        media_type=submission.content_type,
        filename=submission.original_filename,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.put("/submissions/{submission_id}/grade", response_model=GradeRead)
def grade_submission(submission_id: int, payload: GradeWrite, db: DB, user: Lecturer):
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "Submission not found")
    # Lock the parent task to serialize grade writes with edits and deletion.
    owned_task(db, submission.task_id, user, lock=True)
    grade = db.scalar(select(Grade).where(Grade.submission_id == submission_id))
    if grade is None:
        grade = Grade(submission_id=submission_id, lecturer_id=user.id)
        db.add(grade)
    grade.score = payload.score
    grade.feedback = payload.feedback
    grade.graded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(grade)
    return grade


@router.get("/submissions/{submission_id}/grade", response_model=GradeRead)
def get_grade(submission_id: int, db: DB, user: CurrentUser):
    submission = visible_submission(db, submission_id, user)
    if submission.grade is None:
        raise HTTPException(404, "Submission has not been graded")
    return submission.grade
