const chat = document.getElementById("chat");
const form = document.getElementById("chat-form");
const questionInput = document.getElementById("question");
const modelInput = document.getElementById("model");
const template = document.getElementById("message-template");

const LOG_LEVEL_PRIORITY = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

const FRONTEND_LOG_LEVEL = (localStorage.getItem("legalsearch.logLevel") || "info").toLowerCase();
const SESSION_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

function logEvent(level, event, details = {}) {
  const normalizedLevel = LOG_LEVEL_PRIORITY[level] ? level : "info";
  const currentLevelPriority = LOG_LEVEL_PRIORITY[FRONTEND_LOG_LEVEL] || LOG_LEVEL_PRIORITY.info;
  if (LOG_LEVEL_PRIORITY[normalizedLevel] < currentLevelPriority) {
    return;
  }

  const payload = {
    ts: new Date().toISOString(),
    level: normalizedLevel,
    event,
    sessionId: SESSION_ID,
    ...details,
  };

  if (normalizedLevel === "debug") {
    console.debug("[legalsearch:web]", payload);
  } else if (normalizedLevel === "info") {
    console.info("[legalsearch:web]", payload);
  } else if (normalizedLevel === "warn") {
    console.warn("[legalsearch:web]", payload);
  } else {
    console.error("[legalsearch:web]", payload);
  }
}

logEvent("info", "frontend_ready", {
  logLevel: FRONTEND_LOG_LEVEL,
  userAgent: navigator.userAgent,
});

function addMessage(role, meta, content) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".meta").textContent = meta;
  node.querySelector(".content").textContent = content;
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;

  logEvent("debug", "message_rendered", {
    role,
    meta,
    contentLength: content.length,
  });
}

function formatCitations(citations) {
  if (!Array.isArray(citations) || citations.length === 0) {
    return "";
  }

  const lines = ["\n\n引用来源："];
  for (const c of citations) {
    lines.push(`[${c.index}] ${c.title}\n${c.url}\n摘要: ${c.summary}`);
  }
  return lines.join("\n");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = questionInput.value.trim();
  const model = modelInput.value.trim();
  if (!question) {
    logEvent("warn", "submit_blocked_empty_question");
    return;
  }

  const start = performance.now();
  logEvent("info", "chat_submit", {
    questionLength: question.length,
    questionPreview: question.slice(0, 40),
    model: model || "default",
  });

  addMessage("user", "你", question);
  questionInput.value = "";

  addMessage("assistant", "系统", "正在提取法律实体并执行首轮联网检索...\n随后会自动路由到对应分析 pipeline。");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, model: model || null }),
    });

    const requestId = response.headers.get("X-Request-ID") || "n/a";
    const elapsedMs = Math.round(performance.now() - start);
    logEvent("info", "chat_response_received", {
      status: response.status,
      ok: response.ok,
      requestId,
      elapsedMs,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    chat.lastElementChild.remove();

    const pipeline = data.trace.pipeline_id ? `P${data.trace.pipeline_id}` : "未标记";
    const routeReason = data.trace.pipeline_reason || data.trace.search_reason || "无";
    const trace = `复杂度: ${data.trace.complexity_score}/5 | 信心: ${data.trace.confidence_score.toFixed(
      2
    )} | 联网: ${data.trace.should_search_web ? "是" : "否"} | 路由: ${pipeline}`;
    const output = `${data.answer}${formatCitations(data.citations)}\n\n模型: ${data.model_used}\n决策: ${trace}`;
    addMessage("assistant", "法律助手", `${output}\n路由理由: ${routeReason}`);

    logEvent("info", "chat_response_rendered", {
      pipeline,
      citations: Array.isArray(data.citations) ? data.citations.length : 0,
      answerLength: (data.answer || "").length,
    });
  } catch (error) {
    chat.lastElementChild.remove();
    addMessage("assistant", "错误", `请求失败: ${error.message}`);
    logEvent("error", "chat_request_failed", {
      message: error?.message || "unknown_error",
      stack: error?.stack || "",
    });
  }
});
