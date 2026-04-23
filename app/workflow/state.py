from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    model: str | None

    # Global routing/trace fields
    complexity_score: int
    confidence_score: float
    should_search_web: bool
    search_reason: str
    pipeline_id: int
    pipeline_reason: str

    # First-pass search and summary
    extracted_keywords: list[str]
    initial_query: str
    initial_search_results: list[dict[str, Any]]
    search_summary: str

    # Shared search artifacts
    queries: list[str]
    raw_search_results: list[dict[str, Any]]
    enriched_search_results: list[dict[str, Any]]
    useful_sources: list[dict[str, Any]]

    # Pipeline control
    checklist_result: str
    fallback_to_pipeline_2: bool
    sub_questions: list[str]
    loop_count: int
    continue_loop: bool

    answer: str
    citations: list[dict[str, Any]]
    disclaimer: str
