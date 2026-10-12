from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, Submission, Task, User


def active_task(db: Session, task_id: int, *, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id, Task.is_deleted.is_(False))
    if lock:
        query = query.with_for_update()
    task = db.scalar(query)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def owned_task(db: Session, task_id: int, user: User, *, lock: bool = False) -> Task:
    task = active_task(db, task_id, lock=lock)
    if task.lecturer_id != user.id:
        raise HTTPException(403, "This task belongs to another lecturer")
    return task


def visible_submission(db: Session, submission_id: int, user: User) -> Submission:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "Submission not found")
    task = active_task(db, submission.task_id)
    allowed = (
        submission.student_id == user.id
        if user.role == Role.student
        else task.lecturer_id == user.id
    )
    if not allowed:
        raise HTTPException(404, "Submission not found")
    return submission
