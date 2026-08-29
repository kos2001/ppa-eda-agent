import { type ReactNode, useState } from "react";
import "./Markdown.css";
import { parseMarkdown, type Block } from "./markdownParse";
// A small renderer for the markdown this project actually generates.
//
// The review request was shown in a bare <pre>: a 10,000-character
// document with headings, nested lists and fenced tool output, squeezed
// unrendered into a 260px box at 0.66rem. Every structural cue the
// generator went to the trouble of emitting was thrown away exactly
// where a person has to read carefully and decide something.
//
// No markdown dependency is added, for two reasons. The obvious one is
// that this dashboard has three dependencies and the syntax in play is
// six constructs wide (see request_review.py and
// case_retrieval.precedent_block). The load-bearing one is that the
// document embeds raw tool output — OpenLane logs, recorded diagnoses,
// whatever a previous agent wrote into a case file. Rendering that
// through dangerouslySetInnerHTML would make any '<img onerror=...>'
// that ever landed in a log into script running in the console. This
// builds React elements instead, so the escaping is structural rather
// than something to remember.

// Inline: `code`, **bold**, and bare URLs. Deliberately not a full
// inline grammar — the generator emits these three and nothing else,
// and guessing at more would mangle tool output that merely contains
// an asterisk.
const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(https?:\/\/[^\s<>()]+)/g;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  let k = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyBase}-i${k++}`;
    if (m[1]) out.push(<code key={key}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={key}>{m[2].slice(2, -2)}</strong>);
    else
      out.push(
        <a key={key} href={m[3]} target="_blank" rel="noreferrer noopener">
          {m[3]}
        </a>,
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function Blocks({ blocks, idBase }: { blocks: Block[]; idBase: string }) {
  return (
    <>
      {blocks.map((b, i) => {
        const key = `${idBase}-${i}`;
        switch (b.kind) {
          case "heading": {
            // h1/h2 are consumed as section titles by MarkdownDoc, so
            // anything reaching here is h3+.
            const Tag = `h${Math.min(b.level, 6)}` as "h3";
            return <Tag key={key}>{inline(b.text, key)}</Tag>;
          }
          case "para":
            return <p key={key}>{inline(b.text, key)}</p>;
          case "list":
            return b.ordered ? (
              <ol key={key}>
                {b.items.map((it, j) => (
                  <li key={`${key}-${j}`}>{inline(it, `${key}-${j}`)}</li>
                ))}
              </ol>
            ) : (
              <ul key={key}>
                {b.items.map((it, j) => (
                  <li key={`${key}-${j}`}>{inline(it, `${key}-${j}`)}</li>
                ))}
              </ul>
            );
          case "code":
            return (
              <pre key={key} className="md__code">
                <code>{b.text}</code>
              </pre>
            );
          case "rule":
            return <hr key={key} />;
        }
      })}
    </>
  );
}

type Section = { title: string | null; level: number; blocks: Block[] };

function toSections(blocks: Block[]): Section[] {
  const out: Section[] = [];
  let cur: Section = { title: null, level: 0, blocks: [] };
  for (const b of blocks) {
    if (b.kind === "heading" && b.level <= 2) {
      if (cur.blocks.length || cur.title) out.push(cur);
      cur = { title: b.text, level: b.level, blocks: [] };
    } else cur.blocks.push(b);
  }
  if (cur.blocks.length || cur.title) out.push(cur);
  return out;
}

function countLines(s: Section): number {
  return s.blocks.reduce((n, b) => {
    if (b.kind === "code") return n + b.text.split("\n").length;
    if (b.kind === "list") return n + b.items.length;
    return n + 1;
  }, 0);
}

/**
 * A markdown document with its top-level sections collapsible.
 *
 * Collapsing is the answer to the scrolling, not a smaller font: the
 * document is long because it genuinely carries a lot, and shrinking it
 * only makes the same amount of text harder to read. Sections start
 * open when short, and closed when long enough to bury what follows —
 * with the line count on the toggle, so a closed section still says how
 * much is inside rather than hiding that too.
 */
function MarkdownDoc({
  source,
  className = "",
  collapseOver = 14,
}: {
  source: string;
  className?: string;
  collapseOver?: number;
}) {
  const sections = toSections(parseMarkdown(source));
  const [closed, setClosed] = useState<Set<number>>(
    () =>
      new Set(
        sections
          .map((s, i) => (s.title && countLines(s) > collapseOver ? i : -1))
          .filter((i) => i >= 0),
      ),
  );

  const toggle = (i: number) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const collapsible = sections.filter((s) => s.title).length;
  const allClosed = closed.size >= collapsible && collapsible > 0;

  return (
    <div className={`md ${className}`.trim()}>
      {collapsible > 1 && (
        <div className="md__toolbar">
          <button
            type="button"
            className="md__toolbar-btn"
            onClick={() =>
              setClosed(
                allClosed
                  ? new Set()
                  : new Set(sections.map((s, i) => (s.title ? i : -1)).filter((i) => i >= 0)),
              )
            }
          >
            {allClosed ? "Expand all" : "Collapse all"}
          </button>
        </div>
      )}
      {sections.map((s, i) => {
        if (!s.title) return <Blocks key={`s${i}`} blocks={s.blocks} idBase={`s${i}`} />;
        const isClosed = closed.has(i);
        const n = countLines(s);
        return (
          <section key={`s${i}`} className={`md__section md__section--h${s.level}`}>
            <button
              type="button"
              className="md__section-head"
              aria-expanded={!isClosed}
              onClick={() => toggle(i)}
            >
              <span className="md__chevron" aria-hidden="true">
                {isClosed ? "▸" : "▾"}
              </span>
              <span className="md__section-title">{inline(s.title, `t${i}`)}</span>
              {isClosed && <span className="md__section-count">{n} lines</span>}
            </button>
            {!isClosed && (
              <div className="md__section-body">
                <Blocks blocks={s.blocks} idBase={`s${i}`} />
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

export default MarkdownDoc;
