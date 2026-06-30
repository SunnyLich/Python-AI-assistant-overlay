/* i18n.js — translation engine for the Wisp docs.
   English (docs-pages.js) is the source of truth. Each language file calls
   I18N.register(code, { ui, nav, meta, tr }). Anything not translated falls
   back to English automatically. Code blocks, env vars and CLI stay English,
   except the model-facing default system prompt example. */

const I18N = {
  langs: [
    { code: 'en',      label: 'EN',  name: 'English' },
    { code: 'zh-Hans', label: '简体', name: '简体中文' },
    { code: 'zh-Hant', label: '繁體', name: '繁體中文' },
    { code: 'fr',      label: 'FR',  name: 'Français' },
    { code: 'es',      label: 'ES',  name: 'Español' },
  ],
  reg: {},
  cur: 'en',
  register(code, obj) { this.reg[code] = obj; },
  data() { return this.reg[this.cur] || null; },
};

/* normalise whitespace so dictionary keys match rendered textContent */
function _norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

/* translate one string via the active language's flat tr{} dictionary */
function trText(en) {
  const d = I18N.data();
  if (!d || !d.tr) return en;
  const hit = d.tr[_norm(en)];
  return hit != null ? hit : en;
}

/* UI chrome string lookup (header, search, toc, nav-arrows) */
function trUI(key, fallback) {
  const d = I18N.data();
  if (d && d.ui && d.ui[key] != null) return d.ui[key];
  return fallback;
}

function _escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _renderSystemPromptBlock(prompt) {
  return '<span class="c-key">SYSTEM_PROMPT_UTILITY</span>=<span class="c-val">' +
    _escapeHtml(prompt) +
    '</span>';
}

/* section / page-label lookup for the sidebar + breadcrumb + page-nav */
function trSection(en) {
  const d = I18N.data();
  if (d && d.nav && d.nav.sections && d.nav.sections[en] != null) return d.nav.sections[en];
  return en;
}
function trLabel(en) {
  const d = I18N.data();
  if (d && d.nav && d.nav.labels && d.nav.labels[en] != null) return d.nav.labels[en];
  return en;
}

/* page title / subtitle lookup, keyed by page id */
function trMeta(pageId, field, en) {
  const d = I18N.data();
  if (d && d.meta && d.meta[pageId] && d.meta[pageId][field] != null) return d.meta[pageId][field];
  return en;
}

/* Walk the rendered article and swap block-level prose to the active language.
   Skips anything inside <pre> (code) and elements with no dictionary hit. */
const _TR_SELECTOR = [
  '#content h2', '#content h3',
  '#content p', '#content li',
  '#content td', '#content th',
  '#content .callout-label',
  '#content .sec-pillar-k', '#content .sec-pillar-t',
  '#content .ch-issue', '#content .ch-sol',
  '#content .c-issue', '#content .c-sol',
].join(',');

function applyContentI18n() {
  const d = I18N.data();
  if (!d || !d.tr) return; // English — leave as authored
  document.querySelectorAll(_TR_SELECTOR).forEach(el => {
    if (el.closest('pre')) return;
    const key = _norm(el.textContent);
    if (!key) return;
    const val = d.tr[key];
    if (val == null) return;
    // preserve a leading decorative empty element (e.g. the compare dot)
    const lead = el.firstElementChild;
    if (lead && lead.textContent.trim() === '' &&
        (el.classList.contains('ch-issue') || el.classList.contains('ch-sol'))) {
      el.innerHTML = lead.outerHTML + val;
    } else {
      el.innerHTML = val;
    }
  });
  document.querySelectorAll('#content img[alt]').forEach(img => {
    const current = img.getAttribute('alt') || '';
    const translated = trText(current);
    if (translated !== current) img.setAttribute('alt', translated);
  });
  document.querySelectorAll('#content code[data-i18n-system-prompt]').forEach(code => {
    if (!d.systemPrompt) return;
    code.innerHTML = _renderSystemPromptBlock(d.systemPrompt);
  });
}

/* ── language switcher ────────────────────────────────── */
function buildLangSwitch() {
  const wrap = document.getElementById('langSwitch');
  if (!wrap) return;
  wrap.innerHTML = I18N.langs.map(l =>
    '<button class="lang-btn' + (l.code === I18N.cur ? ' active' : '') + '" ' +
    'data-lang="' + l.code + '" title="' + l.name + '" onclick="setLang(\'' + l.code + '\')">' +
    l.label + '</button>'
  ).join('');
}

function setLang(code) {
  if (!I18N.reg[code] && code !== 'en') return;
  I18N.cur = code;
  try { localStorage.setItem('wisp-lang', code); } catch (e) {}
  document.documentElement.lang = code === 'en' ? 'en' : code;
  buildLangSwitch();
  applyChromeI18n();
  buildSidebar();
  navigate(currentPage, { preserveScroll: true });
}

/* header + search chrome that lives outside the article */
function applyChromeI18n() {
  const dl = document.getElementById('hl-download');
  const cl = document.getElementById('hl-changelog');
  if (dl) dl.textContent = trUI('download', 'Download');
  if (cl) cl.textContent = trUI('changelog', 'Changelog');
  const sbl = document.querySelector('.search-btn .sb-label');
  if (sbl) sbl.textContent = trUI('searchBtn', 'Search documentation');
  const si = document.getElementById('searchInput');
  const demoClose = document.querySelector('.demo-lightbox-close');
  if (demoClose) demoClose.setAttribute('aria-label', trUI('closeDemo', 'Close enlarged demo'));
  if (si) si.placeholder = trUI('searchPlaceholder', 'Search docs…');
}

function initI18n() {
  let saved = 'en';
  try { saved = localStorage.getItem('wisp-lang') || 'en'; } catch (e) {}
  if (!I18N.reg[saved] && saved !== 'en') saved = 'en';
  I18N.cur = saved;
  document.documentElement.lang = saved === 'en' ? 'en' : saved;
  buildLangSwitch();
  applyChromeI18n();
}
