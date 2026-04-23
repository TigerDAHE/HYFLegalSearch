from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    model: str | None = Field(default=None, description="可选，覆盖默认模型")


class Citation(BaseModel):
    index: int
    title: str
    url: str
    summary: str
    relevance: float = Field(ge=0, le=1)


class DecisionTrace(BaseModel):
    complexity_score: int = Field(ge=1, le=5)
    confidence_score: float = Field(ge=0, le=1)
    should_search_web: bool
    search_reason: str
    pipeline_id: int | None = None
    pipeline_reason: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    disclaimer: str
    trace: DecisionTrace
    model_used: str
