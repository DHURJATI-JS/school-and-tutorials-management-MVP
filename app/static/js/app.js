/* ClassNest core UI behaviours. CSP-safe: no inline handlers - everything is wired with data-attributes. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const csrf = () => document.body.dataset.csrf || "";
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const CN = (window.CN = window.CN || {});
  CN.$ = $;
  CN.$$ = $$;

  /* ---------------------------------------------------------------- fetch helper */
  CN.reloadSoon = function (delay) {             // at most once per 30 s, so a misbehaving socket can't loop
    let last = 0;
    try { last = Number(sessionStorage.getItem("cn-reload") || 0); } catch (e) { /* private mode */ }
    if (CN.reloading || Date.now() - last < 30000) return;
    CN.reloading = true;
    try { sessionStorage.setItem("cn-reload", String(Date.now())); } catch (e) { /* ignore */ }
    setTimeout(() => location.reload(), delay || 1500);
  };
  CN.api = async function (url, opts) {
    opts = opts || {};
    const headers = Object.assign({ "X-CSRF-Token": csrf(), "X-Requested-With": "fetch", Accept: "application/json" },
      opts.headers || {});
    let body = opts.body;
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
    const res = await fetch(url, { method: opts.method || (body ? "POST" : "GET"), headers, body,
      credentials: "same-origin", keepalive: !!opts.keepalive });
    // blocked on the spot: an IP ban (block page) or a ban / deletion that ended the session (sign-in notice)
    if (res.headers.get("X-Access-Blocked") || res.status === 401) CN.reloadSoon(600);
    let data = null;
    try { data = await res.clone().json(); } catch (e) { data = null; }
    if (!res.ok) {
      const detail = data && (data.detail || data.error);
      const err = new Error(typeof detail === "string" ? detail : "Request failed (" + res.status + ")");
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  };

  /* ---------------------------------------------------------------- toasts */
  const ICONS = { success: "fa-circle-check", danger: "fa-circle-exclamation", warning: "fa-triangle-exclamation",
    info: "fa-circle-info" };
  CN.toast = function (message, type, timeout) {
    type = type || "success";
    const box = $("#cn-toasts");
    if (!box || !message) return;
    const el = document.createElement("div");
    el.className = "cn-toast " + type;
    const icon = document.createElement("i");
    icon.className = "fa-solid lead-icon " + (ICONS[type] || ICONS.info);
    const text = document.createElement("div");
    text.textContent = message;
    const close = document.createElement("button");
    close.className = "cn-toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = '<i class="fa-solid fa-xmark"></i>';
    el.append(icon, text, close);
    box.appendChild(el);
    const kill = () => { el.classList.add("out"); setTimeout(() => el.remove(), 300); };
    close.addEventListener("click", kill);
    setTimeout(kill, timeout || (type === "danger" ? 9000 : 5500));
  };
  try {
    JSON.parse(($("#cn-flashes") || {}).textContent || "[]").forEach((f) => CN.toast(f[1], f[0]));
  } catch (e) { /* ignore */ }

  /* ---------------------------------------------------------------- light / dark theme */
  function setThemeIcons(theme) {
    $$("[data-theme-toggle] i").forEach((i) => { i.className = "fa-solid " + (theme === "dark" ? "fa-sun" : "fa-moon"); });
  }
  CN.theme = () => document.documentElement.getAttribute("data-mdb-theme") || "light";
  setThemeIcons(CN.theme());
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-theme-toggle]")) return;
    const next = CN.theme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-mdb-theme", next);
    try { localStorage.setItem("cn-theme", next); } catch (err) { /* private mode */ }
    setThemeIcons(next);
    document.dispatchEvent(new CustomEvent("cn:theme", { detail: next }));
  });

  /* ---------------------------------------------------------------- top progress bar */
  const bar = $("#cn-progress");
  CN.progress = {
    start() { if (!bar) return; bar.classList.add("on"); bar.style.width = "35%"; clearTimeout(this.t);
      this.t = setTimeout(() => { bar.style.width = "80%"; }, 450); },
    done() { if (!bar) return; bar.style.width = "100%"; setTimeout(() => { bar.classList.remove("on"); bar.style.width = "0"; }, 280); },
  };
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank" ||
        a.hasAttribute("download") || a.origin !== location.origin) return;
    const href = a.getAttribute("href") || "";
    if (href.startsWith("#") || a.pathname.startsWith("/files/") || a.pathname.endsWith(".csv")) return;
    CN.progress.start();
  });

  /* ---------------------------------------------------------------- confirm dialog */
  CN.confirm = function (message, opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      wrap.className = "cn-modal open";
      wrap.innerHTML = '<div class="cn-modal-dialog" role="alertdialog" aria-modal="true"><div class="cn-modal-body">' +
        '<h3 class="h5 mb-2"></h3><p class="mb-0 text-muted"></p></div><div class="cn-modal-foot">' +
        '<button type="button" class="btn btn-ghost" data-x>Cancel</button>' +
        '<button type="button" class="btn ' + (opts.danger === false ? "btn-primary" : "btn-danger") + '" data-ok></button></div></div>';
      $("h3", wrap).textContent = opts.title || "Are you sure?";
      $("p", wrap).textContent = message;
      $("[data-ok]", wrap).textContent = opts.okText || "Yes, continue";
      const done = (value) => { wrap.classList.remove("open"); document.removeEventListener("keydown", onKey);
        setTimeout(() => wrap.remove(), 200); resolve(value); };
      const onKey = (ev) => { if (ev.key === "Escape") done(false); };
      wrap.addEventListener("click", (ev) => {
        if (ev.target === wrap || ev.target.closest("[data-x]")) done(false);
        else if (ev.target.closest("[data-ok]")) done(true);
      });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(wrap);
      $("[data-ok]", wrap).focus();
    });
  };

  /* ---------------------------------------------------------------- forms: confirm + double-submit guard */
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-ajax")) return;
    if (form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      const ok = await CN.confirm(form.dataset.confirm, { okText: form.dataset.confirmOk, danger: form.dataset.confirmTone !== "primary" });
      if (ok) {
        form.dataset.confirmed = "1";
        if (form.requestSubmit) form.requestSubmit(e.submitter || undefined); else form.submit();
      }
      return;
    }
    if (form.hasAttribute("data-allow-resubmit")) return;
    if (form.dataset.submitted) { e.preventDefault(); return; }     // client-side double-submit guard
    form.dataset.submitted = "1";
    const btn = e.submitter || $("[type=submit]", form);
    setTimeout(() => {                                              // after the browser captured the submitter
      $$("[type=submit]", form).forEach((b) => { b.disabled = true; });
      if (btn) { btn.dataset.label = btn.innerHTML; btn.classList.add("is-loading");
        btn.innerHTML = '<span class="cn-spin"></span>' + (btn.dataset.loading || "Please wait…"); }
    }, 0);
    if ((form.method || "get").toLowerCase() !== "get" || !form.target) CN.progress.start();
  });
  window.addEventListener("pageshow", () => {
    CN.progress.done();
    $$("form[data-submitted]").forEach((f) => {
      delete f.dataset.submitted; delete f.dataset.confirmed;
      $$("[type=submit]", f).forEach((b) => { b.disabled = false; b.classList.remove("is-loading");
        if (b.dataset.label) b.innerHTML = b.dataset.label; });
    });
  });
  CN.busy = function (btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.classList.add("is-loading");
      btn.innerHTML = '<span class="cn-spin"></span>' + (label || "Working…"); }
    else { btn.disabled = false; btn.classList.remove("is-loading"); if (btn.dataset.label) btn.innerHTML = btn.dataset.label; }
  };

  /* ---------------------------------------------------------------- dropdowns, modals, sidebar */
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-dropdown-toggle]");
    $$(".cn-dropdown.open").forEach((d) => { if (!toggle || !d.contains(toggle)) d.classList.remove("open"); });
    if (toggle) { e.preventDefault(); toggle.closest(".cn-dropdown").classList.toggle("open"); }

    const opener = e.target.closest("[data-modal-open]");
    if (opener) {
      e.preventDefault();
      const modal = $(opener.dataset.modalOpen);
      if (modal) {
        modal.classList.add("open");
        Object.keys(opener.dataset).forEach((k) => {           // data-set-<name>="value" fills fields in the modal
          if (k.startsWith("set") && k.length > 3) {
            const name = k.slice(3).replace(/^[A-Z]/, (c) => c.toLowerCase()).replace(/[A-Z]/g, (c) => "_" + c.toLowerCase());
            const field = modal.querySelector('[name="' + name + '"]');
            if (field) field.value = opener.dataset[k];
          }
        });
        if (opener.dataset.action) { const f = $("form", modal); if (f) f.action = opener.dataset.action; }
        const first = $("input:not([type=hidden]):not([type=file]), select, textarea", modal);
        if (first) setTimeout(() => first.focus(), 80);
      }
    }
    const closer = e.target.closest("[data-modal-close]");
    if (closer || e.target.classList.contains("cn-modal")) {
      const m = (closer || e.target).closest(".cn-modal");
      if (m && !m.hasAttribute("data-static")) m.classList.remove("open");
    }
    if (e.target.closest("[data-sidebar-toggle]")) document.body.classList.toggle("cn-nav-open");
    if (e.target.closest("[data-sidebar-close]")) document.body.classList.remove("cn-nav-open");

    const copy = e.target.closest("[data-copy]");
    if (copy && navigator.clipboard) navigator.clipboard.writeText(copy.dataset.copy).then(() => CN.toast("Copied", "info", 1800));
    if (e.target.closest("[data-print]")) window.print();
    if (e.target.closest("[data-back]") && history.length > 1) { e.preventDefault(); history.back(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $$(".cn-modal.open:not([data-static])").forEach((m) => m.classList.remove("open"));
      $$(".cn-dropdown.open").forEach((d) => d.classList.remove("open")); }
  });

  /* ---------------------------------------------------------------- tabs */
  $$("[data-tabs]").forEach((tabs) => {
    const buttons = $$("button[data-tab]", tabs);
    const scope = tabs.parentElement;
    const show = (id) => {
      buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
      $$(":scope > .cn-tab-panel", scope).forEach((p) => p.classList.toggle("active", p.id === id));
      document.dispatchEvent(new CustomEvent("cn:tab", { detail: id }));
    };
    buttons.forEach((b) => b.addEventListener("click", () => { show(b.dataset.tab); history.replaceState(null, "", "#" + b.dataset.tab); }));
    const hash = location.hash.slice(1);
    const wanted = buttons.some((b) => b.dataset.tab === hash) ? hash : tabs.dataset.default;
    show(buttons.some((b) => b.dataset.tab === wanted) ? wanted : buttons[0] && buttons[0].dataset.tab);
  });

  /* ---------------------------------------------------------------- notifications bell */
  const bell = $("[data-notif]");
  if (bell) {
    const dot = $(".cn-dot", bell);
    const list = $("[data-notif-list]");
    const render = (data) => {
      if (!data) return;
      if (dot) { dot.textContent = data.unread > 9 ? "9+" : String(data.unread); dot.hidden = !data.unread; }
      if (!list) return;
      list.replaceChildren();
      if (!data.items.length) {
        const empty = document.createElement("div");
        empty.className = "text-muted small p-3 text-center";
        empty.textContent = "You're all caught up ✨";
        list.appendChild(empty);
      }
      data.items.forEach((n) => {
        const a = document.createElement("a");
        a.className = "cn-notif" + (n.read ? "" : " unread");
        a.href = n.link || "/notifications";
        const i = document.createElement("i"); i.className = "fa-solid " + (n.icon || "fa-bell");
        const body = document.createElement("div");
        const t = document.createElement("div"); t.className = "fw-bold small"; t.textContent = n.title;
        const s = document.createElement("div"); s.className = "small text-muted"; s.textContent = (n.body ? n.body + " · " : "") + n.ago;
        body.append(t, s); a.append(i, body); list.appendChild(a);
      });
    };
    const load = () => CN.api("/api/notifications").then(render).catch(() => {});
    load();
    setInterval(() => { if (!document.hidden) load(); }, 30000);  // doubles as the "still allowed in?" heartbeat
    const readAll = $("[data-notif-read]");
    if (readAll) readAll.addEventListener("click", (e) => { e.preventDefault(); CN.api("/api/notifications/read-all", { method: "POST" }).then(load); });
  }

  /* ---------------------------------------------------------------- filters auto-submit */
  document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-autosubmit]");
    if (el && el.form) { if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit(); }
  });

  /* ---------------------------------------------------------------- holiday-aware date hints */
  $$("input[type=date][data-dayinfo]").forEach((input) => {
    const hint = document.createElement("div");
    hint.className = "cn-day-hint";
    input.after(hint);
    const check = () => {
      if (!input.value) { hint.textContent = ""; return; }
      CN.api("/api/day-info?date=" + encodeURIComponent(input.value)).then((d) => {
        if (d.blocked) { hint.className = "cn-day-hint blocked"; hint.textContent = "⛔ " + cap(d.label) + " — not allowed for tasks or deadlines"; }
        else if (d.kind === "vacation") { hint.className = "cn-day-hint vacation";
          hint.textContent = "🏖️ " + d.label + (d.allow_homework ? " — only holiday homework allowed" : " — no work allowed"); }
        else { hint.className = "cn-day-hint open"; hint.textContent = "✓ Working day"; }
      }).catch(() => { hint.textContent = ""; });
    };
    input.addEventListener("change", check);
    check();
  });

  /* ---------------------------------------------------------------- file inputs: previews + video duration */
  $$("input[type=file][data-preview]").forEach((input) => {
    input.addEventListener("change", () => {
      const img = $(input.dataset.preview);
      const f = input.files && input.files[0];
      if (img && f && f.type.startsWith("image/")) { img.src = URL.createObjectURL(f); img.hidden = false; }
    });
  });
  $$("input[type=file][data-duration-into]").forEach((input) => {
    input.addEventListener("change", () => {
      const f = input.files && input.files[0];
      const out = $(input.dataset.durationInto);
      if (!f || !out) return;
      const v = document.createElement("video");
      v.preload = "metadata";
      v.addEventListener("loadedmetadata", () => { if (isFinite(v.duration)) out.value = Math.round(v.duration); URL.revokeObjectURL(v.src); });
      v.src = URL.createObjectURL(f);
    });
  });

  /* ---------------------------------------------------------------- webcam capture into a file input */
  CN.camera = async function (inputSel, facing) {
    const input = $(inputSel);
    if (!input) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      CN.toast("The camera needs HTTPS (or localhost). You can upload a photo instead.", "warning"); return;
    }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing || "user", width: { ideal: 1280 } }, audio: false }); }
    catch (err) { CN.toast("Couldn't open the camera: " + err.message, "danger"); return; }
    const wrap = document.createElement("div");
    wrap.className = "cn-modal open";
    wrap.innerHTML = '<div class="cn-modal-dialog"><div class="cn-modal-head"><h3>Take a photo</h3>' +
      '<button type="button" class="cn-icon-btn" data-x aria-label="Close"><i class="fa-solid fa-xmark"></i></button></div>' +
      '<div class="cn-modal-body"><div class="cn-camera"><video autoplay playsinline muted></video>' +
      (facing === "environment" ? '<div class="cn-card-frame"></div>' : "") + '</div></div>' +
      '<div class="cn-modal-foot"><button type="button" class="btn btn-ghost" data-x>Cancel</button>' +
      '<button type="button" class="btn btn-primary" data-snap><i class="fa-solid fa-camera me-2"></i>Capture</button></div></div>';
    document.body.appendChild(wrap);
    const video = $("video", wrap);
    video.srcObject = stream;
    const stop = () => { stream.getTracks().forEach((t) => t.stop()); wrap.remove(); };
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap || e.target.closest("[data-x]")) stop();
      if (!e.target.closest("[data-snap]")) return;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      canvas.toBlob((blob) => {
        const file = new File([blob], "camera-" + Date.now() + ".jpg", { type: "image/jpeg" });
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        stop();
        CN.toast("Photo captured", "success", 2000);
      }, "image/jpeg", 0.92);
    });
  };
  $$("[data-camera-for]").forEach((btn) => btn.addEventListener("click", () => CN.camera(btn.dataset.cameraFor, btn.dataset.facing)));

  /* ---------------------------------------------------------------- template fillers */
  $$("select[data-fill]").forEach((sel) => {
    let map = {};
    try { map = JSON.parse($(sel.dataset.fill).textContent); } catch (e) { return; }
    sel.addEventListener("change", () => {
      const tpl = map[sel.value];
      if (!tpl) return;
      Object.keys(tpl).forEach((key) => {
        const el = sel.form && sel.form.querySelector('[data-fill-key="' + key + '"]');
        if (el && tpl[key] !== undefined) el.value = tpl[key];
      });
    });
  });

  /* ---------------------------------------------------------------- reveal-on-scroll + misc */
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
    }), { threshold: 0.12 });
    $$(".cn-reveal").forEach((el) => io.observe(el));
  } else {
    $$(".cn-reveal").forEach((el) => el.classList.add("in"));
  }
  $$("[data-check-all]").forEach((box) => box.addEventListener("change", () => {
    $$(box.dataset.checkAll).forEach((c) => { c.checked = box.checked; });
  }));
  CN.fmtTime = (sec) => {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
  };
  CN.readJSON = (id) => { const el = document.getElementById(id); try { return el ? JSON.parse(el.textContent) : null; } catch (e) { return null; } };

  /* ---------------------------------------------------------------- server-sent events over fetch (AI streaming) */
  CN.sse = async function (url, payload, onEvent) {
    const res = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf(), "X-Requested-With": "fetch" },
      body: JSON.stringify(payload) });
    if (!res.ok) {
      let msg = "Something went wrong (" + res.status + ").";
      try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (block.startsWith("data: ")) { try { onEvent(JSON.parse(block.slice(6))); } catch (e) { /* skip */ } }
      }
    }
  };

  /* ---------------------------------------------------------------- chat bubble builder (textContent only) */
  CN.bubble = function (log, kind, text, meta) {
    const wrap = document.createElement("div");
    wrap.className = "cn-msg " + kind;
    const body = document.createElement("div");
    body.className = "cn-bubble";
    if (text) body.textContent = text;
    wrap.appendChild(body);
    if (meta) { const m = document.createElement("div"); m.className = "cn-meta"; m.textContent = meta; wrap.appendChild(m); }
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
    return wrap;
  };
  CN.typing = function (el) { el.innerHTML = '<span class="cn-typing"><span></span><span></span><span></span></span>'; };
  CN.socket = function (path, onMessage, onState) {          // auto-reconnecting WebSocket with heartbeat
    let ws, tries = 0, stopped = false, ping;
    const open = () => {
      ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + path);
      ws.addEventListener("open", () => { tries = 0; if (onState) onState("open"); ping = setInterval(() => send({ type: "ping" }), 25000); });
      ws.addEventListener("message", (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (err) { return; }
        if (msg && msg.type === "kicked") {          // access revoked (ban, deletion, IP block): leave at once
          stopped = true;
          CN.toast(msg.reason || "Your access has been removed.", "danger", 8000);
          CN.reloadSoon(2500);
        }
        try { onMessage(msg); } catch (err) { /* ignore */ }
      });
      ws.addEventListener("close", (e) => {
        clearInterval(ping);
        if (onState) onState("closed", e.code);
        if (e.code === 4403 && !stopped) { stopped = true; CN.reloadSoon(1500); return; }
        if (stopped || e.code === 4400) return;
        setTimeout(open, Math.min(10000, 800 * Math.pow(2, tries++)));
      });
    };
    const send = (obj) => { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; } return false; };
    open();
    return { send, stop() { stopped = true; if (ws) ws.close(); } };
  };
})();
