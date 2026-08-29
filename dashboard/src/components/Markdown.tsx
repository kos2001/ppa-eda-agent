import { type ReactNode, useState } from "react";
import "./Markdown.css";
import {
  countLines,
  parseMarkdown,
  subheads,
  toSections,
  type Block,
  type Section,
} from "./markdownParse";
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

/**
 * A markdown document shown one section at a time, with a contents rail.
 *
 * Collapsing sections was the first attempt and only halved the problem:
 * open two and you are scrolling again, and the reader still has to
 * remember which of eight toggles held the thing they wanted. The rail
 * replaces scrolling with navigation — pick a section, read it whole,
 * pick the next. Each entry carries its line count and its subheadings,
 * so choosing does not require opening.
 *
 * Below three sections the rail is not worth its own width and the
 * document renders flat, which is also what a short one wants.
 */
function MarkdownDoc({
  source,
  className = "",
  railFrom = 3,
}: {
  source: string;
  className?: string;
  railFrom?: number;
}) {
  const sections = toSections(parseMarkdown(source));
  const titled = sections.filter((s) => s.title);
  const [active, setActive] = useState(0);

  if (titled.length < railFrom) {
    return (
      <div className={`md ${className}`.trim()}>
        {sections.map((s, i) => (
          <section key={`s${i}`} className="md__section md__section--flat">
            {s.title && <h2 className="md__flat-title">{inline(s.title, `t${i}`)}</h2>}
            <Blocks blocks={s.blocks} idBase={`s${i}`} />
          </section>
        ))}
      </div>
    );
  }

  // Untitled blocks (anything before the first heading) belong to the
  // section above rather than becoming a nameless rail entry.
  //
  // `blocks` is copied, not spread-shared: {...s} would alias the array
  // parseMarkdown produced, so appending here would edit the parse
  // result. Harmless today only because the parse is redone every
  // render, which is exactly the kind of thing that stops being true.
  const entries = sections.reduce<Section[]>((acc, s) => {
    if (!s.title && acc.length) acc[acc.length - 1].blocks.push(...s.blocks);
    else acc.push({ ...s, title: s.title ?? "Overview", blocks: [...s.blocks] });
    return acc;
  }, []);

  const current = entries[Math.min(active, entries.length - 1)];

  return (
    <div className={`md md--railed ${className}`.trim()}>
      <div className="md__pane" key={active}>
        <h2 className="md__pane-title">{inline(current.title!, `pt${active}`)}</h2>
        <div className="md__pane-body">
          <Blocks blocks={current.blocks} idBase={`s${active}`} />
        </div>
        {/* Linear reading still has to be possible: a contents rail is
            for jumping, and someone reading the whole request in order
            should not have to hunt the next entry in a list. */}
        <nav className="md__steps" aria-label="Section navigation">
          <button
            type="button"
            disabled={active === 0}
            onClick={() => setActive((i) => Math.max(0, i - 1))}
          >
            ← {entries[active - 1]?.title ?? ""}
          </button>
          <span className="md__steps-pos">
            {active + 1} / {entries.length}
          </span>
          <button
            type="button"
            disabled={active >= entries.length - 1}
            onClick={() => setActive((i) => Math.min(entries.length - 1, i + 1))}
          >
            {entries[active + 1]?.title ?? ""} →
          </button>
        </nav>
      </div>

      <nav className="md__rail" aria-label="Contents">
        <p className="md__rail-label">Contents</p>
        <ol>
          {entries.map((s, i) => {
            const subs = subheads(s);
            return (
              <li key={`r${i}`}>
                <button
                  type="button"
                  className={`md__rail-item${i === active ? " is-active" : ""}`}
                  aria-current={i === active ? "true" : undefined}
                  onClick={() => setActive(i)}
                >
                  <span className="md__rail-title">{s.title}</span>
                  <span className="md__rail-count">{countLines(s)} lines</span>
                </button>
                {i === active && subs.length > 0 && (
                  <ul className="md__rail-subs">
                    {subs.map((t, j) => (
                      <li key={`r${i}-${j}`}>{t}</li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
    </div>
  );
}

export default MarkdownDoc;
