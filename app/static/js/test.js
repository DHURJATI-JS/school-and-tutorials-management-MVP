/* Tests: student test-taking (timer, autosave, auto-submit), result polling, admin editor + AI progress. */
(function () {
  "use strict";
  const CN = window.CN;

  /* ---------------------------------------------------------------- student: take a test */
  const take = document.querySelector("[data-test-take]");
  if (take) {
    const aid = take.dataset.attempt;
    const key = "cn-attempt-" + aid;
    const timer = document.querySelector("[data-timer]");
    const timerText = timer ? timer.querySelector("span") : null;
    const submitBtn = document.querySelector("[data-submit-test]");
    let remaining = Number(take.dataset.remaining || 0);
    let submitted = false;
    let dirty = false;

    const collect = () => {
      const answers = {};
      take.querySelectorAll("[data-q]").forEach((q) => {
        const id = q.dataset.q;
        const checked = q.querySelector("input[type=radio]:checked");
        const text = q.querySelector("textarea");
        if (checked) answers[id] = checked.value;
        else if (text && text.value.trim()) answers[id] = text.value;
      });
      return answers;
    };
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "{}");
      Object.keys(saved).forEach((id) => {
        const q = take.querySelector('[data-q="' + id + '"]');
        if (!q) return;
        const radio = q.querySelector('input[type=radio][value="' + CSS.escape(saved[id]) + '"]');
        if (radio) radio.checked = true;
        const text = q.querySelector("textarea");
        if (text && !radio) text.value = saved[id];
      });
    } catch (e) { /* ignore */ }
    const progress = document.querySelector("[data-answered]");
    const updateProgress = () => {
      if (!progress) return;
      progress.textContent = Object.keys(collect()).length + " / " + take.querySelectorAll("[data-q]").length + " answered";
    };
    updateProgress();
    take.addEventListener("input", () => {
      dirty = true;
      try { localStorage.setItem(key, JSON.stringify(collect())); } catch (e) { /* storage full */ }
      updateProgress();
    });
    const save = () => {
      if (!dirty || submitted) return Promise.resolve();
      dirty = false;
      return CN.api("/student/attempts/" + aid + "/save", { json: { answers: collect() } }).catch((err) => {
        dirty = true;
        if (err.status === 409) location.reload();
      });
    };
    setInterval(save, 20000);

    const submit = async (auto) => {
      if (submitted) return;
      if (!auto) {
        const total = take.querySelectorAll("[data-q]").length;
        const done = Object.keys(collect()).length;
        const msg = done < total ? "You have answered " + done + " of " + total + " questions. Submit anyway?" : "Submit your test now?";
        if (!(await CN.confirm(msg, { okText: "Submit test", danger: false, title: "Submit test" }))) return;
      }
      submitted = true;
      CN.busy(submitBtn, true, auto ? "Time's up — submitting…" : "Submitting…");
      try {
        const res = await CN.api("/student/attempts/" + aid + "/submit", { json: { answers: collect() } });
        try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
        location.href = res.redirect;
      } catch (err) {
        submitted = false;
        CN.busy(submitBtn, false);
        CN.toast(err.message + " — your answers are saved; please try again.", "danger");
      }
    };
    if (submitBtn) submitBtn.addEventListener("click", () => submit(false));
    const tick = () => {
      remaining = Math.max(0, remaining - 1);
      if (timerText) timerText.textContent = CN.fmtTime(remaining);
      if (timer) timer.classList.toggle("low", remaining <= 60);
      if (remaining === 0) { clearInterval(handle); submit(true); }
    };
    const handle = setInterval(tick, 1000);
    if (timerText) timerText.textContent = CN.fmtTime(remaining);
    window.addEventListener("beforeunload", (e) => { if (!submitted) { save(); e.preventDefault(); e.returnValue = ""; } });
  }

  /* ---------------------------------------------------------------- result page: wait for grading */
  const pending = document.querySelector("[data-attempt-status]");
  if (pending) {
    const poll = () => CN.api(pending.dataset.attemptStatus).then((d) => {
      if (d.status === "graded") location.reload(); else setTimeout(poll, 3000);
    }).catch(() => setTimeout(poll, 6000));
    setTimeout(poll, 2500);
  }

  /* ---------------------------------------------------------------- admin: AI generation progress */
  const generating = document.querySelector("[data-test-generating]");
  if (generating) {
    const poll = () => CN.api(generating.dataset.testGenerating).then((d) => {
      if (d.status !== "generating") location.reload(); else setTimeout(poll, 3000);
    }).catch(() => setTimeout(poll, 5000));
    setTimeout(poll, 3000);
  }

  /* ---------------------------------------------------------------- admin: question editor */
  const editor = document.querySelector("[data-test-editor]");
  if (!editor) return;
  const list = editor.querySelector("[data-questions]");
  const kinds = CN.readJSON("test-kinds") || { mcq: "Multiple choice", true_false: "True / False", short: "Short answer" };
  const locked = editor.dataset.locked === "1";
  let questions = CN.readJSON("test-questions") || [];

  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  function field(labelText, control) {
    const wrap = el("div", "cn-field");
    wrap.appendChild(el("label", "form-label", labelText));
    wrap.appendChild(control);
    return wrap;
  }
  function render() {
    list.replaceChildren();
    if (!questions.length) {
      const empty = el("div", "cn-empty");
      empty.appendChild(el("h3", "", "No questions yet"));
      empty.appendChild(el("p", "", "Add questions manually or generate them with AI."));
      list.appendChild(empty);
    }
    questions.forEach((q, i) => {
      const card = el("div", "cn-qedit");
      const head = el("div", "cn-qedit-head");
      head.appendChild(el("span", "cn-badge primary", "Q" + (i + 1)));
      const kind = el("select", "form-select form-select-sm w-auto");
      Object.keys(kinds).forEach((k) => { const o = el("option", "", kinds[k]); o.value = k; o.selected = q.kind === k; kind.appendChild(o); });
      kind.disabled = locked;
      kind.addEventListener("change", () => {
        q.kind = kind.value;
        if (q.kind === "mcq" && (!q.options || q.options.length < 2)) { q.options = ["", "", "", ""]; q.answer = "0"; }
        if (q.kind === "true_false") { q.options = ["True", "False"]; q.answer = "true"; }
        if (q.kind === "short") { q.options = []; q.answer = ""; }
        render();
      });
      head.appendChild(kind);
      const marks = el("input", "form-control form-control-sm");
      marks.type = "number"; marks.min = "0.5"; marks.step = "0.5"; marks.value = q.marks || 1; marks.style.width = "90px";
      marks.disabled = locked;
      marks.addEventListener("input", () => { q.marks = Number(marks.value) || 1; updateTotal(); });
      head.appendChild(el("span", "small text-muted ms-auto", "Marks"));
      head.appendChild(marks);
      if (!locked) {
        [["fa-arrow-up", -1], ["fa-arrow-down", 1]].forEach(([icon, dir]) => {
          const b = el("button", "btn btn-ghost btn-sm"); b.type = "button"; b.innerHTML = '<i class="fa-solid ' + icon + '"></i>';
          b.addEventListener("click", () => { const j = i + dir; if (j < 0 || j >= questions.length) return; const t = questions[i]; questions[i] = questions[j]; questions[j] = t; render(); });
          head.appendChild(b);
        });
        const del = el("button", "btn btn-soft-danger btn-sm"); del.type = "button"; del.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
        del.addEventListener("click", () => { questions.splice(i, 1); render(); });
        head.appendChild(del);
      }
      card.appendChild(head);
      const text = el("textarea", "form-control"); text.rows = 2; text.value = q.text || ""; text.disabled = locked;
      text.addEventListener("input", () => { q.text = text.value; });
      card.appendChild(field("Question", text));
      if (q.kind === "mcq") {
        const box = el("div", "");
        (q.options || []).forEach((opt, oi) => {
          const row = el("div", "d-flex gap-2 align-items-center mb-2");
          const radio = el("input", "form-check-input mt-0"); radio.type = "radio"; radio.name = "ans-" + i; radio.checked = String(q.answer) === String(oi);
          radio.disabled = locked; radio.title = "Correct answer";
          radio.addEventListener("change", () => { q.answer = String(oi); });
          const inp = el("input", "form-control form-control-sm"); inp.value = opt; inp.placeholder = "Option " + String.fromCharCode(65 + oi); inp.disabled = locked;
          inp.addEventListener("input", () => { q.options[oi] = inp.value; });
          row.append(radio, inp);
          if (!locked && q.options.length > 2) {
            const rm = el("button", "btn btn-ghost btn-sm"); rm.type = "button"; rm.innerHTML = '<i class="fa-solid fa-xmark"></i>';
            rm.addEventListener("click", () => { q.options.splice(oi, 1); if (Number(q.answer) >= q.options.length) q.answer = "0"; render(); });
            row.appendChild(rm);
          }
          box.appendChild(row);
        });
        if (!locked && (q.options || []).length < 6) {
          const add = el("button", "btn btn-soft btn-sm"); add.type = "button"; add.innerHTML = '<i class="fa-solid fa-plus me-1"></i>Option';
          add.addEventListener("click", () => { q.options.push(""); render(); });
          box.appendChild(add);
        }
        card.appendChild(field("Options (select the correct one)", box));
      } else if (q.kind === "true_false") {
        const sel = el("select", "form-select"); sel.disabled = locked;
        [["true", "True"], ["false", "False"]].forEach(([v, l]) => { const o = el("option", "", l); o.value = v; o.selected = q.answer === v; sel.appendChild(o); });
        sel.addEventListener("change", () => { q.answer = sel.value; });
        card.appendChild(field("Correct answer", sel));
      } else {
        const ans = el("textarea", "form-control"); ans.rows = 2; ans.value = q.answer || ""; ans.disabled = locked;
        ans.addEventListener("input", () => { q.answer = ans.value; });
        card.appendChild(field("Model answer (used for AI / keyword grading)", ans));
      }
      const exp = el("input", "form-control"); exp.value = q.explanation || ""; exp.placeholder = "Shown to students after grading (optional)"; exp.disabled = locked;
      exp.addEventListener("input", () => { q.explanation = exp.value; });
      card.appendChild(field("Explanation", exp));
      list.appendChild(card);
    });
    updateTotal();
  }
  function updateTotal() {
    const total = questions.reduce((s, q) => s + (Number(q.marks) || 0), 0);
    const out = document.querySelector("[data-total-marks]");
    if (out) out.textContent = questions.length + " questions · " + total + " marks";
  }
  const addBtn = editor.querySelector("[data-add-question]");
  if (addBtn) addBtn.addEventListener("click", () => {
    questions.push({ kind: "mcq", text: "", options: ["", "", "", ""], answer: "0", explanation: "", marks: 1 });
    render();
    list.lastElementChild.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  const saveBtn = editor.querySelector("[data-save-test]");
  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const meta = editor.querySelector("[data-test-meta]");
    const payload = {
      title: meta.querySelector('[name="title"]').value,
      instructions: meta.querySelector('[name="instructions"]').value,
      duration_minutes: Number(meta.querySelector('[name="duration_minutes"]').value) || 30,
      show_results: meta.querySelector('[name="show_results"]').checked,
      questions: questions.map((q) => ({ kind: q.kind, text: q.text || "", options: q.options || [], answer: String(q.answer || ""),
        explanation: q.explanation || "", marks: Number(q.marks) || 1 })),
    };
    CN.busy(saveBtn, true, "Saving…");
    try {
      const res = await CN.api(editor.dataset.saveUrl, { json: payload });
      CN.toast("Saved ✓ " + res.count + " questions · " + res.total_marks + " marks", "success");
    } catch (err) {
      CN.toast(err.message, "danger");
    } finally {
      CN.busy(saveBtn, false);
    }
  });
  render();
})();
