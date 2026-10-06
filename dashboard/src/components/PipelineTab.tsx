import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchReferenceDb,
  type PipelineCase,
  type ReferenceDb,
} from "../api/referenceDb";
import { groupByDesign, type DesignGroup } from "./caseGrouping";
import ClosureLedger from "./ClosureLedger";
import { useLang } from "../i18n";
import ActionCenter from "./ActionCenter";
import HowItWorks from "./HowItWorks";
import CaseCard from "./CaseCard";
import "./Tabs.css";
import "./PipelineTab.css";

// One design's runs, newest first, behind a header that says how many
// there are and how many closed. Collapsed by default for all but the
// newest design: the store holds 54 cases across 8 designs, and the
// flat list this replaces was 54 cards deep with no way to see what
// designs exist without scrolling past all of them.
function DesignGroupSectionImpl({
  group,
  defaultOpen,
  onApplied,
  focusDesign,
  focusCase,
}: {
  group: DesignGroup;
  defaultOpen: boolean;
  onApplied: () => void;
  focusDesign: string | null;
  focusCase: string | null;
}) {
  const { t } = useLang();
  const [open, setOpen] = useState(defaultOpen);

  // The Action Center jumps to a design's newest case. That case now
  // lives inside a collapsed group, so the group has to open with it or
  // the jump lands on nothing.
  useEffect(() => {
    if (focusDesign === group.design) setOpen(true);
  }, [focusDesign, group.design]);

  return (
    <section className="pipeline__group">
      <button
        className="pipeline__group-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="pipeline__group-name">
          {open ? "▾" : "▸"} {group.design}
        </span>
        <span className="pipeline__group-meta">
          {t("group_meta")
            .replace("{n}", String(group.cases.length))
            .replace("{p}", String(group.passed))
            .replace("{c}", String(group.candidates))}
        </span>
      </button>
      {open && (
        <div className="pipeline__group-body">
          <ClosureLedger cases={group.cases} />
          {group.cases.map((c, index) => (
            <CaseCard
              // The case file, not `${design}__${date}`: the store keeps
              // one dated file per design per day plus a timestamped one
              // per re-run, so the old key collided up to eleven times
              // and let one card's open state land on another case.
              key={c.file ?? `${c.design}__${c.date}__${index}`}
              pipelineCase={c}
              defaultOpen={false}
              onApplied={onApplied}
              focusDesign={!focusCase && index === 0 ? focusDesign : null}
              focusCase={focusCase}
            />
          ))}
        </div>
      )}
    </section>
  );
}

// Memoized so the 15 s poll's `lastRefresh` update does not re-render every
// group and open case card when the store has not changed.
const DesignGroupSection = memo(DesignGroupSectionImpl);

// Runs the actual agent, not just displays what it did before — the
// concrete answer to "why is this a dashboard, not the DTCO agent
// itself": this panel IS the agent's control surface. Press "run", a
// real pipeline/orchestrator.py candidate-generation-and-auto-repair
// loop spawns server-side against real OpenLane, and the panel polls
// its live status until a new reference-db case shows up below.
export default function PipelineTab({ initialDesign = null, initialCase = null }: { initialDesign?: string | null; initialCase?: string | null }) {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The db object last turned into `cases`. fetchReferenceDb returns the
  // same object when the store has not changed, and a poll that found
  // nothing new must not hand every memo and card below a fresh array.
  const shownDb = useRef<ReferenceDb | null>(null);
  const loadCases = useCallback(() => {
    return fetchReferenceDb()
      .then((db) => {
        if (db === shownDb.current) return;
        shownDb.current = db;
        const flat = Object.values(db.designs).flat();
        flat.sort((a, b) => b.date.localeCompare(a.date));
        setCases(flat);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [focusDesign, setFocusDesign] = useState<string | null>(initialDesign);
  const [focusCase, setFocusCase] = useState<string | null>(initialCase);

  useEffect(() => {
    let cancelled = false;
    loadCases().finally(() => {
      if (cancelled) return;
      setLoading(false);
      setLastRefresh(new Date());
    });
    return () => {
      cancelled = true;
    };
  }, [loadCases]);

  // Keep the console live rather than a snapshot of whenever the tab was
  // opened. reference-db is written by things this browser doesn't
  // control — an orchestrator.py run from a terminal, self_improve.py on
  // a schedule, an MCP tool call — so without this the page silently
  // showed stale state until someone reloaded. GET /reference-db is
  // mtime-cached server-side, so a poll that finds nothing new costs a
  // stat per case file rather than a re-read and re-parse.
  // A hidden tab skips the poll; the list is re-checked when it
  // becomes visible again rather than on every tick nobody sees.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      loadCases().then(() => setLastRefresh(new Date()));
    };
    const id = window.setInterval(refresh, 15000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [loadCases]);

  const designNames = useMemo(
    () => Array.from(new Set((cases ?? []).map((c) => c.design))).sort(),
    [cases]
  );
  const groups = useMemo(() => groupByDesign(cases ?? []), [cases]);

  return (
    <div className="tab">
      {!loading && !error && (
        <ActionCenter
          designs={designNames}
          cases={cases ?? []}
          lastRefresh={lastRefresh}
          onOpenCase={(d) => {
            // Re-set even if unchanged, so clicking the same design twice
            // scrolls back to it instead of doing nothing.
            setFocusDesign(null);
            setFocusCase(null);
            window.setTimeout(() => setFocusDesign(d), 0);
          }}
          onRunStarted={loadCases}
        />
      )}

      {/* Collapsed by default. It explains the loop well but is 587px of
          prose — larger than the Action Center that actually tells you
          what to do — so it sat above the answer instead of behind it. */}
      <HowItWorks cases={cases ?? []} />

      {loading && <p>{t("pipeline_loading")}</p>}
      {error && (
        <p className="tab__error">
          {error} — {t("pipeline_error_hint")}
        </p>
      )}
      {!loading && !error && groups.length === 0 && (
        <p>{t("pipeline_empty")}</p>
      )}

      <div className="pipeline__evidence-head">
        <span className="pipeline__evidence-title">{t("evidence_title")}</span>
        {/* The dropdown that used to live here filtered to one design at
            a time. The groups below are that filter, except every design
            is reachable at once and the counts are visible without
            choosing one. */}
        {/* Only once there is a store to count. The list re-polls after
            every triggered run, and rendering the count unconditionally
            put "0 runs across 0 designs" on screen during each refresh —
            a number that was never true. */}
        {cases !== null && (
          <span className="pipeline__evidence-count">
            {t("evidence_count")
              .replace("{n}", String(cases.length))
              .replace("{d}", String(groups.length))}
          </span>
        )}
      </div>

      {groups.map((group, index) => (
        <DesignGroupSection
          key={group.design}
          group={group}
          // Only the newest design opens. The store holds 54 cases
          // across 8 designs; opening all of them is the wall of cards
          // the grouping exists to remove.
          defaultOpen={index === 0}
          onApplied={loadCases}
          focusDesign={focusDesign}
          focusCase={focusCase}
        />
      ))}
    </div>
  );
}
