from pydantic import BaseModel, Field


class ReconPointInput(BaseModel):
    sample: str = Field(min_length=1, max_length=64)
    route: str = Field(min_length=1, max_length=64)
    minute: float
    primary_key: str = Field(min_length=1, max_length=64)
    backup_key: str = Field(min_length=1, max_length=64)


class ReconSheetInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    note: str = Field(default='', max_length=500)
    primary_label: str = Field(default='主记录器', max_length=40)
    backup_label: str = Field(default='备记录器', max_length=40)
    points: list[ReconPointInput] = Field(min_length=5, max_length=5000)
    request_id: str | None = Field(default=None, max_length=80)


class ReadingInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: float


class ReconReadingsInput(BaseModel):
    source: str
    readings: list[ReadingInput] = Field(min_length=1, max_length=5000)
    note: str = Field(default='', max_length=300)
    request_id: str | None = Field(default=None, max_length=80)


class ReconDecisionInput(BaseModel):
    decision: str
    note: str = Field(default='', max_length=300)
    request_id: str | None = Field(default=None, max_length=80)


class ReconSealInput(BaseModel):
    note: str = Field(default='', max_length=500)
    request_id: str | None = Field(default=None, max_length=80)
