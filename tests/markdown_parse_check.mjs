// Runs the dashboard's markdown parser over a real generated review
// request and prints what it found, for test_markdown_render.py to
// assert on.
//
// The parser is TypeScript, so it is loaded through tsx. It lives in
// markdownParse.ts rather than Markdown.tsx precisely so this works:
// the component imports a stylesheet, which node cannot resolve.
//
// A thin harness on purpose — every assertion lives in the Python test,
// so the project keeps one suite and one place to read.
import { parseMarkdown } from "../dashboard/src/components/markdownParse.ts";
import { readFileSync } from "node:fs";

const src = readFileSync(process.argv[2], "utf8");
const blocks = parseMarkdown(src);

const counts = {};
for (const b of blocks) counts[b.kind] = (counts[b.kind] ?? 0) + 1;

console.log(
  JSON.stringify(
    {
      total: blocks.length,
      counts,
      headings: blocks
        .filter((b) => b.kind === "heading")
        .map((b) => ({ level: b.level, text: b.text })),
      // Code fences carry raw tool output. If the parser leaked a fence
      // marker into the body, or split one block into several, this is
      // where it shows.
      code: blocks
        .filter((b) => b.kind === "code")
        .map((b) => ({ lines: b.text.split("\n").length, text: b.text })),
      listItems: blocks
        .filter((b) => b.kind === "list")
        .flatMap((b) => b.items),
      paras: blocks.filter((b) => b.kind === "para").map((b) => b.text),
    },
    null,
    0,
  ),
);
