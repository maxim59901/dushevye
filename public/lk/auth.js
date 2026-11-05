/* Dushevye LK auth helper — cookie session via /api + CSRF */
(function (global) {
  const ROLE_PAGE = {
    admin: 'admin.html',
    owner: 'owner.html',
    accountant: 'accountant.html',
    manager: 'manager.html',
    measurer: 'measurer.html',
    installer: 'installer.html',
    supply: 'supply.html',
    production: 'production.html',
  };
  const ROLE_LABEL = {
    admin: 'Админ', owner: 'Хозяин', accountant: 'Бухгалтер', manager: 'Менеджер',
    measurer: 'Замерщик', installer: 'Монтажник', supply: 'Снабжение', production: 'Производство',
  };
  const PRIV = { admin: 1, owner: 1 };
  let csrfToken = null;
  let csrfPromise = null;

  async function ensureCsrf(force) {
    if (csrfToken && !force) return csrfToken;
    if (csrfPromise && !force) return csrfPromise;
    csrfPromise = (async () => {
      const res = await fetch('/api/auth/csrf', { credentials: 'same-origin' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.csrfToken) {
        throw new Error('Не удалось получить CSRF-токен');
      }
      csrfToken = data.csrfToken;
      return csrfToken;
    })();
    try {
      return await csrfPromise;
    } finally {
      csrfPromise = null;
    }
  }

  async function api(path, opts) {
    const o = Object.assign({ credentials: 'same-origin', headers: {} }, opts || {});
    const method = (o.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      const token = await ensureCsrf();
      o.headers['X-CSRF-Token'] = token;
    }
    if (o.body && typeof o.body === 'object' && !(o.body instanceof FormData)) {
      o.headers['Content-Type'] = 'application/json';
      o.body = JSON.stringify(o.body);
    }
    const res = await fetch(path, o);
    let data = null;
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) data = await res.json();
    if (!res.ok) {
      const msg = (data && (data.detail || data.message)) || ('Ошибка ' + res.status);
      const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const Auth = {
    ROLE_PAGE, ROLE_LABEL,
    api,
    ensureCsrf,
    async me() { return api('/api/auth/me'); },
    async login(email, password) {
      await ensureCsrf(true);
      return api('/api/auth/login', { method: 'POST', body: { email, password } });
    },
    async logout() {
      try { await api('/api/auth/logout', { method: 'POST', body: {} }); } catch (e) {}
      csrfToken = null;
      location.href = 'login.html';
    },
    cabinetUrl(user) {
      return ROLE_PAGE[user.role] || 'index.html';
    },
    pageRole() {
      const f = (location.pathname.split('/').pop() || '').replace('.html', '');
      const map = {
        admin: 'admin', owner: 'owner', accountant: 'accountant', manager: 'manager',
        measurer: 'measurer', installer: 'installer', supply: 'supply', production: 'production',
      };
      return map[f] || null;
    },
    /** Guard cabinet page: require login + matching role (privileged can open any). */
    async requireCabinet() {
      let data;
      try {
        await ensureCsrf();
        data = await Auth.me();
      } catch (e) {
        location.replace('login.html?next=' + encodeURIComponent(location.pathname));
        return null;
      }
      const user = data.user;
      const need = Auth.pageRole();
      if (need && user.role !== need && !PRIV[user.role]) {
        location.replace(Auth.cabinetUrl(user));
        return null;
      }
      Auth.mountChrome(user);
      return user;
    },
    mountChrome(user) {
      document.querySelectorAll('.badge.demo').forEach((el) => {
        el.textContent = 'Вход';
        el.classList.remove('demo');
        el.classList.add('ok');
      });
      document.querySelectorAll('.side .foot').forEach((el) => {
        el.textContent = (user.name || user.email) + ' · ' + (ROLE_LABEL[user.role] || user.role);
      });
      document.querySelectorAll('.top .meta').forEach((meta) => {
        if (meta.querySelector('[data-auth-chrome]')) return;
        const wrap = document.createElement('span');
        wrap.setAttribute('data-auth-chrome', '1');
        wrap.style.display = 'inline-flex';
        wrap.style.gap = '8px';
        wrap.style.alignItems = 'center';
        wrap.innerHTML =
          '<span class="badge muted">' + escapeHtml(user.email || '') + '</span>' +
          '<button type="button" class="btn ghost" data-act="pwd">Сменить пароль</button>' +
          '<button type="button" class="btn ghost" data-act="out">Выйти</button>';
        meta.appendChild(wrap);
        wrap.querySelector('[data-act="out"]').onclick = () => Auth.logout();
        wrap.querySelector('[data-act="pwd"]').onclick = () => Auth.openPasswordModal();
      });
      if (!PRIV[user.role]) {
        document.querySelectorAll('a[href="index.html"]').forEach((a) => {
          if ((a.textContent || '').includes('Все роли') || (a.textContent || '').includes('←')) a.style.display = 'none';
        });
      }
    },
    openPasswordModal() {
      let m = document.getElementById('pwdModal');
      if (m) m.remove();
      m = document.createElement('div');
      m.id = 'pwdModal';
      m.innerHTML =
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px">' +
        '<form style="background:#fff;border-radius:14px;padding:20px;max-width:400px;width:100%;border:1px solid var(--line)">' +
        '<h3 style="margin:0 0 12px;font-size:16px">Сменить пароль</h3>' +
        '<div class="field"><label>Текущий</label><input type="password" name="cur" required/></div>' +
        '<div class="field"><label>Новый (от 8 символов)</label><input type="password" name="neu" required minlength="8"/></div>' +
        '<div id="pwdErr" class="err" style="display:none;background:#FEE2E2;color:var(--bad);padding:8px;border-radius:8px;font-size:13px;margin-bottom:8px"></div>' +
        '<div class="row"><button class="btn" type="submit">Сохранить</button>' +
        '<button class="btn ghost" type="button" data-close>Отмена</button></div>' +
        '</form></div>';
      document.body.appendChild(m);
      m.querySelector('[data-close]').onclick = () => m.remove();
      m.querySelector('form').onsubmit = async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const err = m.querySelector('#pwdErr');
        err.style.display = 'none';
        try {
          await api('/api/auth/change-password', {
            method: 'POST',
            body: { current_password: fd.get('cur'), new_password: fd.get('neu') },
          });
          m.remove();
          Auth.showToast('Пароль изменён');
        } catch (ex) {
          err.textContent = ex.message;
          err.style.display = 'block';
        }
      };
    },
    showToast(msg) {
      let t = document.getElementById('authToast');
      if (!t) {
        t = document.createElement('div');
        t.id = 'authToast';
        t.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:60;background:#1F2326;color:#fff;padding:12px 16px;border-radius:10px;font-size:13px;max-width:320px;box-shadow:0 8px 24px rgba(0,0,0,.2)';
        document.body.appendChild(t);
      }
      t.textContent = msg;
      t.style.display = 'block';
      clearTimeout(t._timer);
      t._timer = setTimeout(() => { t.style.display = 'none'; }, 3200);
    },
    /**
     * Show one-time temp password in a modal with Copy — never via alert()
     * (alert dumps the password into browser console / dialog history).
     */
    showTempPasswordModal({ email, role, password, note }) {
      let m = document.getElementById('tempPassModal');
      if (m) m.remove();
      m = document.createElement('div');
      m.id = 'tempPassModal';
      m.setAttribute('role', 'dialog');
      m.setAttribute('aria-modal', 'true');
      m.innerHTML =
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:55;padding:16px">' +
        '<div style="background:#fff;border-radius:14px;padding:22px;max-width:440px;width:100%;border:1px solid var(--line)">' +
        '<h3 style="margin:0 0 8px;font-size:16px">Учётка создана</h3>' +
        '<p style="margin:0 0 12px;font-size:13px;color:var(--muted)">Пароль показывается один раз. Письмо не отправлялось — сохраните и передайте сотруднику.</p>' +
        '<div class="field"><label>Email</label><div style="font-weight:600">' + escapeHtml(email) + '</div></div>' +
        '<div class="field"><label>Роль</label><div>' + escapeHtml(role) + '</div></div>' +
        '<div class="field"><label>Временный пароль</label>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
        '<code id="tempPassValue" style="flex:1;background:var(--soft);padding:10px 12px;border-radius:10px;font-size:15px;letter-spacing:.04em;user-select:all">' +
        escapeHtml(password) + '</code>' +
        '<button type="button" class="btn" data-copy>Копировать</button></div></div>' +
        (note ? '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">' + escapeHtml(note) + '</p>' : '') +
        '<div class="row"><button type="button" class="btn ghost" data-close>Закрыть</button></div>' +
        '</div></div>';
      document.body.appendChild(m);
      const copyBtn = m.querySelector('[data-copy]');
      copyBtn.onclick = async () => {
        const val = password;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(val);
          } else {
            const ta = document.createElement('textarea');
            ta.value = val;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
          }
          copyBtn.textContent = 'Скопировано';
          setTimeout(() => { copyBtn.textContent = 'Копировать'; }, 2000);
        } catch (e) {
          copyBtn.textContent = 'Не удалось';
        }
      };
      m.querySelector('[data-close]').onclick = () => m.remove();
      m.addEventListener('click', (ev) => { if (ev.target === m.firstChild) m.remove(); });
    },
    /** Wire invite + users table for admin/owner pages */
    async wireAdminUsers() {
      const user = await Auth.requireCabinet();
      if (!user || !PRIV[user.role]) return user;

      const inviteCard = document.querySelector('#invite') || document.querySelector('[id="invite"]');
      if (inviteCard) {
        const emailInp = inviteCard.querySelector('input[type=email]');
        const roleSel = inviteCard.querySelector('select');
        const btns = inviteCard.querySelectorAll('button');
        if (roleSel) {
          const roles = [
            ['manager', 'Менеджер'], ['accountant', 'Бухгалтер'], ['measurer', 'Замерщик'],
            ['installer', 'Монтажник'], ['supply', 'Снабжение'], ['production', 'Производство'],
            ['admin', 'Админ'], ['owner', 'Хозяин'],
          ];
          roleSel.innerHTML = roles.map(([v, l]) => '<option value="' + v + '">' + l + '</option>').join('');
        }
        if (emailInp && !inviteCard.querySelector('input[name=name]')) {
          const nf = document.createElement('div');
          nf.className = 'field';
          nf.innerHTML = '<label>Имя</label><input type="text" name="name" placeholder="Ирина К."/>';
          emailInp.closest('.field').before(nf);
        }
        const createBtn = btns[0];
        if (createBtn) {
          createBtn.textContent = 'Создать учётку';
          createBtn.onclick = async () => {
            const email = (emailInp && emailInp.value || '').trim();
            const role = roleSel ? roleSel.value : 'manager';
            const nameEl = inviteCard.querySelector('input[name=name]');
            const name = nameEl ? nameEl.value.trim() : '';
            if (!email) { Auth.showToast('Укажите email'); return; }
            createBtn.disabled = true;
            try {
              const res = await api('/api/users', { method: 'POST', body: { email, role, name } });
              Auth.showTempPasswordModal({
                email: res.user.email,
                role: ROLE_LABEL[res.user.role] || res.user.role,
                password: res.tempPassword,
                note: res.note || '',
              });
              let box = inviteCard.querySelector('[data-temp-pass]');
              if (!box) {
                box = document.createElement('div');
                box.setAttribute('data-temp-pass', '1');
                box.className = 'note';
                inviteCard.appendChild(box);
              }
              // Do not echo the password again in the page DOM long-term — only email/role hint.
              box.innerHTML = '<b>Учётка создана:</b> ' + escapeHtml(res.user.email) +
                ' · роль ' + escapeHtml(ROLE_LABEL[res.user.role] || res.user.role) +
                '<br>Пароль показан в окне выше (один раз).';
              await Auth.refreshUsersTable();
              await Auth.refreshAudit();
            } catch (ex) {
              Auth.showToast(ex.message);
            } finally {
              createBtn.disabled = false;
            }
          };
        }
        if (btns[1]) btns[1].style.display = 'none';
      }

      await Auth.refreshUsersTable();
      await Auth.refreshAudit();
      return user;
    },
    async refreshUsersTable() {
      let tbody = document.querySelector('#users table tbody');
      if (!tbody) {
        const h = Array.from(document.querySelectorAll('h2')).find((x) => /Пользователи|Роли сотрудников/i.test(x.textContent || ''));
        if (h) tbody = h.parentElement.querySelector('table tbody');
      }
      if (!tbody) return;
      try {
        const data = await api('/api/users');
        const roles = [
          ['manager', 'Менеджер'], ['accountant', 'Бухгалтер'], ['measurer', 'Замерщик'],
          ['installer', 'Монтажник'], ['supply', 'Снабжение'], ['production', 'Производство'],
          ['admin', 'Админ'], ['owner', 'Хозяин'],
        ];
        tbody.innerHTML = data.users.map((u) => {
          const opts = roles.map(([v, l]) =>
            '<option value="' + v + '"' + (v === u.role ? ' selected' : '') + '>' + l + '</option>'
          ).join('');
          return '<tr data-uid="' + u.id + '">' +
            '<td>' + escapeHtml(u.name || '—') + '<div style="font-size:12px;color:var(--muted)">' + escapeHtml(u.email) + '</div></td>' +
            '<td><span class="tag info">' + escapeHtml(ROLE_LABEL[u.role] || u.role) + '</span></td>' +
            '<td><select data-role>' + opts + '</select></td>' +
            '<td><button class="btn ghost" type="button" data-save-role>Сохранить</button></td></tr>';
        }).join('');
        tbody.querySelectorAll('[data-save-role]').forEach((btn) => {
          btn.onclick = async () => {
            const tr = btn.closest('tr');
            const id = tr.getAttribute('data-uid');
            const role = tr.querySelector('[data-role]').value;
            btn.disabled = true;
            try {
              await api('/api/users/' + id + '/role', { method: 'PATCH', body: { role } });
              await Auth.refreshUsersTable();
              await Auth.refreshAudit();
            } catch (ex) { Auth.showToast(ex.message); }
            finally { btn.disabled = false; }
          };
        });
      } catch (e) {
        console.warn(e);
      }
    },
    async refreshAudit() {
      let tbody = null;
      const h = Array.from(document.querySelectorAll('h2')).find((x) => /журнал/i.test(x.textContent || ''));
      if (h) tbody = h.parentElement.querySelector('table tbody');
      if (!tbody) return;
      try {
        const data = await api('/api/audit?limit=30');
        tbody.innerHTML = data.items.map((it) => {
          const when = (it.created_at || '').replace('T', ' ').slice(0, 16);
          const who = it.actor_name || it.actor_email || '—';
          const what = it.action + (it.detail ? ': ' + it.detail : '');
          return '<tr><td>' + escapeHtml(when) + '</td><td>' + escapeHtml(who) + '</td><td>' + escapeHtml(what) + '</td></tr>';
        }).join('') || '<tr><td colspan="3">Пока пусто</td></tr>';
      } catch (e) { console.warn(e); }
    },
  };

  global.Auth = Auth;

  const file = (location.pathname.split('/').pop() || '');
  if (/^(admin|owner|accountant|manager|measurer|installer|supply|production)\.html$/i.test(file)) {
    document.addEventListener('DOMContentLoaded', () => {
      if (file === 'admin.html' || file === 'owner.html') Auth.wireAdminUsers();
      else Auth.requireCabinet();
    });
  } else if (file === 'login.html' || file === 'catalog.html') {
    document.addEventListener('DOMContentLoaded', () => { ensureCsrf().catch(() => {}); });
  }
})(window);
