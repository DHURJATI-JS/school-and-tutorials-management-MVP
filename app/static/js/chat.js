/* Batch group chat (live, moderated). */
(function () {
  "use strict";
  const root = document.querySelector("[data-batch-chat]");
  if (!root) return;
  const CN = window.CN;
  const batch = root.dataset.batch;
  const me = root.dataset.me;
  const canMod = root.dataset.mod === "1";
  const log = root.querySelector("[data-log]");
  const form = root.querySelector("form");
  const input = form.querySelector("textarea");
  const online = document.querySelector("[data-online]");
  const status = document.querySelector("[data-conn]");
  let oldest = null;
  let loadingOlder = false;

  function render(m, prepend) {
    const mine = me && String(m.uid) === me;
    const el = document.createElement("div");
    el.className = "cn-msg" + (mine ? " mine" : "") + (m.flagged ? " flagged" : "");
    el.dataset.id = m.id;
    const body = document.createElement("div");
    body.className = "cn-bubble";
    body.textContent = m.text;
    const meta = document.createElement("div");
    meta.className = "cn-meta";
    meta.textContent = (mine ? "You" : m.name + " · " + m.role) + " · " + m.ts;
    if (m.badge) { const b = document.createElement("span"); b.className = "cn-badge danger"; b.textContent = m.badge; meta.appendChild(b); }
    if (canMod) {
      const del = document.createElement("button");
      del.className = "btn btn-link btn-sm p-0 text-danger";
      del.type = "button";
      del.title = "Delete message";
      del.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
      del.addEventListener("click", async () => {
        if (await CN.confirm("Delete this message for everyone?", { okText: "Delete" })) CN.api("/chat/messages/" + m.id + "/delete", { method: "POST" }).catch((e) => CN.toast(e.message, "danger"));
      });
      meta.appendChild(del);
    }
    el.append(body, meta);
    if (prepend) log.prepend(el); else { log.appendChild(el); log.scrollTop = log.scrollHeight; }
    if (oldest === null || m.id < oldest) oldest = m.id;
  }
  (CN.readJSON("chat-initial") || []).forEach((m) => render(m));
  log.scrollTop = log.scrollHeight;

  const sock = CN.socket("/ws/chat/" + batch, (m) => {
    if (m.type === "message") render(m);
    else if (m.type === "deleted") { const el = log.querySelector('[data-id="' + m.id + '"]'); if (el) el.remove(); }
    else if (m.type === "presence" && online) online.textContent = m.online.length + " online · " + m.online.slice(0, 6).join(", ");
    else if (m.type === "notice") { CN.bubble(log, "system", m.text); CN.toast(m.text, "warning", 9000); }
    else if (m.type === "kicked") { CN.bubble(log, "system", m.reason); sock.stop(); input.disabled = true; }
  }, (state, code) => {
    if (status) status.textContent = state === "open" ? "Live" : (code === 4403 ? "Access denied" : "Reconnecting…");
    if (status) status.className = "cn-badge " + (state === "open" ? "success" : "warning");
  });

  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    if (sock.send({ type: "message", text })) input.value = "";
    else CN.toast("Not connected yet — retrying…", "warning");
  });
  log.addEventListener("scroll", () => {
    if (log.scrollTop > 40 || loadingOlder || oldest === null) return;
    loadingOlder = true;
    const before = log.scrollHeight;
    CN.api("/api/chat/" + batch + "/history?before=" + oldest).then((d) => {
      d.messages.slice().reverse().forEach((m) => render(m, true));
      log.scrollTop = log.scrollHeight - before;
      loadingOlder = d.messages.length === 0;
    }).catch(() => { loadingOlder = false; });
  });
})();
