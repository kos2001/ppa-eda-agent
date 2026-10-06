import { useState } from "react";
import { layoutImageUrl, type PipelineCase } from "../api/referenceDb";
import { sweptAxis } from "./caseGrouping";
import { useLang, type DictKey } from "../i18n";
import { TRANSLATED_KNOBS } from "./pipelineModel";

// The case's real rendered GDS layout, stored in reference-db by
// orchestrator.py so it survives the run directory being cleaned up.
// Deliberately shown to the *human* too, not just handed to subagents:
// arxiv.org/html/2605.06936v3 measured that a layout image improves
// diagnosis of real post-flow violations over text alone, and there's
// no reason that advantage should stop at the agent boundary.
export function CaseLayoutImage({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const { t } = useLang();
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  if (!pipelineCase.layout_image || failed) return null;
  return (
    <div className={`pipeline__layout-image ${expanded ? "pipeline__layout-image--expanded" : ""}`}>
      <span className="tab__meta-label">
        {t("pipeline_layout_image_label")}
        {pipelineCase.layout_image_tag ? ` · ${pipelineCase.layout_image_tag}` : ""}
        <span className="pipeline__layout-hint">
          {expanded ? t("pipeline_layout_collapse") : t("pipeline_layout_expand")}
        </span>
      </span>
      <img
        src={layoutImageUrl(pipelineCase.layout_image)}
        alt={`Rendered GDS layout for ${pipelineCase.design} ${pipelineCase.layout_image_tag ?? ""}`}
        loading="lazy"
        onClick={() => setExpanded((v) => !v)}
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function TopologySummary({ topology }: { topology: NonNullable<PipelineCase["topology"]> }) {
  return (
    <div className="tab__meta pipeline__topology">
      <span>
        <span className="tab__meta-label">macros</span>
        {topology.has_macros ? "yes" : "no"}
      </span>
      <span>
        <span className="tab__meta-label">modules</span>
        {topology.module_count}
      </span>
      <span>
        <span className="tab__meta-label">clock domains</span>
        {topology.clock_domain_count}
      </span>
      <span>
        <span className="tab__meta-label">power domains</span>
        {topology.power_domain_count}
      </span>
      <span>
        <span className="tab__meta-label">sequential elements (est.)</span>
        {topology.sequential_element_estimate}
      </span>
    </div>
  );
}

// The knobs a case swept, in the reader's language. Keys the dictionary
// does not carry are shown exactly as recorded rather than prettified,
// so an unfamiliar label can be grepped for in the case file.
export function AxisLabel({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const { t } = useLang();
  const axis = sweptAxis(pipelineCase);
  const candidates = pipelineCase.iterations.flatMap((i) => i.results);
  if (axis.length === 0) {
    return (
      <span className="pipeline__axis pipeline__axis--none">
        {candidates.length <= 1 ? t("axis_none") : t("axis_repeat")}
      </span>
    );
  }
  return (
    <span className="pipeline__axis">
      {axis.map((key) => {
        const dictKey = `knob_${key}` as DictKey;
        return (
          <span className="pipeline__axis-knob" key={key}>
            {dictKey in TRANSLATED_KNOBS ? t(dictKey) : key}
          </span>
        );
      })}
    </span>
  );
}
