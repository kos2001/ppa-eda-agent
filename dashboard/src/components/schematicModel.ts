export type SheetPin = { name: string; direction: string; x: number; y: number; net: string };
export type SheetComponent = {
  name: string; symbol: string; type: string; line: number;
  attributes: Record<string, string>; defaults: Record<string, string>;
  pins: SheetPin[]; child: string | null; master: string | null;
};
export type SheetNet = {
  name: string; aliases: string[]; named: boolean;
  connections: { instance: string; pin: string; type: string; direction: string }[];
};
export type SheetInspection = {
  cell: string; source: string; source_text: string; sha256: string; modified_at: string; basis: string;
  components: SheetComponent[]; nets: SheetNet[];
  ports: { name: string; direction: string }[];
  counts: { devices: number; elements: number; nets: number; ports: number };
  symbols: { symbol: string; source: string; sha256: string }[];
  issues: { kind: string; instance?: string; line?: number; message: string }[];
};
export type SheetSelection = { kind: "instance" | "net"; name: string } | null;
export const LABEL_TYPES = new Set(["ipin", "opin", "iopin", "label", "noconn"]);

/** Only attach xschem's inert drawing primitives, never active SVG. */
export function safeSchematicSvg(text: string): SVGSVGElement {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  if (doc.querySelector("parsererror") || doc.documentElement.localName !== "svg") throw new Error("Invalid schematic SVG");
  const tags = new Set(["svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "style", "title"]);
  const attrs = new Set(["xmlns", "width", "height", "viewBox", "version", "class", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "transform", "fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity", "stroke-linecap", "stroke-linejoin", "font-size", "font-family", "font-weight", "text-anchor", "xml:space"]);
  for (const node of [doc.documentElement, ...doc.documentElement.querySelectorAll("*")]) {
    if (!tags.has(node.localName) || node.namespaceURI !== "http://www.w3.org/2000/svg") { node.remove(); continue; }
    if (node.localName === "style") {
      const css = node.textContent ?? "";
      if (/@|url\s*\(|[<>\\]/i.test(css) || css.replace(/\.l\d+\s*\{[^{}]*\}/g, "").trim()) node.remove();
    }
    for (const attr of [...node.attributes]) {
      if (!attrs.has(attr.name) || /url\s*\(|javascript:|data:/i.test(attr.value)) node.removeAttribute(attr.name);
    }
    if (node.hasAttribute("class") && !/^l\d+(\s+l\d+)*$/.test(node.getAttribute("class")!)) node.removeAttribute("class");
  }
  doc.querySelectorAll("style").forEach(node => { node.textContent = node.textContent!.replace(/\.l(\d+)/g, ".schview__drawing .l$1"); });
  return doc.documentElement as unknown as SVGSVGElement;
}
