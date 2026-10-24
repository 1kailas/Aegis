// Aegis AI — Enterprise Documentation Portal Logic (docs.js)

document.addEventListener("DOMContentLoaded", () => {
  initPageRouter();
  initCodeTabs();
  initCopyButtons();
  setupSearchEngine();
  initMobileMenu();
});

// ------------------------------------------------------------- 1. Page Router
function initPageRouter() {
  const pages = document.querySelectorAll(".doc-page");
  const sidebarLinks = document.querySelectorAll(".sidebar-link");
  const footerNavLinks = document.querySelectorAll(".nav-page-btn[data-nav]");

  function showPage(pageId) {
    if (!pageId) pageId = "overview";
    pageId = pageId.replace("#", "");

    let targetPage = document.getElementById(`page-${pageId}`);
    if (!targetPage) {
      pageId = "overview";
      targetPage = document.getElementById("page-overview");
    }

    // Toggle active page
    pages.forEach(p => p.classList.remove("active-page"));
    targetPage.classList.add("active-page");

    // Toggle active sidebar link
    sidebarLinks.forEach(link => {
      if (link.dataset.page === pageId) {
        link.classList.add("active");
      } else {
        link.classList.remove("active");
      }
    });

    // Update URL hash without scroll jump
    history.replaceState(null, null, `#${pageId}`);

    // Scroll main window to top smoothly
    window.scrollTo({ top: 0, behavior: "smooth" });

    // Rebuild Right TOC for this active page
    buildPageTOC(targetPage);

    // Close mobile menu if open
    document.getElementById("sidebar")?.classList.remove("mobile-open");
  }

  // Handle sidebar clicks
  sidebarLinks.forEach(link => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      showPage(link.dataset.page);
    });
  });

  // Handle footer pagination clicks
  footerNavLinks.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      showPage(btn.dataset.nav);
    });
  });

  // Handle browser back/forward or initial hash load
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.replace("#", "");
    if (hash) showPage(hash);
  });

  const initialHash = window.location.hash.replace("#", "") || "overview";
  showPage(initialHash);
}

// ------------------------------------------------------------- 2. Dynamic TOC Builder
function buildPageTOC(activePageElement) {
  const tocNav = document.getElementById("tocNav");
  if (!tocNav || !activePageElement) return;

  const headings = activePageElement.querySelectorAll("h2, h3");
  if (!headings.length) {
    tocNav.innerHTML = `<li style="font-size:0.78rem; color:var(--text-dim); padding:4px 12px;">No sub-sections</li>`;
    return;
  }

  tocNav.innerHTML = Array.from(headings).map((h, idx) => {
    if (!h.id) {
      h.id = "sec-" + h.textContent.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    }
    const isSub = h.tagName.toLowerCase() === "h3";
    return `
      <li>
        <a href="#${h.id}" class="toc-link ${isSub ? "sub" : ""}" style="${isSub ? "padding-left:22px; font-size:0.75rem;" : ""}">
          ${h.textContent}
        </a>
      </li>
    `;
  }).join("");

  // Setup smooth scroll for TOC links
  tocNav.querySelectorAll("a").forEach(a => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const targetId = a.getAttribute("href").replace("#", "");
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

// ------------------------------------------------------------- 3. Tabbed Code Switcher
function initCodeTabs() {
  document.querySelectorAll(".code-tab-card").forEach(card => {
    const tabBtns = card.querySelectorAll(".code-tabs-header .tab-btn");
    const panes = card.querySelectorAll(".code-tab-pane");
    const copyBtn = card.querySelector(".tab-copy-btn");

    tabBtns.forEach(btn => {
      btn.addEventListener("click", () => {
        const targetTab = btn.dataset.tab;
        tabBtns.forEach(b => b.classList.remove("active"));
        panes.forEach(p => p.classList.remove("active"));

        btn.classList.add("active");
        card.querySelector(`#${targetTab}`)?.classList.add("active");
      });
    });

    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        const activePane = card.querySelector(".code-tab-pane.active pre code") || card.querySelector("pre code");
        if (activePane) {
          copyText(activePane.textContent, copyBtn);
        }
      });
    }
  });
}

// ------------------------------------------------------------- 4. Copy Snippets
function copyText(text, btnElement) {
  if (!text) return;
  // Strip shell prompts ($) if copying command line
  const clean = text.replace(/^\$\s+/gm, "");
  navigator.clipboard.writeText(clean).then(() => {
    const orig = btnElement.textContent;
    btnElement.textContent = "Copied!";
    btnElement.style.color = "var(--status-ok)";
    btnElement.style.borderColor = "var(--status-ok-border)";
    setTimeout(() => {
      btnElement.textContent = orig;
      btnElement.style.color = "";
      btnElement.style.borderColor = "";
    }, 1800);
  });
}

