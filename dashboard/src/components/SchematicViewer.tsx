// A schematic you can actually get into: pan, zoom, and a full-screen
// sheet.
//
// The drawings this console shows are dense by nature — a gate-level
// cone is small cells with small pin labels, and xschem exports one
// fixed 1000x700 canvas — so a panel-sized <img> is a picture of a
// schematic rather than a schematic. Every EDA viewer solves this the
// same way, and so do maps: scroll to zoom about the pointer, drag to
// pan, a key to fit, and a way to fill the screen.
//
// Implemented as a CSS transform on the <img> rather than by reloading
// the SVG at a new size: the file is vector, the browser re-rasterises
// it on every scale step for free, and nothing goes back to the server
// when someone spins a scroll wheel.
import { useCallback, useEffect, useRef, useState } from "react";
import "./SchematicViewer.css";

const MIN_SCALE = 0.1;
const MAX_SCALE = 40;
// One wheel notch. 1.15 keeps a full sweep of the wheel inside roughly
// one decade of zoom, which is what makes "scroll to the pin you want"
// feel like one motion instead of ten.
const WHEEL_STEP = 1.15;

type Transform = { scale: number; x: number; y: number };

const IDENTITY: Transform = { scale: 1, x: 0, y: 0 };

export default function SchematicViewer({ src, alt, onClose }: {
  src: string;
  alt: string;
  /** Present when this is the full-screen sheet, absent when inline. */
  onClose?: () => void;
}) {
  const [t, setT] = useState<Transform>(IDENTITY);
  const frameRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  /** Scale that fits the whole drawing, and the offset that centres it. */
  const fit = useCallback(() => {
    const frame = frameRef.current;
    const img = imgRef.current;
    if (!frame || !img || !img.naturalWidth) return;
    const scale = Math.min(frame.clientWidth / img.naturalWidth,
                           frame.clientHeight / img.naturalHeight);
    setT({
      scale,
      x: (frame.clientWidth - img.naturalWidth * scale) / 2,
      y: (frame.clientHeight - img.naturalHeight * scale) / 2,
    });
  }, []);

  // Refit when the drawing changes and when the frame does. A viewer
  // that kept a zoom from the previous sheet would open every new one
  // somewhere arbitrary, and one that ignored a window resize would put
  // the drawing off-centre the moment anything moved.
  useEffect(() => { setT(IDENTITY); }, [src]);
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => fit());
    observer.observe(frame);
    return () => observer.disconnect();
  }, [fit]);

  const zoomAbout = useCallback((factor: number, cx: number, cy: number) => {
    setT((prev) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      const applied = scale / prev.scale;
      // Keep the point under the cursor fixed — the difference between
      // zooming into what you are looking at and zooming into the middle
      // and then hunting for it again.
      return { scale, x: cx - (cx - prev.x) * applied, y: cy - (cy - prev.y) * applied };
    });
  }, []);

  const onWheel = useCallback((e: React.WheelEvent) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect) return;
    zoomAbout(e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP,
              e.clientX - rect.left, e.clientY - rect.top);
  }, [zoomAbout]);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, ox: t.x, oy: t.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setT((prev) => ({ ...prev, x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) }));
  };
  const endDrag = () => { drag.current = null; };

  // Keys are the part people reach for once they are in a sheet: the
  // shortcuts every viewer of anything uses.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onClose) { onClose(); return; }
      const frame = frameRef.current;
      if (!frame) return;
      const cx = frame.clientWidth / 2;
      const cy = frame.clientHeight / 2;
      if (e.key === "+" || e.key === "=") zoomAbout(WHEEL_STEP * WHEEL_STEP, cx, cy);
      else if (e.key === "-" || e.key === "_") zoomAbout(1 / (WHEEL_STEP * WHEEL_STEP), cx, cy);
      else if (e.key === "0" || e.key.toLowerCase() === "f") fit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomAbout, fit, onClose]);

  return (
    <div className={onClose ? "schview schview--full" : "schview"}>
      <div
        className="schview__frame"
        ref={frameRef}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={fit}
      >
        <img
          ref={imgRef}
          className="schview__img"
          src={src}
          alt={alt}
          draggable={false}
          onLoad={fit}
          style={{
            transform: `translate(${t.x}px, ${t.y}px) scale(${t.scale})`,
            transformOrigin: "0 0",
          }}
        />
      </div>
      <div className="schview__bar">
        <button type="button" onClick={() => {
          const f = frameRef.current;
          if (f) zoomAbout(1 / (WHEEL_STEP * WHEEL_STEP), f.clientWidth / 2, f.clientHeight / 2);
        }} title="zoom out (-)">−</button>
        <span className="schview__zoom">{Math.round(t.scale * 100)}%</span>
        <button type="button" onClick={() => {
          const f = frameRef.current;
          if (f) zoomAbout(WHEEL_STEP * WHEEL_STEP, f.clientWidth / 2, f.clientHeight / 2);
        }} title="zoom in (+)">+</button>
        <button type="button" onClick={fit} title="fit (0 or F, or double-click)">fit</button>
        <span className="schview__hint">scroll to zoom · drag to pan</span>
        {onClose && (
          <button type="button" className="schview__close" onClick={onClose}
                  title="close (Esc)">close</button>
        )}
      </div>
    </div>
  );
}
