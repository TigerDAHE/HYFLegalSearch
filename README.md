# AgenticSearch 法律问答 MVP

面向中国大陆法（中文）的最小可用法律问答系统。

核心链路：

用户提问 -> LLM 提取法律实体/关键词 -> 首轮联网搜索(1次) ->
LLM 生成搜索摘要 -> Router LLM 选择 pipeline ->
进入 pipeline 执行 -> 生成回答（带引用+风险提示）

## 技术栈

- 后端: FastAPI
- 工作流: LangGraph
- 大模型路由: LiteLLM（支持多模型切换）
- 联网搜索: Google Serper
- 前端: 原生 HTML + CSS + JS（最小可用）

## 项目结构

app/
  core/config.py           # 环境配置
  models/schemas.py        # API 输入输出模型
  services/llm_router.py   # 多模型统一调用
  services/serper.py       # Serper 搜索
  services/fetcher.py      # 网页抓取与正文清洗
  workflow/state.py        # LangGraph 状态定义
  workflow/graph.py        # AgenticSearch 工作流
  main.py                  # FastAPI 入口
frontend/
  index.html               # 聊天页面
  style.css                # 页面样式
  app.js                   # 前端交互

## 环境变量

复制 .env.example 为 .env 并填写最少配置：

- SERPER_API_KEY=你的 Serper Key
- LLM_MODEL=默认模型名（如 openai/gpt-4o-mini）
- 若使用 OpenAI 兼容网关，可填 LLM_API_BASE / LLM_API_KEY

## 安装与运行

1. 安装依赖

   pip install -r requirements.txt

2. 启动服务

   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

3. 打开浏览器

   http://127.0.0.1:8000

## API

POST /api/chat

请求体示例：

{
  "question": "劳动合同未签订双倍工资如何计算？",
  "model": "openai/gpt-4o-mini"
}

响应体包含：

- answer: 最终答案（中文）
- citations: 引用来源数组（标题、链接、摘要、相关性）
- disclaimer: 固定风险提示
- trace: 决策轨迹（复杂度、信心、是否触及边界、是否联网、原因）
- model_used: 实际使用模型

## 工作流决策逻辑

Router LLM 的输入是“原始提问 + 首轮搜索摘要”，输出 pipeline 编号：

- 1 简单常识核实（搜 1 次）
- 2 法条检索对比（搜 n 次）
- 3 案情推理分析（搜 n*m 次，含 agentic loop）

### Pipeline 1: 简单常识核实

1. 执行约束检查清单：时间效力、地域差异、形式要件、主体资格。
2. 若发现需法条原文精确检索，自动 fallback 到 Pipeline 2。
3. 生成包含“前提假设声明”的答案。

### Pipeline 2: 法条检索对比

1. 将问题拆解为 1-3 个法律要件子问题。
2. 对每个子问题定向联网检索（多次搜索）。
3. 逻辑缝合并处理法条竞合，输出结构化回答。

### Pipeline 3: 案情推理分析

1. 将复杂案情拆解为 3-5 个法律要件。
2. 对每个要件执行定向检索，并支持多轮 agentic loop。
3. 评估证据是否充分，必要时继续循环。
4. 融合多源证据，输出结构化建议与风险说明。

## 重要说明

- 系统强制在最终答案中保留风险提示：
  本回答仅供信息参考，不构成正式法律意见或律师法律服务建议。
- 为避免误导，模型提示词要求“不虚构法条或判例”。
- 若检索证据不足，答案会显式提示不确定性。

## 常见网络错误排查

若日志出现 `httpx.ConnectError: [WinError 10054]`（远程主机强制关闭连接），通常是代理链路或瞬时网络抖动导致。

可按顺序排查：

1. 关闭系统代理透传：将 `SERPER_TRUST_ENV=false`（避免继承本机代理导致 TLS 握手失败）。
2. 增加重试与超时：
  - `SERPER_RETRIES=3`
  - `SERPER_TIMEOUT_SECONDS=25`
  - `SERPER_RETRY_BACKOFF_SECONDS=1.0`
3. 缩短查询长度：`SERPER_MAX_QUERY_LENGTH=120`（复杂长句可降低被中间设备重置概率）。

Serper 客户端已内置：查询清洗、指数退避重试、网络异常降级为空结果，不会因单次网络失败中断整个工作流。
