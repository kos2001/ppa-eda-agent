// The parsing half of the markdown renderer, split out from
// Markdown.tsx so it can be run directly by node — importing the
// component pulls in "./Markdown.css", which Vite resolves and node
// does not, and a parser that cannot be executed outside a browser
// build is a parser that does not get tested against real documents.
//
export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "para"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "rule" };

const H = /^(#{1,6})\s+(.*)$/;
const BULLET = /^[-*]\s+(.*)$/;
const NUMBERED = /^(\d+)[.)]\s+(.*)$/;
const RULE = /^\s*(-{3,}|_{3,}|\*{3,})\s*$/;

export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) out.push({ kind: "para", text: para.join(" ") });
    para = [];
  };
  const flushList = () => {
    if (list) out.push({ kind: "list", ...list });
    list = null;
  };
  const flush = () => {
    flushPara();
    flushList();
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code first: everything inside is literal, including lines
    // that would otherwise read as headings or bullets. A log full of
    // '- ' lines rendered as a list is how this goes wrong.
    if (/^\s*```/.test(line)) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      out.push({ kind: "code", text: body.join("\n") });
      continue;
    }

    const h = H.exec(line);
    if (h) {
      flush();
      out.push({ kind: "heading", level: h[1].length, text: h[2].trim() });
      continue;
    }

    // Before the bullet test: '---' matches neither cleanly otherwise.
    if (RULE.test(line)) {
      flush();
      out.push({ kind: "rule" });
      continue;
    }

    const b = BULLET.exec(line);
    const n = NUMBERED.exec(line);
    if (b || n) {
      flushPara();
      const ordered = Boolean(n);
      const text = (b ? b[1] : n![2]).trim();
      if (list && list.ordered === ordered) list.items.push(text);
      else {
        flushList();
        list = { ordered, items: [text] };
      }
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }

    // A continuation line of a list item belongs to that item, not to a
    // new paragraph — the generator wraps long bullets.
    if (list && /^\s+/.test(line)) {
      list.items[list.items.length - 1] += " " + line.trim();
      continue;
    }

    flushList();
    para.push(line.trim());
  }
  flush();
  return out;
}

