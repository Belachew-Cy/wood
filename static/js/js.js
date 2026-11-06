/* ================================================================
   DEREJE WOOD & KITCHEN CABINET MANAGEMENT SYSTEM
   Complete JavaScript v2.0
   ================================================================
   - Theme toggle (light/dark) with persistence
   - Mobile menu + management rail collapse
   - Toast notifications
   - Command palette (⌘K / Ctrl+K)
   - Live counter animations
   - Scroll reveal
   - Form submission (Formspree)
   - Flask flash → toast bridge
   - Recent work loader
   ================================================================ */

(function () {
  'use strict';

  /* ============================================================
     THEME TOGGLE
     ============================================================ */
  const THEME_KEY = 'dw-theme';
  const root = document.documentElement;
  const REDUCE_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');
  const prefersReducedMotion = () => REDUCE_MOTION.matches;

  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    const btn = document.querySelector('.theme-toggle');
    if (btn) {
      btn.classList.add('rotating');
      const icon = btn.querySelector('.toggle-icon');
      const label = btn.querySelector('.toggle-label');
      if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
      if (label) label.textContent = theme === 'dark' ? 'Light' : 'Dark';
      setTimeout(() => btn.classList.remove('rotating'), 500);
    }
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
  }

  function placeThemeToggle(btn) {
    const nav = btn.closest("nav");
    if (!nav) return;
    const ul = nav.querySelector(":scope > ul");
    const actions = nav.querySelector(".nav-actions");
    if (!ul || !actions) return;
    const compact = window.matchMedia("(max-width: 1199px)").matches;
    const host = btn.closest(".nav-theme-item");
    if (compact) {
      if (!host) {
        const li = document.createElement("li");
        li.className = "nav-theme-item";
        ul.appendChild(li);
        li.appendChild(btn);
      }
    } else if (host) {
      actions.appendChild(btn);
      host.remove();
    }
  }

  function ensureThemeToggle() {
    const nav = document.querySelector("body > header nav");
    if (!nav) return;
    const actions = nav.querySelector(".nav-actions") || nav;
    let btn = nav.querySelector(".theme-toggle");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "theme-toggle";
      btn.setAttribute("aria-label", "Toggle light and dark theme");
      btn.innerHTML = '<span class="toggle-icon">🌙</span><span class="toggle-label">Dark</span>';
      btn.addEventListener("click", () => {
        const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(next);
      });
      actions.appendChild(btn);
    }
    placeThemeToggle(btn);
  }

  // Restore saved theme
  let savedTheme = null;
  try { savedTheme = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(savedTheme || (prefersDark ? 'dark' : 'light'));
  ensureThemeToggle();
  // The theme control lives in the bar on desktop and in the collapsed menu on
  // tablet/phone, where the extra 49px is the difference between one row and two.
  const compactNav = window.matchMedia('(max-width: 1199px)');
  compactNav.addEventListener('change', () => {
    const btn = document.querySelector('body > header nav .theme-toggle');
    if (btn) placeThemeToggle(btn);
  });

  /* ============================================================
     MOBILE MENU
     ============================================================ */
  const menuToggle = document.querySelector('.menu-toggle');
  if (menuToggle) {
    menuToggle.addEventListener('click', (e) => {
      const ul = document.querySelector('body > header nav > ul');
      if (!ul) return;
      const open = ul.classList.toggle('open');
      e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  /* ============================================================
     MANAGEMENT RAIL (collapse on phones)
     ============================================================ */
  const railToggle = document.querySelector('.dash-sidebar-toggle');
  if (railToggle) {
    railToggle.addEventListener('click', (e) => {
      const rail = e.currentTarget.closest('.dash-sidebar');
      if (!rail) return;
      const open = rail.classList.toggle('is-open');
      e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  /* Expandable groups: the group holding the current page always opens, and
     every other group remembers the reader's last choice. */
  document.querySelectorAll('.dash-nav-group').forEach((group, index) => {
    const toggle = group.querySelector('.dash-nav-toggle');
    const list = group.querySelector('.dash-nav-list');
    if (!toggle || !list) return;
    const key = 'wk-nav-group:' + (group.dataset.navGroup || index);
    let stored = null;
    try { stored = window.localStorage.getItem(key); } catch (err) { stored = null; }
    const hasActive = !!group.querySelector('.dash-nav-link.active');
    // The rail wraps into a band up to 1199px. Opening every group there turns
    // it into a page-tall wall of pills, so only the current group starts open.
    const compact = window.matchMedia('(max-width: 1199px)').matches;
    const open = hasActive || (stored !== null ? stored === '1' : !compact);
    group.classList.toggle('is-collapsed', !open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.addEventListener('click', () => {
      const expanded = !group.classList.toggle('is-collapsed');
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      try {
        window.localStorage.setItem(key, expanded ? '1' : '0');
      } catch (err) { /* private mode: the rail simply starts open next time */ }
    });
  });

  /* Saved collapse states land before the first frame; arming the transition
     afterwards keeps groups from animating shut on page load. */
  const railNav = document.querySelector('.dash-sidebar-nav');
  if (railNav) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      railNav.classList.add('is-animated');
    }));
  }

  /* On a long rail the current page can sit below the fold; centre it once so
     the active module is visible without the reader hunting for it. */
  const railScroll = document.querySelector('.dash-sidebar');
  const activeLink = document.querySelector('.dash-sidebar .dash-nav-link.active');
  if (railScroll && activeLink
      && window.matchMedia('(min-width: 1101px)').matches
      && railScroll.scrollHeight > railScroll.clientHeight + 8) {
    const top = activeLink.getBoundingClientRect().top
      - railScroll.getBoundingClientRect().top + railScroll.scrollTop;
    railScroll.scrollTop = Math.max(0, top - railScroll.clientHeight / 3);
  }

  /* ============================================================
     FOOTER YEAR
     ============================================================ */
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ============================================================
     TOAST NOTIFICATIONS
     ============================================================ */
  const TOAST_ICONS = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/></svg>',
    error:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5h.01"/></svg>',
    info:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/></svg>'
  };

  function notify(message, { type = 'info', title = '', duration = 5000 } = {}) {
    let box = document.querySelector('.toast-container');
    if (!box) {
      box = document.createElement('div');
      box.className = 'toast-container';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    // A toast repeats whatever the server just said about a record - a plan
    // title, an applicant's name - so that text goes in as text, never as
    // markup. Only the fixed icon below is allowed to be HTML.
    const icon = document.createElement('span');
    icon.className = 't-icon';
    icon.innerHTML = TOAST_ICONS[type] || TOAST_ICONS.info;
    const body = document.createElement('div');
    if (title) {
      const strong = document.createElement('strong');
      strong.textContent = title;
      body.appendChild(strong);
    }
    const text = document.createElement('span');
    text.textContent = message;
    body.appendChild(text);
    const close = document.createElement('button');
    close.className = 't-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '\u00d7';
    toast.append(icon, body, close);

    const dismiss = () => {
      toast.classList.add('hide');
      toast.addEventListener('animationend', () => toast.remove(), { once: true });
    };
    toast.querySelector('.t-close').addEventListener('click', dismiss);
    box.appendChild(toast);
    if (duration) setTimeout(dismiss, duration);
  }

  // Expose globally so Flask templates can call it
  window.notify = notify;

  /* ============================================================
     FLASK FLASH → TOAST BRIDGE
     ============================================================ */
  try {
    const flashEl = document.getElementById('flashes');
    if (flashEl) {
      const flashes = JSON.parse(flashEl.textContent || '[]');
      flashes.forEach(([type, msg]) => {
        const mapped = type === 'message' ? 'info' : type;
        notify(msg, { type: mapped });
      });
    }
  } catch (e) { /* no flashes */ }

  /* ============================================================
     CONTACT FORM (Formspree)
     ============================================================ */
  const contactForm = document.querySelector('form[action*="formspree"]');
  if (contactForm) {
    contactForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.target;
      const btn = form.querySelector('button[type="submit"]');

      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;

      const isConfigured = form.action && !form.action.includes('YOUR_FORM_ID');
      if (!isConfigured) {
        form.reset();
        notify(
          'Your request was recorded locally. Add your Formspree form ID in contact.html to receive it by email.',
          { type: 'info', title: 'Demo mode' }
        );
        return;
      }

      if (btn) { btn.disabled = true; btn.classList.add('is-busy'); }
      try {
        const res = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          headers: { Accept: 'application/json' }
        });
        if (res.ok) {
          form.reset();
          notify('Thank you! We will get back to you within 24 hours.', {
            type: 'success', title: 'Request sent'
          });
        } else {
          notify('Something went wrong on our side. Please try again or call us directly.', {
            type: 'error', title: 'Send failed'
          });
        }
      } catch (err) {
        notify('Network error — please check your connection and try again.', {
          type: 'error', title: 'Send failed'
        });
      } finally {
        if (btn) { btn.disabled = false; btn.classList.remove('is-busy'); }
      }
    });
  }

  // Arriving from a service card with ?service=... preselects that service.
  // The submitted Formspree payload is unchanged.
  const serviceSelect = contactForm && contactForm.querySelector('select[name="service"]');
  if (serviceSelect) {
    const wanted = new URLSearchParams(window.location.search).get('service');
    if (wanted) {
      const match = Array.from(serviceSelect.options)
        .find(o => (o.value || o.textContent).trim().toLowerCase() === wanted.trim().toLowerCase());
      if (match) serviceSelect.value = match.value;
    }
  }

  /* ============================================================
     RECENT WORK (home page)
     ============================================================ */
  function emptyGrid(box, message) {
    box.innerHTML = '';
    const note = document.createElement('p');
    note.className = 'empty-state';
    const lead = document.createElement('strong');
    lead.textContent = 'Latest projects';
    note.appendChild(lead);
    note.appendChild(document.createTextNode(' ' + message));
    box.appendChild(note);
  }

  // Project titles are typed in through the admin project form, so they are
  // inserted as text nodes instead of markup.
  function projectCard(p, href) {
    const card = document.createElement('a');
    card.className = 'project-card';
    card.href = href;
    const img = document.createElement('img');
    img.src = p.image;
    img.alt = p.title;
    img.loading = 'lazy';
    const label = document.createElement('div');
    label.className = 'label';
    const name = document.createElement('span');
    name.textContent = p.title;
    const arrow = document.createElement('span');
    arrow.className = 'arrow';
    arrow.textContent = '→';
    label.append(name, arrow);
    card.append(img, label);
    return card;
  }

  async function loadRecentWork() {
    const box = document.getElementById('recentWork');
    if (!box) return;
    const source = box.dataset.source || '/project.json';
    const href = box.dataset.projectsUrl || '/projects';
    try {
      const res = await fetch(source);
      if (!res.ok) throw new Error('status ' + res.status);
      const data = await res.json();
      box.removeAttribute('aria-busy');
      if (!Array.isArray(data) || data.length === 0) {
        emptyGrid(box, '— new work is being photographed, check back soon.');
        return;
      }
      box.innerHTML = '';
      data.slice(0, 4).forEach(p => {
        const card = projectCard(p, href);
        box.appendChild(card);
        applyTilt(card);
      });
      scopeReveal(box);
    } catch (e) {
      console.warn('project.json unavailable', e);
      box.removeAttribute('aria-busy');
      emptyGrid(box, 'could not be loaded right now. Use the menu to see our work.');
    }
  }
  loadRecentWork();

  /* ============================================================
     NAVIGATION PROGRESS
     ============================================================ */
  let progressBar = null;
  let progressTimer = null;

  function showProgress(to) {
    if (prefersReducedMotion()) return;
    if (!progressBar) {
      progressBar = document.createElement('div');
      progressBar.className = 'dw-progress';
      progressBar.setAttribute('aria-hidden', 'true');
      document.body.appendChild(progressBar);
    }
    clearTimeout(progressTimer);
    progressBar.classList.remove('is-done');
    if (to === 0) {
      // drop back to zero without animating, or a second click crawls backwards
      const was = progressBar.style.transition;
      progressBar.style.transition = 'none';
      progressBar.style.setProperty('--p', '0');
      requestAnimationFrame(() => { progressBar.style.transition = was; });
      showProgress(0.7);
      return;
    }
    requestAnimationFrame(() => progressBar.style.setProperty('--p', String(to)));
    if (to >= 1) {
      progressBar.classList.add('is-done');
      progressTimer = setTimeout(() => progressBar.style.setProperty('--p', '0'), 480);
    }
  }

  // Only plain, same-tab, same-document link clicks hand the bar to the next page.
  // Modifier clicks open a tab we never render into, and fetch-driven forms call
  // preventDefault() here, so a bar started for them would stall at 70%.
  document.addEventListener('click', (event) => {
    const link = event.target.closest ? event.target.closest('a[href]') : null;
    if (!link) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button) return;
    const href = link.getAttribute('href');
    if (!href || href.charAt(0) === '#' || /^(mailto:|tel:|javascript:)/i.test(href)) return;
    if (link.target && link.target !== '_self') return;
    if (link.dataset.noProgress !== undefined) return;
    showProgress(0);
  }, true);

  /* ============================================================
     COUNTER ANIMATION
     ============================================================ */
  function animateCounters() {
    // the finished value is already in the markup, so reduced motion needs no
    // substitute animation - just leave the text alone.
    if (prefersReducedMotion()) return;
    const selectors = [
      '[data-counter]',
      '.hr-card strong',
      '.report-card strong',
      '.finance-metric strong',
      '.stat strong'
    ];
    document.querySelectorAll(selectors.join(',')).forEach(el => {
      if (el.dataset.counterDone) return;
      const text = el.textContent.trim();
      const match = text.match(/^([\d,]+)/);
      if (!match) return;
      const target = parseInt(match[1].replace(/,/g, ''));
      if (isNaN(target) || target < 10) return;

      el.dataset.counterDone = '1';
      const suffix = text.slice(match[1].length);
      const duration = 1400;
      const start = performance.now();

      function tick(now) {
        const t = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        const val = Math.floor(target * eased);
        el.textContent = val.toLocaleString() + suffix;
        if (t < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  }

  /* ============================================================
     SCROLL REVEAL (IntersectionObserver)
     ============================================================ */
  const REVEAL_SELECTOR = [
    '.section', '.page-hero', '.hero-text', '.about-split', '.cta-band',
    '.dash-panel', '.chart-card', '.hr-card', '.stat',
    '.service-card', '.service-full', '.project-card', '.gallery-item',
    '.step', '.report-panel', '.finance-panel'
  ].join(', ');

  let revealObserver = null;

  // Scoped so grids that JS fills after load can be revealed too. The dataset
  // flag keeps an element out of a second observer on repeat calls.
  function scopeReveal(root) {
    if (!('IntersectionObserver' in window)) return;
    if (!revealObserver) {
      revealObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            entry.target.classList.add('reveal');
            revealObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
    }
    (root || document).querySelectorAll(REVEAL_SELECTOR).forEach(el => {
      if (el.dataset.revealing) return;
      el.dataset.revealing = '1';
      revealObserver.observe(el);
    });
  }

  /* ============================================================
     COMMAND PALETTE (⌘K / Ctrl+K)
     ============================================================ */
  function setupCommandPalette() {
    if (document.querySelector('.palette-overlay')) return;

    const overlay = document.createElement('div');
    overlay.className = 'palette-overlay';
    overlay.innerHTML = `
      <div class="palette" role="dialog" aria-label="Command palette">
        <input class="palette-input" placeholder="Type a command or search..." autofocus>
        <div class="palette-list"></div>
      </div>
    `;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('.palette-input');
    const list = overlay.querySelector('.palette-list');

    // Build items from actual links
    const navLinks = Array.from(document.querySelectorAll(
      '.dash-nav-link, body > header nav ul a, .hr-nav-link, .settings-sidebar .hr-nav-link'
    ));
    const items = [];
    navLinks.forEach(link => {
      const label = link.textContent.trim();
      const href = link.getAttribute('href');
      if (label && !items.some(i => i.label === label)) {
        items.push({ label, href: href || '#' });
      }
    });

    if (items.length === 0) {
      items.push(
        { label: 'Dashboard', href: '#' },
        { label: 'Inventory', href: '#' },
        { label: 'Orders', href: '#' },
        { label: 'Settings', href: '#' }
      );
    }

    function render(filter = '') {
      const filtered = items.filter(i =>
        i.label.toLowerCase().includes(filter.toLowerCase())
      );
      list.textContent = '';
      if (filtered.length === 0) {
        const none = document.createElement('div');
        none.className = 'palette-item';
        none.textContent = 'No results';
        list.appendChild(none);
      }
      filtered.forEach((item, idx) => {
        const row = document.createElement('div');
        row.className = 'palette-item' + (idx === 0 ? ' selected' : '');
        row.dataset.href = item.href;
        const arrow = document.createElement('span');
        arrow.textContent = '→';
        const label = document.createElement('span');
        label.textContent = item.label;
        row.append(arrow, label);
        list.appendChild(row);
      });

      list.querySelectorAll('.palette-item').forEach(el => {
        el.addEventListener('click', () => {
          const href = el.dataset.href;
          if (href && href !== '#') window.location.href = href;
          closePalette();
        });
      });
    }

    render();
    input.addEventListener('input', e => render(e.target.value));

    function openPalette() {
      overlay.classList.add('open');
      setTimeout(() => input.focus(), 50);
    }
    function closePalette() {
      overlay.classList.remove('open');
      input.value = '';
      render();
    }

    overlay.addEventListener('click', e => {
      if (e.target === overlay) closePalette();
    });

    document.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        overlay.classList.contains('open') ? closePalette() : openPalette();
      }
      if (e.key === 'Escape' && overlay.classList.contains('open')) {
        closePalette();
      }
    });

    // Bind search box if present
    const searchBox = document.querySelector('.search-box, #searchTrigger');
    if (searchBox) searchBox.addEventListener('click', openPalette);
  }

  /* ============================================================
     LIVE CLOCK (optional — only if #liveTime exists)
     ============================================================ */
  function setupClock() {
    const el = document.getElementById('liveTime');
    if (!el) return;
    const update = () => {
      const now = new Date();
      el.textContent = `Live • ${now.toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit'
      })}`;
    };
    update();
    setInterval(update, 30000);
  }

  /* ============================================================
     HOVER EFFECTS: subtle 3D tilt on cards (optional)
     ============================================================ */
  function applyTilt(card) {
    if (card.dataset.tiltBound) return;
    card.dataset.tiltBound = '1';
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = `translateY(-3px) perspective(600px) rotateY(${x * 4}deg) rotateX(${-y * 4}deg)`;
    });
    card.addEventListener('mouseleave', () => {
      card.style.transform = '';
    });
  }

  function scopeTilt(root) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia('(max-width: 720px)').matches) return;
    (root || document).querySelectorAll('.service-card, .project-card').forEach(applyTilt);
  }

  /* ============================================================
     TABLE FILTERING
     A panel marked [data-filter] filters only the rows the server has
     already put on the page. The report centre and the record browser
     used to build their tables in the browser from a JSON blob; with the
     rows rendered on the server this is all the search box needs.
     ============================================================ */
  function setupTableFilters() {
    document.querySelectorAll('[data-filter]').forEach((panel) => {
      const input = panel.querySelector('[data-filter-search]');
      if (!input) return;

      const rows = Array.from(panel.querySelectorAll('tbody tr'))
        .filter((row) => !row.querySelector('.report-empty'));
      // A card directory has no table, so it hands its own items back instead.
      const items = rows.length ? rows : Array.from(
        panel.querySelectorAll('[data-filter-item]'));
      const unit = panel.dataset.filterUnit || 'row';
      const counter = panel.querySelector('[data-filter-count]');
      const empty = panel.querySelector('[data-filter-empty]');

      const apply = () => {
        const term = input.value.trim().toLowerCase();
        let shown = 0;

        items.forEach((row) => {
          const match = !term || row.innerText.toLowerCase().includes(term);
          row.hidden = !match;
          if (match) shown++;
        });

        if (counter) {
          counter.textContent = shown === items.length
            ? items.length + (items.length === 1 ? ' ' + unit : ' ' + unit + 's')
            : shown + ' of ' + items.length + ' ' + unit + 's shown';
        }
        if (empty) empty.hidden = shown !== 0;
      };

      input.addEventListener('input', apply);
      apply();
    });
  }

  /* ============================================================
     PRINT
     [data-print] replaces the inline onclick the pasted report centre
     shipped with, so a page button still prints without its own script.
     ============================================================ */
  function setupPrint() {
    document.querySelectorAll('[data-print]').forEach((button) => {
      button.addEventListener('click', () => window.print());
    });
  }

  /* ============================================================
     INIT
     ============================================================ */
  function init() {
    scopeReveal();
    scopeTilt();
    setupCommandPalette();
    setupClock();
    setupTableFilters();
    setupPrint();
    setTimeout(animateCounters, 300);
    if (document.readyState === 'complete') showProgress(1);
    else {
      showProgress(0);
      window.addEventListener('load', () => showProgress(1), { once: true });
    }
    // a cached or hung load must not leave a bar parked across the top bar
    setTimeout(() => { if (progressBar && !progressBar.classList.contains('is-done')) showProgress(1); }, 6000);
  }

  // Templates that inject their own cards can hand the new nodes back to the
  // shared reveal + tilt behaviour instead of re-implementing it.
  window.dwEnhance = (root) => { scopeReveal(root); scopeTilt(root); };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();