/* Storefront assistant. Graphite chrome, one accent (the existing orange).
   No Meta messengers. The page still works if this file fails to load. */
(function () {
  if (document.getElementById("vitrine-assistant")) return;

  var style = document.createElement("style");
  style.textContent = [
    "#vitrine-assistant{--va-graphite:#2B2F33;--va-accent:#C45A2C;--va-bg:#F7F7F5;--va-line:#E5E7EB;--va-muted:#6B7280;--va-white:#fff;",
    "position:fixed;right:20px;bottom:20px;z-index:40;font-family:Inter,system-ui,-apple-system,sans-serif;color:var(--va-graphite);line-height:1.45}",
    "#vitrine-assistant *{box-sizing:border-box}",
    "#vitrine-assistant .va-open{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:999px;background:var(--va-graphite);color:#fff;padding:12px 16px;font:inherit;font-weight:600;font-size:14px;cursor:pointer;box-shadow:0 8px 24px rgba(43,47,51,.18)}",
    "#vitrine-assistant .va-open:hover{filter:brightness(1.08)}",
    "#vitrine-assistant .va-mark{width:16px;height:12px;border:2px solid #fff;border-radius:4px;position:relative;display:inline-block}",
    "#vitrine-assistant .va-mark:after{content:'';position:absolute;left:2px;bottom:-6px;border:5px solid transparent;border-top-color:#fff}",
    "#vitrine-assistant .va-panel{position:fixed;right:20px;bottom:20px;width:min(380px,calc(100vw - 40px));height:min(560px,calc(100vh - 40px));background:var(--va-white);border:1px solid var(--va-line);border-radius:16px;box-shadow:0 16px 40px rgba(43,47,51,.16);display:none;flex-direction:column;overflow:hidden}",
    "#vitrine-assistant.is-open .va-panel{display:flex}",
    "#vitrine-assistant.is-open .va-open{display:none}",
    "#vitrine-assistant .va-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;background:var(--va-graphite);color:#fff}",
    "#vitrine-assistant .va-head strong{display:block;font-size:14px;letter-spacing:.02em}",
    "#vitrine-assistant .va-head span{display:block;font-size:12px;opacity:.75;font-weight:500}",
    "#vitrine-assistant .va-x{border:0;background:transparent;color:#fff;font:inherit;font-size:20px;line-height:1;cursor:pointer;padding:4px 6px}",
    "#vitrine-assistant .va-log{flex:1;overflow:auto;padding:14px;background:var(--va-bg);display:flex;flex-direction:column;gap:10px}",
    "#vitrine-assistant .va-msg{max-width:92%;border-radius:12px;padding:10px 12px;font-size:14px}",
    "#vitrine-assistant .va-msg p{margin:0;white-space:pre-wrap}",
    "#vitrine-assistant .va-bot{background:var(--va-white);border:1px solid var(--va-line);align-self:flex-start}",
    "#vitrine-assistant .va-user{background:var(--va-graphite);color:#fff;align-self:flex-end}",
    "#vitrine-assistant .va-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}",
    "#vitrine-assistant .va-btn, #vitrine-assistant .va-send, #vitrine-assistant .va-submit{display:inline-flex;align-items:center;justify-content:center;border-radius:10px;padding:8px 12px;font:inherit;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}",
    "#vitrine-assistant .va-btn{background:transparent;color:var(--va-graphite);border:1px solid var(--va-graphite)}",
    "#vitrine-assistant .va-btn.va-accent, #vitrine-assistant .va-send, #vitrine-assistant .va-submit{background:var(--va-accent);color:#fff;border:0}",
    "#vitrine-assistant .va-btn:hover, #vitrine-assistant .va-send:hover, #vitrine-assistant .va-submit:hover{filter:brightness(.97)}",
    "#vitrine-assistant .va-compose, #vitrine-assistant .va-form{padding:10px;border-top:1px solid var(--va-line);background:var(--va-white);display:flex;flex-direction:column;gap:8px}",
    "#vitrine-assistant textarea, #vitrine-assistant input{width:100%;border:1px solid var(--va-line);border-radius:10px;padding:8px 10px;font:inherit;font-size:14px;color:var(--va-graphite);background:#fff}",
    "#vitrine-assistant textarea{resize:none;min-height:44px}",
    "#vitrine-assistant .va-row{display:flex;gap:8px;align-items:center}",
    "#vitrine-assistant .va-row .va-send, #vitrine-assistant .va-submit{flex:0 0 auto}",
    "#vitrine-assistant .va-foot{display:flex;gap:8px;flex-wrap:wrap}",
    "#vitrine-assistant .va-error{color:#9B2C2C;font-size:12px;margin:0}",
    "#vitrine-assistant .va-ok{color:var(--va-graphite);font-size:13px;margin:0}",
    "#vitrine-assistant .va-form[hidden], #vitrine-assistant .va-compose[hidden]{display:none}",
    "@media (max-width:640px){#vitrine-assistant{right:12px;bottom:12px}#vitrine-assistant .va-open-label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}#vitrine-assistant .va-panel{left:12px;right:12px;bottom:12px;width:auto;height:min(70vh,560px)}}",
    "@media print{#vitrine-assistant{display:none !important}}"
  ].join("");
  document.head.appendChild(style);

  var root = document.createElement("div");
  root.id = "vitrine-assistant";
  root.innerHTML = [
    '<button type="button" class="va-open" aria-expanded="false" aria-controls="va-panel">',
    '<span class="va-mark" aria-hidden="true"></span><span class="va-open-label">Помощник</span></button>',
    '<section class="va-panel" id="va-panel" role="dialog" aria-labelledby="va-title" hidden>',
    '<div class="va-head"><div><strong id="va-title">Помощник</strong><span>Шаблон, заявка, демо кабинета</span></div>',
    '<button type="button" class="va-x" aria-label="Закрыть">×</button></div>',
    '<div class="va-log" aria-live="polite"></div>',
    '<form class="va-compose">',
    '<div class="va-row"><textarea rows="2" maxlength="1000" placeholder="Например: угловая 900×900" aria-label="Сообщение"></textarea>',
    '<button type="submit" class="va-send">Отправить</button></div>',
    '<p class="va-error" hidden></p>',
    '<div class="va-foot">',
    '<button type="button" class="va-btn" data-lead>Оставить заявку</button>',
    '<a class="va-btn" href="/lk/login.html">Демо ЛК</a>',
    "</div></form>",
    '<form class="va-form" hidden>',
    '<input name="name" maxlength="80" autocomplete="name" placeholder="Имя" aria-label="Имя" required>',
    '<input name="phone" maxlength="24" autocomplete="tel" placeholder="Телефон" aria-label="Телефон" required>',
    '<textarea name="comment" rows="3" maxlength="2000" placeholder="Комментарий" aria-label="Комментарий"></textarea>',
    '<p class="va-error" hidden></p>',
    '<p class="va-ok" hidden></p>',
    '<div class="va-row"><button type="submit" class="va-submit">Отправить заявку</button>',
    '<button type="button" class="va-btn" data-back>К чату</button></div>',
    "</form></section>"
  ].join("");
  document.body.appendChild(root);

  var panel = root.querySelector(".va-panel");
  var log = root.querySelector(".va-log");
  var compose = root.querySelector(".va-compose");
  var form = root.querySelector(".va-form");
  var box = compose.querySelector("textarea");
  var composeError = compose.querySelector(".va-error");
  var formError = form.querySelector(".va-error");
  var formOk = form.querySelector(".va-ok");
  var history = [];
  var lastTemplateId = "";
  var lastUserText = "";
  var sending = false;

  var GREETING = "Подскажу шаблон по типу и размерам: угловая, ниша, П-образная или трапеция. Например: «угловая 900×900». Точную цену без конфигуратора не называю.";

  function setOpen(open) {
    root.classList.toggle("is-open", open);
    panel.hidden = !open;
    root.querySelector(".va-open").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      if (!log.childElementCount) addBot(GREETING, null);
      if (!form.hidden) form.querySelector("[name=name]").focus();
      else box.focus();
    }
  }

  function addBubble(role, text) {
    var item = document.createElement("div");
    item.className = "va-msg " + (role === "user" ? "va-user" : "va-bot");
    var p = document.createElement("p");
    p.textContent = text;
    item.appendChild(p);
    log.appendChild(item);
    scrollLog();
    return item;
  }

  function scrollLog() {
    log.scrollTop = log.scrollHeight;
  }

  function safeHref(href) {
    if (typeof href !== "string") return "";
    if (href === "/lk/login.html" || href === "/cfg.html") return href;
    if (href.indexOf("/cfg.html?") === 0 && href.indexOf(" ") === -1) return href;
    return "";
  }

  function addBot(text, actions, template) {
    var item = addBubble("bot", text);
    var links = document.createElement("div");
    links.className = "va-actions";
    (actions || []).forEach(function (action) {
      if (!action || action.id === "lead" || action.id === "demo") return;
      if (action.id !== "configurator") return;
      var href = safeHref(action.href);
      if (!href) return;
      var a = document.createElement("a");
      a.className = "va-btn" + (template ? " va-accent" : "");
      a.href = href;
      a.textContent = action.label || "Открыть конфигуратор";
      links.appendChild(a);
    });
    if (links.childElementCount) item.appendChild(links);
    scrollLog();
  }

  function showError(node, text) {
    node.hidden = !text;
    node.textContent = text || "";
  }

  function errorText(data, status) {
    if (data && typeof data.detail === "string" && data.detail) return data.detail;
    if (status === 429) return "Слишком много запросов с этого адреса. Подождите минуту.";
    return "Не удалось отправить. Попробуйте ещё раз.";
  }

  async function postJson(url, payload) {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(payload)
    });
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var err = new Error(errorText(data, res.status));
      err.status = res.status;
      throw err;
    }
    return data || {};
  }

  root.querySelector(".va-open").addEventListener("click", function () { setOpen(true); });
  root.querySelector(".va-x").addEventListener("click", function () { setOpen(false); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && root.classList.contains("is-open")) setOpen(false);
  });

  compose.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    if (sending) return;
    var text = box.value.trim();
    if (!text) return;
    sending = true;
    showError(composeError, "");
    addBubble("user", text);
    box.value = "";
    var pending = addBubble("bot", "Думаю…");
    try {
      var data = await postJson("/api/vitrine/chat", { message: text, history: history.slice(-8) });
      pending.remove();
      lastUserText = text;
      lastTemplateId = data.template && data.template.id ? data.template.id : "";
      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: String(data.reply || "") });
      if (history.length > 8) history = history.slice(-8);
      addBot(String(data.reply || ""), data.actions, data.template);
    } catch (ex) {
      pending.remove();
      showError(composeError, ex.message || "Не удалось отправить.");
    } finally {
      sending = false;
      box.focus();
    }
  });

  box.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      compose.requestSubmit ? compose.requestSubmit() : compose.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  compose.querySelector("[data-lead]").addEventListener("click", function () {
    compose.hidden = true;
    form.hidden = false;
    showError(formError, "");
    formOk.hidden = true;
    var comment = form.querySelector("[name=comment]");
    if (!comment.value.trim() && lastUserText) comment.value = lastUserText;
    form.querySelector("[name=name]").focus();
  });

  form.querySelector("[data-back]").addEventListener("click", function () {
    form.hidden = true;
    compose.hidden = false;
    box.focus();
  });

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    if (sending) return;
    sending = true;
    showError(formError, "");
    formOk.hidden = true;
    var excerpt = history.map(function (item) {
      return (item.role === "user" ? "Гость: " : "Помощник: ") + item.content;
    }).join("\n").slice(0, 1500);
    try {
      var data = await postJson("/api/vitrine/leads", {
        name: form.querySelector("[name=name]").value,
        phone: form.querySelector("[name=phone]").value,
        comment: form.querySelector("[name=comment]").value,
        template_id: lastTemplateId,
        excerpt: excerpt
      });
      formOk.textContent = "Заявка №" + data.id + " сохранена. Статус: " + (data.status || "заявка с витрины") + ".";
      formOk.hidden = false;
      form.querySelector(".va-submit").disabled = true;
    } catch (ex) {
      showError(formError, ex.message || "Не удалось сохранить заявку.");
    } finally {
      sending = false;
    }
  });
})();
