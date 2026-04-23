from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.fetcher import enrich_search_results
from app.services.llm_router import llm_router
from app.services.serper import serper_client
from app.workflow.state import AgentState

logger = get_logger("app.workflow.graph")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _complexity_from_pipeline(pipeline_id: int) -> int:
    if pipeline_id == 1:
        return 2
    if pipeline_id == 2:
        return 3
    return 5


def _normalize_query_list(values: Any, fallback: list[str], max_size: int) -> list[str]:
    if not isinstance(values, list):
        return fallback[:max_size]
    cleaned = [str(x).strip() for x in values if str(x).strip()]
    return (cleaned or fallback)[:max_size]


def _first_search_once(query: str) -> list[dict[str, Any]]:
    try:
        results = serper_client.search(query)
        logger.info("initial_search_done query=%s results=%s", query[:120], len(results))
        return results
    except Exception:
        logger.exception("initial_search_failed query=%s", query[:120])
        return []


def _merge_search_results(queries: list[str], existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = list(existing or [])
    seen = {item.get("url", "") for item in merged if item.get("url", "")}
    logger.info("merge_search_start queries=%s existing=%s", len(queries), len(merged))
    for query in queries:
        try:
            results = serper_client.search(query)
        except Exception:
            logger.exception("targeted_search_failed query=%s", query[:120])
            results = []
        for item in results:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append({**item, "query": query})
    logger.info("merge_search_done merged=%s", len(merged))
    return merged


def _select_useful_sources(question: str, model: str | None, raw_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not raw_results:
        logger.warning("select_sources_no_raw_results")
        return [], []

    enriched = enrich_search_results(raw_results)
    candidates = []
    for idx, item in enumerate(enriched, start=1):
        candidates.append(
            {
                "idx": idx,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "page_excerpt": item.get("page_text", "")[:800],
            }
        )

    prompt = f"""
用户问题：{question}
候选来源：{json.dumps(candidates, ensure_ascii=False)}

请筛选最有帮助的来源并返回 JSON：
{{
  "selected": [
    {{"idx": 1, "relevance": 0.0-1.0, "summary": "一句话摘要"}}
  ]
}}
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是法律证据筛选助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=900,
    )

    useful: list[dict[str, Any]] = []
    selected = parsed.get("selected", []) if isinstance(parsed, dict) else []
    if isinstance(selected, list):
        for item in selected:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("idx", 0))
            if idx < 1 or idx > len(enriched):
                continue
            src = enriched[idx - 1]
            useful.append(
                {
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "summary": str(item.get("summary", ""))[:300],
                    "relevance": _clamp(float(item.get("relevance", 0.5)), 0, 1),
                    "snippet": src.get("snippet", ""),
                    "page_text": src.get("page_text", ""),
                }
            )

    if not useful:
        for src in enriched[:2]:
            useful.append(
                {
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "summary": (src.get("snippet", "") or "检索结果摘要")[:300],
                    "relevance": 0.5,
                    "snippet": src.get("snippet", ""),
                    "page_text": src.get("page_text", ""),
                }
            )

    useful.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    useful = useful[: get_settings().max_sources_for_synthesis]
    logger.info("select_sources_done raw=%s useful=%s", len(raw_results), len(useful))
    return enriched, useful


def _build_citations(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for idx, src in enumerate(sources, start=1):
        citations.append(
            {
                "index": idx,
                "title": src.get("title", ""),
                "url": src.get("url", ""),
                "summary": src.get("summary", "") or src.get("snippet", "")[:300],
                "relevance": _clamp(float(src.get("relevance", 0.5)), 0, 1),
            }
        )
    return citations


def extract_legal_entities(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    logger.info("node_extract_legal_entities question_len=%s", len(question))
    system_prompt = "你是中国大陆法检索词提取助手，只输出 JSON。"
    user_prompt = f"""
用户问题：{question}

输出格式：
{{
  "keywords": ["...", "..."],
  "primary_query": "..."
}}
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=300,
    )

    keywords = _normalize_query_list(parsed.get("keywords", []), fallback=[question], max_size=8)
    primary_query = str(parsed.get("primary_query", "")).strip() or " ".join(keywords[:4])
    if not primary_query:
        primary_query = question

    return {
        "extracted_keywords": keywords,
        "initial_query": primary_query,
        "queries": [primary_query],
        "should_search_web": True,
    }


def initial_search(state: AgentState) -> AgentState:
    query = state.get("initial_query") or state["question"]
    results = _first_search_once(query)
    results = [{**x, "query": query} for x in results]
    return {
        "initial_search_results": results,
        "raw_search_results": results,
    }


def summarize_initial_search(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    results = state.get("initial_search_results") or []
    snippets = []
    for idx, item in enumerate(results[:6], start=1):
        snippets.append(
            {
                "idx": idx,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
            }
        )

    prompt = f"""
用户提问：{question}
联网搜索结果：{json.dumps(snippets, ensure_ascii=False)}

请输出 120-220 字中文摘要，聚焦：
1) 可能相关的法律依据线索
2) 是否存在时效/地域差异
3) 当前证据不足点
""".strip()

    summary = llm_router.chat(
        messages=[
            {"role": "system", "content": "你是法律检索结果摘要助手。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.1,
        max_tokens=400,
    ).strip()

    final_summary = summary or "首轮检索结果不足，待进一步核验。"
    logger.info("node_summarize_initial_search summary_len=%s", len(final_summary))
    return {"search_summary": final_summary}


def route_pipeline(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    context = state.get("search_summary", "")

    router_prompt = f"""
用户提问：{question}
可能有用的信息：{context}

请判断该问题属于哪一类，仅输出 JSON：
{{
  "pipeline_id": 1 或 2 或 3,
  "reason": "一句话",
  "confidence_score": 0.0-1.0
}}

分类标准：
1. 简单常识核实：法律结论明确，主要依赖常识或单一法条确认。
2. 法条检索对比：需要查找法条原文、司法解释或地方规定并对比。
3. 案情推理分析：包含具体情节冲突，需结合多法分析权利义务。
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是法律问题路由器，只输出 JSON。"},
            {"role": "user", "content": router_prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=300,
    )

    pipeline_id = int(parsed.get("pipeline_id", 2))
    if pipeline_id not in (1, 2, 3):
        pipeline_id = 2
    confidence = _clamp(float(parsed.get("confidence_score", 0.7)), 0, 1)
    reason = str(parsed.get("reason", "基于问题与检索摘要的路由判断"))[:220]
    complexity = _complexity_from_pipeline(pipeline_id)

    logger.info(
        "node_route_pipeline pipeline=%s confidence=%.2f reason=%s",
        pipeline_id,
        confidence,
        reason,
    )

    return {
        "pipeline_id": pipeline_id,
        "pipeline_reason": reason,
        "complexity_score": complexity,
        "confidence_score": confidence,
        "search_reason": reason,
    }


def route_after_router(state: AgentState) -> str:
    pipeline_id = int(state.get("pipeline_id", 2))
    if pipeline_id == 1:
        return "pipeline_1_checklist"
    if pipeline_id == 3:
        return "pipeline_3_decompose"
    return "pipeline_2_decompose"


def pipeline_1_checklist(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    context = state.get("search_summary", "")

    checklist_prompt = f"""
用户问题：{question}
检索摘要：{context}

请按以下检查清单评估，并只输出 JSON：
{{
  "checklist_result": "按条列出检查结论",
  "fallback_to_pipeline_2": true/false,
  "assumption": "前提假设声明"
}}

检查项：
1) 时间效力：涉及法律是否失效/修订。
2) 地域差异：是否存在北上广深等地方特殊规定。
3) 形式要件：口头/书面、证据风险提示。
4) 主体资格：当事人主体资格是否影响结论。

若发现需要具体法条原文或细则比对，请 fallback_to_pipeline_2=true。
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是法律核验清单助手，只输出 JSON。"},
            {"role": "user", "content": checklist_prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=700,
    )

    fallback = bool(parsed.get("fallback_to_pipeline_2", False))
    logger.info("node_pipeline_1_checklist fallback_to_p2=%s", fallback)
    return {
        "checklist_result": str(parsed.get("checklist_result", "未返回清单细项。"))[:1200],
        "fallback_to_pipeline_2": fallback,
        "pipeline_reason": str(parsed.get("assumption", "基于当前信息进行回答。"))[:220],
    }


def route_pipeline_1_after_checklist(state: AgentState) -> str:
    return "pipeline_2_decompose" if state.get("fallback_to_pipeline_2") else "pipeline_1_generate"


def _generate_answer_from_sources(
    question: str,
    model: str | None,
    sources: list[dict[str, Any]],
    extra_constraints: str,
) -> tuple[str, list[dict[str, Any]], str]:
    disclaimer = "风险提示：本回答仅供信息参考，不构成正式法律意见或律师法律服务建议。"
    citations = _build_citations(sources)
    evidence_lines = []
    for idx, src in enumerate(sources, start=1):
        evidence_lines.append(
            f"[{idx}] 标题: {src.get('title', '')}\n"
            f"URL: {src.get('url', '')}\n"
            f"摘要: {src.get('summary', '')}\n"
            f"片段: {(src.get('snippet', '') or src.get('page_text', '')[:300])}"
        )
    evidence_block = "\n\n".join(evidence_lines) if evidence_lines else "无外部检索证据。"

    user_prompt = f"""
用户问题：{question}
证据：
{evidence_block}

生成要求：
1) 先给结论，再给依据与推理。
2) 证据对应处标注 [1][2]。
3) 若信息不足，明确不确定性。
4) 最后一行必须输出：{disclaimer}
{extra_constraints}
""".strip()

    answer = llm_router.chat(
        messages=[
            {"role": "system", "content": "你是中国大陆法律问答助手，不得虚构法条与判例。"},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0.2,
        max_tokens=1500,
    ).strip()

    if disclaimer not in answer:
        answer = f"{answer}\n\n{disclaimer}"

    return answer, citations, disclaimer


def pipeline_1_generate(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    raw = state.get("initial_search_results") or []
    enriched, useful = _select_useful_sources(question, model, raw)
    checklist_result = state.get("checklist_result", "")
    assumption = state.get("pipeline_reason", "基于当前描述场景，以下结论在前提假设成立时有效。")

    extra = (
        "请在开头明确写出前提假设声明。"
        f"可参考：{assumption}。"
        f"并融合以下约束检查清单结论：{checklist_result}。"
    )
    answer, citations, disclaimer = _generate_answer_from_sources(question, model, useful, extra)

    return {
        "enriched_search_results": enriched,
        "useful_sources": useful,
        "answer": answer,
        "citations": citations,
        "disclaimer": disclaimer,
    }


def pipeline_2_decompose(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    context = state.get("search_summary", "")
    prompt = f"""
用户问题：{question}
首轮检索摘要：{context}

请拆解为 1-3 个法律要件检索子问题，只输出 JSON：
{{"sub_questions": ["...", "..."]}}
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是法律问题拆解助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=400,
    )
    sub_questions = _normalize_query_list(parsed.get("sub_questions", []), fallback=[question], max_size=3)
    logger.info("node_pipeline_2_decompose sub_questions=%s", len(sub_questions))
    return {"sub_questions": sub_questions}


def pipeline_2_targeted_search(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    sub_questions = state.get("sub_questions") or [question]
    raw = _merge_search_results(sub_questions, existing=state.get("raw_search_results") or [])
    enriched, useful = _select_useful_sources(question, model, raw)
    logger.info("node_pipeline_2_targeted_search sub_questions=%s raw=%s useful=%s", len(sub_questions), len(raw), len(useful))
    return {
        "queries": sub_questions,
        "raw_search_results": raw,
        "enriched_search_results": enriched,
        "useful_sources": useful,
    }


def pipeline_2_synthesize(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    sources = state.get("useful_sources") or []
    sub_questions = state.get("sub_questions") or []
    extra = (
        "请按“法条依据/差异对比/适用结论”三段结构回答。"
        f"需显式处理这些子问题并做逻辑缝合：{json.dumps(sub_questions, ensure_ascii=False)}。"
        "若存在法条竞合，说明优先适用逻辑。"
    )
    answer, citations, disclaimer = _generate_answer_from_sources(question, model, sources, extra)
    return {
        "answer": answer,
        "citations": citations,
        "disclaimer": disclaimer,
    }


def pipeline_3_decompose(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    context = state.get("search_summary", "")
    prompt = f"""
用户问题：{question}
首轮检索摘要：{context}

请拆解为 3-5 个法律要件子问题，只输出 JSON：
{{"sub_questions": ["...", "...", "..."]}}
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是复杂案情拆解助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=500,
    )
    sub_questions = _normalize_query_list(parsed.get("sub_questions", []), fallback=[question], max_size=5)
    while len(sub_questions) < 3:
        sub_questions.append(f"补充要件：{question}")
    logger.info("node_pipeline_3_decompose sub_questions=%s", len(sub_questions[:5]))
    return {
        "sub_questions": sub_questions[:5],
        "loop_count": 0,
        "continue_loop": True,
    }


def pipeline_3_targeted_search(state: AgentState) -> AgentState:
    sub_questions = state.get("sub_questions") or [state["question"]]
    loop_count = int(state.get("loop_count", 0)) + 1

    queries: list[str] = []
    for sq in sub_questions:
        queries.append(sq)
        queries.append(f"{sq} 法条 司法解释")
        if loop_count >= 2:
            queries.append(f"{sq} 裁判规则 案例")

    queries = [q for i, q in enumerate(queries) if q and q not in queries[:i]]
    raw = _merge_search_results(queries, existing=state.get("raw_search_results") or [])
    logger.info("node_pipeline_3_targeted_search loop=%s queries=%s raw=%s", loop_count, len(queries), len(raw))
    return {
        "queries": queries,
        "raw_search_results": raw,
        "loop_count": loop_count,
    }


def pipeline_3_decide_loop(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    raw = state.get("raw_search_results") or []
    sub_questions = state.get("sub_questions") or []
    loop_count = int(state.get("loop_count", 1))

    sample = []
    for idx, item in enumerate(raw[:10], start=1):
        sample.append(
            {
                "idx": idx,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
            }
        )

    prompt = f"""
用户问题：{question}
子问题：{json.dumps(sub_questions, ensure_ascii=False)}
当前检索样本：{json.dumps(sample, ensure_ascii=False)}
当前循环轮次：{loop_count}

请判断是否继续拆解与检索，只输出 JSON：
{{"continue_loop": true/false, "reason": "一句话"}}

当轮次>=2 时，除非证据极度不足，否则优先 false。
""".strip()

    parsed = llm_router.chat_json(
        messages=[
            {"role": "system", "content": "你是 Agentic loop 控制器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0,
        max_tokens=250,
    )

    continue_loop = bool(parsed.get("continue_loop", False)) and loop_count < 2
    reason = str(parsed.get("reason", "达到当前轮次上限或证据基本充分。"))[:220]

    logger.info("node_pipeline_3_decide_loop loop=%s continue=%s", loop_count, continue_loop)
    return {
        "continue_loop": continue_loop,
        "pipeline_reason": reason,
    }


def route_pipeline_3_loop(state: AgentState) -> str:
    return "pipeline_3_targeted_search" if state.get("continue_loop") else "pipeline_3_synthesize"


def pipeline_3_synthesize(state: AgentState) -> AgentState:
    question = state["question"]
    model = state.get("model")
    raw = state.get("raw_search_results") or []
    enriched, useful = _select_useful_sources(question, model, raw)
    sub_questions = state.get("sub_questions") or []
    extra = (
        "请按“事实抽取/法律关系/责任与救济/行动建议”结构回答。"
        f"必须逐一回应以下要件并做冲突处理：{json.dumps(sub_questions, ensure_ascii=False)}。"
        "若存在证据链断点，明确指出还需补充的事实与材料。"
    )
    answer, citations, disclaimer = _generate_answer_from_sources(question, model, useful, extra)
    logger.info("node_pipeline_3_synthesize useful=%s citations=%s", len(useful), len(citations))
    return {
        "enriched_search_results": enriched,
        "useful_sources": useful,
        "answer": answer,
        "citations": citations,
        "disclaimer": disclaimer,
    }


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("extract_legal_entities", extract_legal_entities)
    graph.add_node("initial_search", initial_search)
    graph.add_node("summarize_initial_search", summarize_initial_search)
    graph.add_node("route_pipeline", route_pipeline)

    graph.add_node("pipeline_1_checklist", pipeline_1_checklist)
    graph.add_node("pipeline_1_generate", pipeline_1_generate)

    graph.add_node("pipeline_2_decompose", pipeline_2_decompose)
    graph.add_node("pipeline_2_targeted_search", pipeline_2_targeted_search)
    graph.add_node("pipeline_2_synthesize", pipeline_2_synthesize)

    graph.add_node("pipeline_3_decompose", pipeline_3_decompose)
    graph.add_node("pipeline_3_targeted_search", pipeline_3_targeted_search)
    graph.add_node("pipeline_3_decide_loop", pipeline_3_decide_loop)
    graph.add_node("pipeline_3_synthesize", pipeline_3_synthesize)

    graph.add_edge(START, "extract_legal_entities")
    graph.add_edge("extract_legal_entities", "initial_search")
    graph.add_edge("initial_search", "summarize_initial_search")
    graph.add_edge("summarize_initial_search", "route_pipeline")
    graph.add_conditional_edges(
        "route_pipeline",
        route_after_router,
        {
            "pipeline_1_checklist": "pipeline_1_checklist",
            "pipeline_2_decompose": "pipeline_2_decompose",
            "pipeline_3_decompose": "pipeline_3_decompose",
        },
    )

    graph.add_conditional_edges(
        "pipeline_1_checklist",
        route_pipeline_1_after_checklist,
        {
            "pipeline_1_generate": "pipeline_1_generate",
            "pipeline_2_decompose": "pipeline_2_decompose",
        },
    )
    graph.add_edge("pipeline_1_generate", END)

    graph.add_edge("pipeline_2_decompose", "pipeline_2_targeted_search")
    graph.add_edge("pipeline_2_targeted_search", "pipeline_2_synthesize")
    graph.add_edge("pipeline_2_synthesize", END)

    graph.add_edge("pipeline_3_decompose", "pipeline_3_targeted_search")
    graph.add_edge("pipeline_3_targeted_search", "pipeline_3_decide_loop")
    graph.add_conditional_edges(
        "pipeline_3_decide_loop",
        route_pipeline_3_loop,
        {
            "pipeline_3_targeted_search": "pipeline_3_targeted_search",
            "pipeline_3_synthesize": "pipeline_3_synthesize",
        },
    )
    graph.add_edge("pipeline_3_synthesize", END)

    return graph.compile()


agentic_graph = build_graph()
