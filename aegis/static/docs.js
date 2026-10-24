// Aegis AI — Complete Documentation Portal Logic (docs.js)

document.addEventListener("DOMContentLoaded", () => {
  setupCopyButtons();
  setupScrollSpy();
  setupSearchEngine();
  setupKeyboardShortcuts();
});

// ------------------------------------------------------------- 1. Copy Snippets
function setupCopyButtons() {
  window.copySnippet = function(text, btnElement) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      // Find button if not passed directly
      let targetBtn = btnElement || (window.event ? window.event.target : null);
      if (targetBtn) {
        const originalText = targetBtn.textContent;
        targetBtn.textContent = "✔ Copied";
        targetBtn.style.color = "var(--ok)";
        targetBtn.style.borderColor = "var(--ok)";
        setTimeout(() => {
          targetBtn.textContent = originalText;
          targetBtn.style.color = "";
          targetBtn.style.borderColor = "";
        }, 1800);
      }
    }).catch(err => {
      console.error("Clipboard copy failed: ", err);
    });
  };

  // Wire all code-block copy buttons
  document.querySelectorAll(".code-block").forEach(block => {
    const copyBtn = block.querySelector(".copy-btn");
    const code = block.querySelector("pre code");
    if (copyBtn && code && !copyBtn.getAttribute("onclick")) {
      copyBtn.addEventListener("click", () => {
        window.copySnippet(code.textContent, copyBtn);
      });
    }
  });
}

// ------------------------------------------------------------- 2. Scroll Spy
function setupScrollSpy() {
  const sections = document.querySelectorAll("article section[id]");
  const tocLinks = document.querySelectorAll("#tocList a");
  const navLinks = document.querySelectorAll(".docs-sidebar .nav-item a");

  if (!sections.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.getAttribute("id");
        if (!id) return;

        // Highlight TOC link
        tocLinks.forEach(link => {
          if (link.getAttribute("href") === `#${id}`) {
            link.classList.add("active");
          } else {
            link.classList.remove("active");
          }
        });

        // Highlight Sidebar link
        navLinks.forEach(link => {
          if (link.getAttribute("href") === `#${id}`) {
            link.classList.add("active");
          } else {
            link.classList.remove("active");
          }
        });
      }
    });
  }, {
    rootMargin: "-20% 0px -70% 0px",
    threshold: 0
  });

  sections.forEach(sec => observer.observe(sec));
}

// ------------------------------------------------------------- 3. Search Engine
function setupSearchEngine() {
  const searchIndex = [];
  const sections = document.querySelectorAll("article section[id]");

  sections.forEach(sec => {
    const secId = sec.getAttribute("id");
    const h1 = sec.querySelector("h1");
    const h2s = sec.querySelectorAll("h2");
    const h3s = sec.querySelectorAll("h3");
    const mainTitle = h1 ? h1.textContent.trim() : (h2s.length ? h2s[0].textContent.trim() : secId);

    // Index section itself
    const pText = Array.from(sec.querySelectorAll("p, li")).map(p => p.textContent.trim()).join(" ");
    searchIndex.push({
      id: secId,
      title: mainTitle,
      category: "Section",
      snippet: pText.substring(0, 160) + "..."
    });

    // Index h2 sub-headings
    h2s.forEach(h => {
      const title = h.textContent.trim();
      searchIndex.push({
        id: secId,
        title: title,
        category: mainTitle,
        snippet: pText.substring(0, 140) + "..."
      });
    });

    // Index h3 sub-headings
    h3s.forEach(h => {
      const title = h.textContent.trim();
      searchIndex.push({
        id: secId,
        title: title,
        category: mainTitle,
        snippet: pText.substring(0, 140) + "..."
      });
    });
  });

  const searchModal = document.getElementById("searchModal");
  const searchInput = document.getElementById("docsSearchInput");
  const searchResults = document.getElementById("searchResultsList");
  const searchTriggerBtn = document.getElementById("searchTriggerBtn");

  function openSearch() {
    searchModal.style.display = "flex";
    searchInput.value = "";
    renderResults(searchIndex.slice(0, 5));
    setTimeout(() => searchInput.focus(), 50);
  }

  function closeSearch() {
    searchModal.style.display = "none";
  }

  searchTriggerBtn?.addEventListener("click", openSearch);

  searchModal?.addEventListener("click", (e) => {
    if (e.target === searchModal) closeSearch();
  });

  let selectedIdx = 0;

  function renderResults(items) {
    if (!items.length) {
      searchResults.innerHTML = `<div style="padding:16px 18px; color:var(--muted); font-size:0.86rem;">No documentation matching your query.</div>`;
      return;
    }

    selectedIdx = 0;
    searchResults.innerHTML = items.map((item, idx) => `
      <a href="#${item.id}" class="search-result-item ${idx === 0 ? "selected" : ""}" data-idx="${idx}">
        <div class="search-result-breadcrumb">${item.category}</div>
        <div class="search-result-title">${item.title}</div>
        <div class="search-result-snippet">${item.snippet}</div>
      </a>
    `).join("");

    searchResults.querySelectorAll(".search-result-item").forEach(item => {
      item.addEventListener("click", () => {
        closeSearch();
      });
    });
  }

  searchInput?.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    if (!q) {
      renderResults(searchIndex.slice(0, 5));
      return;
    }

    const matches = searchIndex.filter(item => {
      return item.title.toLowerCase().includes(q) ||
             item.snippet.toLowerCase().includes(q) ||
             item.category.toLowerCase().includes(q);
    });

    renderResults(matches.slice(0, 8));
  });

  searchInput?.addEventListener("keydown", (e) => {
    const items = searchResults.querySelectorAll(".search-result-item");
    if (!items.length) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[selectedIdx]?.classList.remove("selected");
      selectedIdx = (selectedIdx + 1) % items.length;
      items[selectedIdx]?.classList.add("selected");
      items[selectedIdx]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[selectedIdx]?.classList.remove("selected");
      selectedIdx = (selectedIdx - 1 + items.length) % items.length;
      items[selectedIdx]?.classList.add("selected");
      items[selectedIdx]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (items[selectedIdx]) {
        items[selectedIdx].click();
        closeSearch();
      }
    } else if (e.key === "Escape") {
      closeSearch();
    }
  });

  window.openDocsSearch = openSearch;
  window.closeDocsSearch = closeSearch;
}

// ------------------------------------------------------------- 4. Keyboard Shortcuts
function setupKeyboardShortcuts() {
  window.addEventListener("keydown", (e) => {
    // ⌘K or Ctrl+K opens search
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      window.openDocsSearch();
    }
    // '/' opens search when not in input
    if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
      e.preventDefault();
      window.openDocsSearch();
    }
    // ESC closes search
    if (e.key === "Escape") {
      window.closeDocsSearch();
    }
  });
}
