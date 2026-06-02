// Tiny, dependency-free Markdown → HTML renderer for notebook markdown cells.
// Supports headings, bold/italic, inline code, fenced code, lists, blockquotes,
// links, and paragraphs — enough for analysis notes. Deliberately small to stay
// faithful to the no-build, no-extra-CDN constraint; swap for a real parser if
// notebooks ever need full CommonMark.

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inline(text) {
  let t = escapeHtml(text);
  // inline code first so its contents aren't further formatted
  t = t.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    const safe = /^(https?:|\/|#|mailto:)/i.test(href) ? href : "#";
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  return t;
}

export function renderMarkdown(src) {
  const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  let listType = null; // "ul" | "ol"

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      closeList();
      const code = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      out.push(`<pre class="md-pre"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level} class="md-h md-h${level}">${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      closeList();
      out.push(`<blockquote class="md-quote">${inline(line.replace(/^\s*>\s?/, ""))}</blockquote>`);
      i++;
      continue;
    }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul || ol) {
      const want = ul ? "ul" : "ol";
      if (listType !== want) {
        closeList();
        listType = want;
        out.push(`<${want} class="md-list">`);
      }
      out.push(`<li>${inline((ul || ol)[1])}</li>`);
      i++;
      continue;
    }

    if (/^\s*$/.test(line)) {
      closeList();
      i++;
      continue;
    }

    // paragraph: gather consecutive non-blank, non-structural lines
    closeList();
    const para = [line];
    i++;
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !/^```/.test(lines[i]) &&
      !/^\s*[-*+]\s/.test(lines[i]) &&
      !/^\s*\d+\.\s/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  closeList();
  return out.join("\n");
}
