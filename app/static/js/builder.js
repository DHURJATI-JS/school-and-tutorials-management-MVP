/* WordPress-style home page builder: drag-to-reorder sections, Quill rich text, list (repeater) editors. */
(function () {
  "use strict";
  const CN = window.CN;
  const csrf = () => document.body.dataset.csrf || "";

  async function uploadImage(file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/admin/site/upload-image", { method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf(), "X-Requested-With": "fetch" }, body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Upload failed");
    return data.url;
  }
  function pickImage() {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/png,image/jpeg,image/webp";
      input.addEventListener("change", () => resolve(input.files[0] || null));
      input.click();
    });
  }

  /* drag & drop ordering */
  const sortable = document.querySelector("[data-sortable]");
  if (sortable && window.Sortable) {
    window.Sortable.create(sortable, {
      handle: "[data-handle]", animation: 180, ghostClass: "opacity-50",
      onEnd() {
        const order = Array.from(sortable.querySelectorAll("[data-id]")).map((n) => Number(n.dataset.id));
        CN.api("/admin/site/sections/reorder", { json: { order } })
          .then(() => CN.toast("Section order saved", "success", 1800)).catch((e) => CN.toast(e.message, "danger"));
      },
    });
  }

  /* rich text */
  document.querySelectorAll("[data-quill]").forEach((holder) => {
    if (!window.Quill) return;
    const target = document.querySelector(holder.dataset.quill);
    const quill = new window.Quill(holder, {
      theme: "snow",
      modules: { toolbar: { container: [[{ header: [2, 3, false] }], ["bold", "italic", "underline", "link"],
        [{ list: "ordered" }, { list: "bullet" }], ["blockquote", "image"], [{ align: [] }], ["clean"]],
      handlers: { image: async () => {
        const file = await pickImage();
        if (!file) return;
        try {
          const url = await uploadImage(file);
          const range = quill.getSelection(true);
          quill.insertEmbed(range ? range.index : 0, "image", url, "user");
        } catch (e) { CN.toast(e.message, "danger"); }
      } } } },
    });
    if (target.value) quill.clipboard.dangerouslyPasteHTML(target.value);
    holder.closest("form").addEventListener("submit", () => {
      const html = quill.root.innerHTML;
      target.value = html === "<p><br></p>" ? "" : html;
    });
  });

  /* repeater lists (features, stats, FAQ, testimonials, gallery …) */
  const LABELS = { icon: "Icon", title: "Title", text: "Text", value: "Value", label: "Label", quote: "Quote", name: "Name",
    role: "Role / class", q: "Question", a: "Answer", src: "Image", caption: "Caption" };
  const LONG = new Set(["text", "a", "quote"]);
  document.querySelectorAll("[data-repeater]").forEach((rep) => {
    const fields = JSON.parse(rep.dataset.fields || "[]");
    const hidden = document.querySelector(rep.dataset.repeater);
    const list = rep.querySelector("[data-items]");
    let items = [];
    try { items = JSON.parse(hidden.value || "[]"); } catch (e) { items = []; }
    const sync = () => { hidden.value = JSON.stringify(items); };
    const make = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };
    function render() {
      list.replaceChildren();
      items.forEach((item, i) => {
        const card = make("div", "cn-qedit");
        const head = make("div", "cn-qedit-head");
        const badge = make("span", "cn-badge primary"); badge.textContent = "#" + (i + 1); head.appendChild(badge);
        const spacer = make("span", "ms-auto"); head.appendChild(spacer);
        [["fa-arrow-up", -1], ["fa-arrow-down", 1]].forEach(([icon, dir]) => {
          const b = make("button", "btn btn-ghost btn-sm"); b.type = "button"; b.innerHTML = '<i class="fa-solid ' + icon + '"></i>';
          b.addEventListener("click", () => { const j = i + dir; if (j < 0 || j >= items.length) return; const t = items[i]; items[i] = items[j]; items[j] = t; render(); });
          head.appendChild(b);
        });
        const del = make("button", "btn btn-soft-danger btn-sm"); del.type = "button"; del.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
        del.addEventListener("click", () => { items.splice(i, 1); render(); });
        head.appendChild(del);
        card.appendChild(head);
        const grid = make("div", "cn-form-grid");
        fields.forEach((f) => {
          const wrap = make("div", "cn-field" + (LONG.has(f) ? " span-2" : ""));
          const label = make("label", "form-label"); label.textContent = LABELS[f] || f; wrap.appendChild(label);
          if (f === "src") {
            const row = make("div", "d-flex gap-2 align-items-center");
            const img = make("img", "cn-photo-preview"); img.alt = ""; img.style.width = "120px"; img.style.height = "80px";
            if (item.src) img.src = item.src; else img.hidden = true;
            const up = make("button", "btn btn-soft btn-sm"); up.type = "button"; up.innerHTML = '<i class="fa-solid fa-upload me-1"></i>Upload';
            up.addEventListener("click", async () => {
              const file = await pickImage();
              if (!file) return;
              CN.busy(up, true, "Uploading…");
              try { item.src = await uploadImage(file); img.src = item.src; img.hidden = false; sync(); }
              catch (e) { CN.toast(e.message, "danger"); } finally { CN.busy(up, false); }
            });
            row.append(img, up); wrap.appendChild(row);
          } else {
            const input = make(LONG.has(f) ? "textarea" : "input", "form-control");
            if (LONG.has(f)) input.rows = 2;
            input.value = item[f] || "";
            if (f === "icon") input.placeholder = "fa-star (fontawesome.com/icons)";
            input.addEventListener("input", () => { item[f] = input.value; sync(); if (f === "icon") preview.className = "fa-solid " + input.value; });
            let preview = null;
            if (f === "icon") {
              const row = make("div", "d-flex gap-2 align-items-center");
              preview = make("i", "fa-solid " + (item.icon || "fa-star"));
              preview.style.width = "24px";
              row.append(preview, input); wrap.appendChild(row);
            } else { wrap.appendChild(input); }
          }
          grid.appendChild(wrap);
        });
        card.appendChild(grid);
        list.appendChild(card);
      });
      sync();
    }
    rep.querySelector("[data-add]").addEventListener("click", () => {
      const blank = {}; fields.forEach((f) => { blank[f] = ""; }); if (fields.includes("icon")) blank.icon = "fa-star";
      items.push(blank); render();
    });
    render();
  });
})();
