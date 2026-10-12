from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from app.dependencies import DB, CurrentUser, Lecturer
from app.models import Task
from app.schemas import TaskCreate, TaskRead, TaskUpdate
from app.services.access import active_task, owned_task

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=list[TaskRead])
def list_tasks(
    db: DB,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return db.scalars(
        select(Task).where(Task.is_deleted.is_(False)).order_by(Task.id).offset(offset).limit(limit)
    ).all()


@router.post("", response_model=TaskRead, status_code=201)
def create_task(payload: TaskCreate, db: DB, user: Lecturer):
    task = Task(**payload.model_dump(), lecturer_id=user.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: DB, user: CurrentUser):
    return active_task(db, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: int, payload: TaskUpdate, db: DB, user: Lecturer):
    task = owned_task(db, task_id, user, lock=True)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: DB, user: Lecturer):
    task = owned_task(db, task_id, user, lock=True)
    # Preserve submitted work and grades when a lecturer removes a task.
    task.is_deleted = True
    db.commit()
    return Response(status_code=204)
