/* 云端大模型助手：浏览器直连 WorkBuddy 云服务（免密钥）。
   规范来源：cloud-service skill references/llm/code-generation.md
   - messages[0] 必须是 system；
   - 仅支持 stream: true，逐块拼接 delta.content；
   - 错误按 error.code 前缀分类提示，不向用户暴露内部细节。 */
(function () {
  "use strict";

  var cfg = window.RESEARCH_CLOUD_CONFIG;
  var root = document.getElementById("cloud-ai-panel");
  function earlyStatus(text) {
    var el = root && root.querySelector("[data-cloud-status]");
    if (el) el.textContent = text;
  }
  if (!root) return;
  if (!cfg || !cfg.endpoint || !cfg.publishableKey) {
    earlyStatus("云端配置未注入（static/cloud-config.js 缺失），本面板不可用。");
    return;
  }
  if (typeof WorkBuddyCloud === "undefined") {
    earlyStatus("云端 SDK 未能加载（检查网络或 CDN 访问），本面板暂不可用。");
    return;
  }

  var cloud = WorkBuddyCloud.createWorkBuddyCloud({
    endpoint: cfg.endpoint,
    oauthRelayBaseUrl: cfg.oauthRelayBaseUrl,
    publishableKey: cfg.publishableKey
  });

  var TASKS = {
    polish: {
      label: "学术润色",
      system: "你是材料与能源领域的学术写作助手。对用户给出的中文段落做学术润色：保持术语准确、逻辑清晰、语气克制；只调整表达，不添加任何原文没有的事实或数据；直接输出润色后的文本。"
    },
    english: {
      label: "学术英语改写",
      system: "你是学术英语写作助手。把用户给出的内容改写为符合期刊摘要与正文体裁的学术英语；不编造数据或引用；直接输出改写结果，不要解释。"
    },
    summarize: {
      label: "要点提炼",
      system: "你是严谨的阅读助手。把用户粘贴的内容提炼为不超过 5 条要点，每条一句话；忠实原文，不编造；直接输出要点列表。"
    },
    review: {
      label: "逻辑与证据审查",
      system: "你是方法论审查者。检查用户给出的论证：证据是否支持结论、缺失哪些对照或边界条件、最可能被审稿人质疑的一点是什么。用不超过 5 条的中文条目回答，语气克制。"
    }
  };

  var taskSelect = root.querySelector("[data-cloud-task]");
  var modelSelect = root.querySelector("[data-cloud-model]");
  var input = root.querySelector("[data-cloud-input]");
  var output = root.querySelector("[data-cloud-output]");
  var statusEl = root.querySelector("[data-cloud-status]");
  var runBtn = root.querySelector("[data-cloud-run]");
  var stopBtn = root.querySelector("[data-cloud-stop]");
  var copyBtn = root.querySelector("[data-cloud-copy]");

  var controller = null;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
  }

  function setBusy(busy) {
    if (runBtn) runBtn.disabled = busy;
    if (stopBtn) stopBtn.disabled = !busy;
    if (copyBtn) copyBtn.disabled = busy || !output.value;
  }

  function describeError(err) {
    var code = err && err.error && err.error.code ? err.error.code : "";
    if (code.indexOf("auth_") === 0) {
      return "云端通道校验未通过：该功能需要在已发布的站点域名上使用。";
    }
    if (code.indexOf("quota_") === 0) {
      return err.error.retryAfterMs ? "调用过于频繁，请稍等几秒再试。" : "云端额度不足，请联系应用创建者。";
    }
    if (code.indexOf("gateway_") === 0 || code.indexOf("model_") === 0) {
      return "模型服务暂时不可用，请点击「生成」重试。";
    }
    if (code.indexOf("request_") === 0) {
      return "请求参数不受支持，请检查输入内容或更换模型。";
    }
    if (code.indexOf("internal_") === 0) {
      return "服务内部错误" + (err.requestId ? "（追踪号 " + err.requestId + "）" : "") + "。";
    }
    return "调用失败，请稍后再试。";
  }

  async function loadModels() {
    if (!modelSelect) return;
    setStatus("正在读取可用模型…");
    try {
      var models = await cloud.llm.models.list();
      var usable = (models || []).filter(function (m) { return m.disabled !== true; });
      if (!usable.length) {
        setStatus("云端模型列表为空，请联系应用创建者。");
        return;
      }
      modelSelect.replaceChildren();
      usable.forEach(function (m) {
        var option = document.createElement("option");
        option.value = String(m.id);
        option.textContent = String(m.name || m.id);
        modelSelect.appendChild(option);
      });
      setStatus("");
    } catch (err) {
      setStatus(describeError(err));
    }
  }

  async function run() {
    var text = input.value.trim();
    if (!text) {
      setStatus("先在上方粘贴需要处理的内容。");
      return;
    }
    var task = TASKS[taskSelect.value] || TASKS.polish;
    var model = modelSelect.value;
    if (!model) {
      setStatus("模型尚未就绪。");
      return;
    }
    output.value = "";
    setStatus("生成中…");
    setBusy(true);
    controller = new AbortController();
    var content = "";
    try {
      var stream = cloud.llm.chat.completions.create({
        model: model,
        stream: true,
        temperature: 0.4,
        messages: [
          { role: "system", content: task.system },
          { role: "user", content: text }
        ],
        signal: controller.signal
      });
      for await (var chunk of stream) {
        var delta = chunk && chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
        if (delta && delta.content) {
          content += delta.content;
          output.value = content;
          output.scrollTop = output.scrollHeight;
        }
      }
      setStatus(content ? "已完成。" : "模型没有返回内容，请换一个模型或稍后重试。");
    } catch (err) {
      if (controller && controller.signal.aborted) {
        setStatus("已停止（保留已生成的部分）。");
      } else {
        setStatus(describeError(err));
      }
    } finally {
      setBusy(false);
      controller = null;
    }
  }

  function stop() {
    if (controller) controller.abort();
  }

  function copy() {
    if (!output.value) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(output.value).then(function () {
        setStatus("已复制到剪贴板。");
      });
    } else {
      output.select();
      document.execCommand("copy");
      setStatus("已复制到剪贴板。");
    }
  }

  if (runBtn) runBtn.addEventListener("click", run);
  if (stopBtn) stopBtn.addEventListener("click", stop);
  if (copyBtn) copyBtn.addEventListener("click", copy);
  loadModels();
})();
