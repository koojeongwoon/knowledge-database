const byId = id => document.getElementById(id);
let documents = [];

if (window.mermaid) {
  mermaid.initialize({
    startOnLoad: false,
    theme: "neutral",
    securityLevel: "loose",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  });
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, '<img alt="$1" src="$2" loading="lazy">')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/(^|\s)\*([^*]+)\*/g, "$1<em>$2</em>");
}

function renderMarkdown(source) {
  // Strip frontmatter
  const unfront = source.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "");
  const lines = unfront.split("\n"), out = [];
  let code = false, isMermaid = false, mermaidCode = [], list = null, quote = false;

  const close = () => {
    if (list) { out.push(`</${list}>`); list = null; }
    if (quote) { out.push("</blockquote>"); quote = false; }
  };

  for (const rawLine of lines) {
    const line = escapeHtml(rawLine);

    if (rawLine.startsWith("```")) {
      close();
      if (!code) {
        // Start of code block
        code = true;
        const lang = rawLine.slice(3).trim().toLowerCase();
        if (lang === "mermaid") {
          isMermaid = true;
          mermaidCode = [];
        } else {
          isMermaid = false;
          out.push("<pre><code>");
        }
      } else {
        // End of code block
        code = false;
        if (isMermaid) {
          out.push(`<div class="mermaid">${mermaidCode.join("\n")}</div>`);
          isMermaid = false;
          mermaidCode = [];
        } else {
          out.push("</code></pre>");
        }
      }
      continue;
    }

    if (code) {
      if (isMermaid) {
        mermaidCode.push(rawLine); // raw content for mermaid
      } else {
        out.push(line + "\n");
      }
      continue;
    }

    const heading = rawLine.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      close();
      const n = heading[1].length;
      out.push(`<h${n}>${inline(escapeHtml(heading[2]))}</h${n}>`);
      continue;
    }

    const ul = rawLine.match(/^\s*[-*+]\s+(.+)$/), ol = rawLine.match(/^\s*\d+\.\s+(.+)$/);
    if (ul || ol) {
      const kind = ul ? "ul" : "ol";
      if (list !== kind) {
        close();
        list = kind;
        out.push(`<${kind}>`);
      }
      out.push(`<li>${inline(escapeHtml((ul || ol)[1]))}</li>`);
      continue;
    }

    if (rawLine.startsWith("> ")) {
      if (!quote) { close(); quote = true; out.push("<blockquote>"); }
      out.push(`<p>${inline(escapeHtml(rawLine.slice(2)))}</p>`);
      continue;
    }

    if (/^(-{3,}|\*{3,})$/.test(rawLine.trim())) {
      close();
      out.push("<hr>");
      continue;
    }

    if (!rawLine.trim()) {
      close();
      continue;
    }

    close();
    out.push(`<p>${inline(line)}</p>`);
  }

  close();
  if (code) {
    if (isMermaid) {
      out.push(`<div class="mermaid">${mermaidCode.join("\n")}</div>`);
    } else {
      out.push("</code></pre>");
    }
  }
  return out.join("\n");
}

function message(text, error = false) {
  const el = byId("message");
  el.textContent = text;
  el.className = `message${error ? " error" : ""}`;
}

function displayName(doc) {
  return doc.name.replace(/\.md$/i, "");
}

function renderList() {
  const query = byId("document-search").value.trim().toLowerCase();
  const list = byId("document-list");
  list.textContent = "";
  const filtered = documents.filter(doc => doc.path.toLowerCase().includes(query));
  byId("document-count").textContent = `${filtered.length}개 문서`;

  for (const doc of filtered) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "document-item";
    button.dataset.path = doc.path;
    const title = document.createElement("strong");
    title.textContent = displayName(doc);
    const path = document.createElement("span");
    path.textContent = doc.path;
    button.append(title, path);
    button.addEventListener("click", () => openDocument(doc.path, button));
    list.append(button);
  }
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = query ? "일치하는 문서가 없습니다." : "저장된 Markdown 문서가 없습니다.";
    list.append(empty);
  }
}

function applyHighlight(contentElement, highlightSnippet, query) {
  if (!contentElement) return;

  const cleanSnippet = (highlightSnippet || "").trim().slice(0, 100);
  let targetNode = null;

  if (cleanSnippet) {
    const paragraphs = contentElement.querySelectorAll("p, li, blockquote, pre");
    for (const p of paragraphs) {
      if (p.textContent.includes(cleanSnippet)) {
        targetNode = p;
        p.innerHTML = p.innerHTML.replace(
          escapeHtml(cleanSnippet),
          `<mark class="search-target-hit">${escapeHtml(cleanSnippet)}</mark>`
        );
        break;
      }
    }
  }

  if (!targetNode && query) {
    const words = query.split(/\s+/).filter(w => w.length > 1);
    if (words.length) {
      const escaped = words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
      const regex = new RegExp(`(${escaped})`, "gi");
      const paragraphs = contentElement.querySelectorAll("p, li, h1, h2, h3, h4, blockquote");
      for (const p of paragraphs) {
        if (regex.test(p.textContent)) {
          if (!targetNode) targetNode = p;
          p.innerHTML = p.innerHTML.replace(regex, '<mark class="search-target-hit">$1</mark>');
        }
      }
    }
  }

  if (targetNode) {
    setTimeout(() => {
      targetNode.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 150);
  }
}

async function renderMermaidDiagrams() {
  if (window.mermaid) {
    try {
      await mermaid.run({
        querySelector: ".mermaid",
      });
    } catch (e) {
      console.warn("Mermaid rendering warning:", e);
    }
  }
}

async function openDocument(path, button, highlightSnippet = "", query = "") {
  message("문서를 불러오는 중입니다.");
  try {
    const response = await fetch(`/api/documents/${path.split("/").map(encodeURIComponent).join("/")}`);
    if (response.status === 401) {
      location.replace("/login");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "문서를 불러오지 못했습니다.");

    document.querySelectorAll(".document-item.active").forEach(el => el.classList.remove("active"));
    if (button) button.classList.add("active");

    byId("document-meta").textContent = data.path;
    const contentEl = byId("document-content");
    contentEl.innerHTML = renderMarkdown(data.content);

    // 1. Render Mermaid Diagrams
    await renderMermaidDiagrams();

    // 2. Apply Highlight and Auto Scroll
    applyHighlight(contentEl, highlightSnippet, query);

    message("");
    const newUrl = new URL(location.href);
    newUrl.pathname = "/documents";
    newUrl.searchParams.set("path", data.path);
    if (query) newUrl.searchParams.set("q", query);
    history.replaceState(null, "", newUrl.toString());
  } catch (error) {
    message(error.message, true);
  }
}

async function loadDocuments() {
  try {
    const response = await fetch("/api/documents");
    if (response.status === 401) {
      location.replace("/login");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "문서 목록을 불러오지 못했습니다.");
    documents = data.documents;
    renderList();
    message("");

    const params = new URLSearchParams(location.search);
    const requested = params.get("path");
    const highlight = params.get("highlight") || "";
    const query = params.get("q") || "";

    const target = documents.find(doc => doc.path === requested);
    if (target) {
      const button = [...document.querySelectorAll(".document-item")].find(el => el.dataset.path === target.path);
      openDocument(target.path, button, highlight, query);
    }
  } catch (error) {
    message(error.message, true);
  }
}

byId("document-search").addEventListener("input", renderList);
byId("logout-button").addEventListener("click", async () => {
  const response = await fetch("/logout", { method: "POST" });
  const result = await response.json();
  location.assign(result.logout_url || "/logged-out");
});

loadDocuments();
