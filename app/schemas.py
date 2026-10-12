from datetime import datetime
from decimal import Decimal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models import Role


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserRegister(InputSchema):
    email: EmailStr = Field(max_length=254)
    full_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("full_name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()

    @field_validator("password")
    @classmethod
    def bcrypt_length(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Password cannot contain a null character")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes for bcrypt")
        return value


class OutputSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(OutputSchema):
    id: int
    email: EmailStr
    full_name: str
    role: Role
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TaskCreate(InputSchema):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    due_at: AwareDatetime | None = None

    @field_validator("title", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value cannot be blank")
        return value.strip()


class TaskUpdate(InputSchema):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=20000)
    due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field")
        for name in ("title", "description"):
            if name in self.model_fields_set:
                value = getattr(self, name)
                if value is None or not value.strip():
                    raise ValueError(f"{name} cannot be null or blank")
                setattr(self, name, value.strip())
        return self


class TaskRead(OutputSchema):
    id: int
    title: str
    description: str
    due_at: datetime | None
    lecturer_id: int
    created_at: datetime


class GradeWrite(InputSchema):
    score: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    feedback: str | None = Field(default=None, max_length=10000)


class GradeRead(OutputSchema):
    id: int
    submission_id: int
    lecturer_id: int
    score: Decimal
    feedback: str | None
    graded_at: datetime


class StudentSummary(OutputSchema):
    id: int
    full_name: str
    email: EmailStr


class SubmissionRead(OutputSchema):
    id: int
    task_id: int
    student_id: int
    student: StudentSummary
    answer_text: str | None
    original_filename: str
    content_type: str
    file_size: int
    submitted_at: datetime
    grade: GradeRead | None