function initCopyButtons() {
  window.copySnippet = function(text, btnElement) {
    const target = btnElement || (window.event ? window.event.target : null);
    copyText(text, target);
  };
}

// ------------------------------------------------------------- 5. Search Engine
function setupSearchEngine() {
  const searchModal = document.getElementById("searchModal");
  const searchInput = document.getElementById("searchInput");
  const searchResults = document.getElementById("searchResults");
  const searchTrigger = document.getElementById("searchTrigger");

  // Build searchable index from all pages and headings
  const searchIndex = [];
  document.querySelectorAll(".doc-page").forEach(page => {
    const pageId = page.id.replace("page-", "");
    const pageTitle = page.querySelector("h1")?.textContent || pageId;

    // Index page lead
    const lead = page.querySelector(".page-lead")?.textContent || "";
    searchIndex.push({
      pageId: pageId,
      title: pageTitle,
      crumb: "Guide",
      excerpt: lead.substring(0, 160)
    });

    // Index sections & endpoints
    page.querySelectorAll("h2, h3, .api-path").forEach(h => {
      const text = h.textContent.trim();
      const nextP = h.nextElementSibling?.tagName === "P" ? h.nextElementSibling.textContent : "";
      searchIndex.push({
        pageId: pageId,
        title: text,
        crumb: pageTitle,
        excerpt: nextP.substring(0, 140)
      });
    });
  });

  function openModal() {
    searchModal.style.display = "flex";
    searchInput.value = "";
    renderResults(searchIndex.slice(0, 6));
    setTimeout(() => searchInput.focus(), 50);
  }

  function closeModal() {
    searchModal.style.display = "none";
  }

  searchTrigger?.addEventListener("click", openModal);

  searchModal?.addEventListener("click", (e) => {
    if (e.target === searchModal) closeModal();
  });

  let selectedIdx = 0;

  function renderResults(items) {
    if (!items.length) {
      searchResults.innerHTML = `<div style="padding:16px 18px; color:var(--text-dim); font-size:0.85rem;">No matching documentation found.</div>`;
      return;
    }
    selectedIdx = 0;
    searchResults.innerHTML = items.map((item, idx) => `
      <a class="search-result-row ${idx === 0 ? "selected" : ""}" data-page="${item.pageId}" data-idx="${idx}">
        <div class="search-crumb">${item.crumb}</div>
        <div class="search-title">${item.title}</div>
        <div class="search-excerpt">${item.excerpt}</div>
      </a>
    `).join("");

    searchResults.querySelectorAll(".search-result-row").forEach(row => {
      row.addEventListener("click", () => {
        const pageId = row.dataset.page;
        const link = document.querySelector(`.sidebar-link[data-page="${pageId}"]`);
        if (link) link.click();
        closeModal();
      });
    });
  }

  searchInput?.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    if (!q) {
      renderResults(searchIndex.slice(0, 6));
      return;
    }
    const filtered = searchIndex.filter(item => {
      return item.title.toLowerCase().includes(q) ||
             item.crumb.toLowerCase().includes(q) ||
             item.excerpt.toLowerCase().includes(q);
    });
    renderResults(filtered.slice(0, 8));
  });

  searchInput?.addEventListener("keydown", (e) => {
    const rows = searchResults.querySelectorAll(".search-result-row");
    if (!rows.length) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      rows[selectedIdx]?.classList.remove("selected");
      selectedIdx = (selectedIdx + 1) % rows.length;
      rows[selectedIdx]?.classList.add("selected");
      rows[selectedIdx]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      rows[selectedIdx]?.classList.remove("selected");
      selectedIdx = (selectedIdx - 1 + rows.length) % rows.length;
      rows[selectedIdx]?.classList.add("selected");
      rows[selectedIdx]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      rows[selectedIdx]?.click();
    } else if (e.key === "Escape") {
      closeModal();
    }
  });

  // Global hotkeys
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openModal();
    }
    if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
      e.preventDefault();
      openModal();
    }
    if (e.key === "Escape") {
      closeModal();
    }
  });
}

// ------------------------------------------------------------- 6. Mobile Menu
function initMobileMenu() {
  const btn = document.getElementById("mobileMenuToggle");
  const sidebar = document.getElementById("sidebar");
  btn?.addEventListener("click", () => {
    sidebar?.classList.toggle("mobile-open");
  });
}
