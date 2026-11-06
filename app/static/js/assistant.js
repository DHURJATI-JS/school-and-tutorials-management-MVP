/* Private AI assistant: live streamed chat with memory (only visible to the signed-in user). */
(function () {
  "use strict";
  const root = document.querySelector("[data-assistant]");
  if (!root) return;
  const CN = window.CN;
  const log = root.querySelector("[data-log]");
  const form = root.querySelector("form");
  const input = form.querySelector("textarea");
  const send = form.querySelector("[type=submit]");
  log.scrollTop = log.scrollHeight;

  document.querySelectorAll("[data-suggest]").forEach((b) => b.addEventListener("click", () => {
    input.value = b.dataset.suggest; input.focus();
  }));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message || send.disabled) return;
    const empty = root.querySelector("[data-empty]");
    if (empty) empty.remove();
    CN.bubble(log, "mine", message, "You");
    input.value = "";
    CN.busy(send, true, "Thinking…");
    const ai = CN.bubble(log, "ai", "", "Assistant");
    const body = ai.querySelector(".cn-bubble");
    const meta = ai.querySelector(".cn-meta");
    CN.typing(body);
    let text = "";
    try {
      await CN.sse("/api/assistant/ask", { message }, (ev) => {
        if (ev.type === "token") { if (!text) body.textContent = ""; text += ev.text; body.textContent = text.replace(/<\/?\s*context\s*>/gi, ""); log.scrollTop = log.scrollHeight; }
        else if (ev.type === "final") { body.innerHTML = ev.html; if (ev.source === "fallback") meta.textContent = "Assistant · offline"; }
        else if (ev.type === "notice") { CN.bubble(log, "system", ev.text); }
        else if (ev.type === "done" && ev.concern) {
          CN.bubble(log, "system", "🛡️ Your concern is being shared confidentially with the administration. You'll get a notification when it's reviewed.");
        }
      });
    } catch (err) {
      body.textContent = "⚠️ " + err.message;
    } finally {
      CN.busy(send, false);
      input.focus();
    }
  });
  const clear = document.querySelector("[data-clear-assistant]");
  if (clear) clear.addEventListener("click", async () => {
    if (!(await CN.confirm("This permanently deletes your conversation and what the assistant remembers about you.", { okText: "Clear everything" }))) return;
    await CN.api("/api/assistant/clear", { method: "POST" });
    location.reload();
  });
})();
