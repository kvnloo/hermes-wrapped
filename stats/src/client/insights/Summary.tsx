import { ArrowRight, Lightbulb, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, generateNarrative, getInsights, getNarrative } from "../api";
import { useQuery } from "../data/query";
import { rangeMeta } from "../data/range";
import { SNAPSHOT } from "../snapshot";
import type { Finding, InsightMetric, InsightsReport, Narrative, SentPayload, Severity, TimeRange } from "../types";
import { Badge, Drawer, JsonBlock, type Tone } from "../ui";
import { usePhone } from "../usePhone";
import "./insights.css";

/** The server's format_value, so a figure reads the same in a finding, a slot and the summary. */
export function formatMetric(m: Pick<InsightMetric, "value" | "unit">): string {
	switch (m.unit) {
		case "usd":
			return `$${m.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
		case "pct":
			return `${m.value.toFixed(1)}%`;
		case "s":
		case "h":
			return `${m.value.toFixed(1)}${m.unit}`;
		default:
			return Math.round(m.value).toLocaleString("en-US");
	}
}

export const SEVERITY_TONE: Record<Severity, Tone> = { high: "bad", medium: "warn", low: "neutral" };

export function SeverityBadge({ severity }: { severity: Severity }) {
	return <Badge tone={SEVERITY_TONE[severity]}>{severity}</Badge>;
}

/** Where a finding's link goes, in words. */
export function linkLabel(link: string): string {
	const section = link.replace(/^#\//, "").split("?")[0];
	return section === "traces" ? "Open trace" : `Open ${section.charAt(0).toUpperCase()}${section.slice(1)}`;
}

/** A finding's title and addressable cost: on the Overview it opens Insights, on Insights it scrolls to the row. */
function FindingTitle({ finding, range, compact }: { finding: Finding; range: TimeRange; compact: boolean }) {
	const body = (
		<>
			<SeverityBadge severity={finding.severity} /> <span className="truncate">{finding.title}</span>
		</>
	);
	const title = compact ? (
		<a className="insight-action-title" href={`#/insights?range=${range}`}>
			{body}
		</a>
	) : (
		<button
			type="button"
			className="insight-action-title"
			onClick={() => document.getElementById(`finding-${finding.id}`)?.scrollIntoView({ behavior: "smooth" })}
		>
			{body}
		</button>
	);
	return (
		<span className="insight-action-head">
			{title}
			{finding.impact && (
				<span className="insight-action-impact num" title={finding.impact.label}>
					{formatMetric(finding.impact)}
				</span>
			)}
		</span>
	);
}

/** "12.4 KB of aggregates and findings": what leaves the machine, in one line. */
function sentLine(sent: SentPayload, provider: string | null): string {
	const size = sent.bytes >= 1000 ? `${(sent.bytes / 1000).toFixed(1)} KB` : `${sent.bytes} B`;
	return `${size} of aggregates and findings (${sent.keys.join(", ")}) ${provider ? `to ${provider}` : ""}. No message text, prompts, folders or session keys.`;
}

export function SentPayloadBlock({ sent, provider }: { sent: SentPayload; provider: string | null }) {
	if (sent.bytes === 0) return null;
	return (
		<div className="stack insight-sent" style={{ gap: 6 }}>
			<span className="micro">Sends {sentLine(sent, provider)}</span>
			<JsonBlock data={sent.payload} title="What the model receives" initialCollapsed />
		</div>
	);
}

/**
 * The range's summary: the model's text when it passed the grounding check, else the top
 * deterministic findings with the reason. `compact` is the Overview banner.
 */
export function Summary({ range, report, compact = false }: { range: TimeRange; report: InsightsReport; compact?: boolean }) {
	const narrative = useQuery(["narrative", range], ({ signal }) => getNarrative(range, signal));
	const [generated, setGenerated] = useState<{ range: TimeRange; value: Narrative } | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [expanded, setExpanded] = useState(false);
	const current = generated?.range === range ? generated.value : narrative.data;
	const byId = new Map(report.findings.map(f => [f.id, f]));

	const generate = async () => {
		setBusy(true);
		setError(null);
		try {
			setGenerated({ range, value: await generateNarrative(range) });
		} catch (err) {
			setError(err instanceof ApiError ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	};

	if (current?.status === "verified") {
		return (
			<div className="stack insight-summary" style={{ gap: 12 }}>
				<p className="insight-summary-text" data-clamp={!expanded}>
					{current.summary}
				</p>
				{/* phone.css shows this on a phone, where the summary is clamped to four lines. */}
				<button
					type="button"
					className="btn insight-more"
					data-variant="ghost"
					data-size="sm"
					aria-expanded={expanded}
					onClick={() => setExpanded(open => !open)}
				>
					{expanded ? "Show less" : "Read all"}
				</button>
				{current.actions.length > 0 && (
					<ol className="insight-actions">
						{current.actions.map(action => {
							const finding = byId.get(action.finding);
							return (
								<li key={action.finding}>
									{finding && <FindingTitle finding={finding} range={range} compact={compact} />}
									<span className="muted">{action.text}</span>
								</li>
							);
						})}
					</ol>
				)}
				<div className="row micro" style={{ flexWrap: "wrap", gap: 6 }}>
					<Sparkles size={12} />
					<span>
						Written by {current.model ?? "a model"}
						{current.provider ? ` via ${current.provider}` : ""}. Every figure was checked against what it was sent.
					</span>
				</div>
				{!compact && <SentPayloadBlock sent={current.sent} provider={current.provider} />}
			</div>
		);
	}

	const why = (() => {
		if (!current) return null;
		switch (current.status) {
			case "unverified":
				return `The model's summary cited ${(current.rejected ?? []).slice(0, 4).join(", ")}, which is not in what it was sent, so it is hidden. These findings are computed directly.`;
			case "failed":
				return `The summary failed: ${current.reason ?? "no reason given"}. These findings are computed directly.`;
			case "unavailable":
				return SNAPSHOT ? null : `Summaries are off. ${current.reason ?? ""}`;
			case "not_generated":
				return SNAPSHOT
					? "No written summary in this snapshot (build it with --narrate). These findings are computed directly."
					: null;
		}
	})();
	const canGenerate = !SNAPSHOT && current !== null && current.provider !== null && current.status !== "unavailable";
	const top = report.findings.slice(0, 3);

	return (
		<div className="stack insight-summary" style={{ gap: 12 }}>
			{top.length === 0 ? (
				<p className="muted">Nothing to change in {rangeMeta(range).windowLabel}.</p>
			) : (
				<ol className="insight-actions">
					{top.map(f => (
						<li key={f.id}>
							<FindingTitle finding={f} range={range} compact={compact} />
							<span className="muted truncate" title={f.action}>
								{f.action}
							</span>
						</li>
					))}
				</ol>
			)}
			{why && <span className="micro">{why}</span>}
			{error && <span className="micro" style={{ color: "var(--bad)" }}>{error}</span>}
			{canGenerate && current && (
				<div className="stack" style={{ gap: 8 }}>
					<div className="row" style={{ flexWrap: "wrap" }}>
						<button type="button" className="btn" data-variant="accent" data-size="sm" onClick={generate} disabled={busy}>
							<Sparkles size={13} /> {busy ? "Writing…" : current.status === "not_generated" ? "Write a summary" : "Try again"}
						</button>
						<span className="micro">Sends {sentLine(current.sent, current.provider)}</span>
					</div>
					{!compact && <JsonBlock data={current.sent.payload} title="What the model receives" initialCollapsed />}
				</div>
			)}
		</div>
	);
}

/** Detectors whose findings and checks explain each page's charts. Deterministic: no model involved. */
const EXPLAINS: Record<string, string[]> = {
	overview: ["side-tasks", "subagent-model", "big-sessions", "period-compare"],
	costs: ["side-tasks", "subagent-model", "big-sessions"],
	errors: ["failed-turns"],
	calls: ["voice-lanes", "voice-reactions"],
	frustration: ["voice-reactions"],
	tools: ["tool-failures"],
};

/** "Explain" on a chart card: the findings and checks that bear on this page, with their numbers. */
export function Explain({ section, range }: { section: keyof typeof EXPLAINS; range: TimeRange }) {
	const [open, setOpen] = useState(false);
	const ref = useRef<HTMLDivElement>(null);
	// A phone gets a bottom sheet (Drawer closes itself on Esc and its scrim); a desktop, a popover.
	const phone = usePhone();
	const report = useQuery(["insights", range], ({ signal }) => getInsights(range, signal), { enabled: open });
	const detectors = EXPLAINS[section];

	useEffect(() => {
		if (!open || phone) return;
		const close = (e: MouseEvent | KeyboardEvent) => {
			if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
		};
		document.addEventListener("mousedown", close);
		document.addEventListener("keydown", close);
		return () => {
			document.removeEventListener("mousedown", close);
			document.removeEventListener("keydown", close);
		};
	}, [open, phone]);

	const findings = report.data?.findings.filter(f => detectors.includes(f.detector)) ?? [];
	const checks = report.data?.checks.filter(c => detectors.includes(c.detector) && c.status !== "fired") ?? [];
	const body =
		report.data === null ? (
			<span className="micro">Reading findings…</span>
		) : (
			<div className="stack" style={{ gap: 10 }}>
				{findings.length === 0 && <span className="muted">Nothing to change here in {rangeMeta(range).windowLabel}.</span>}
				{findings.map(f => (
					<ExplainItem key={f.id} finding={f} range={range} />
				))}
				{checks.map(c => (
					<div key={c.detector} className="stack" style={{ gap: 2 }}>
						<span className="explain-check">{c.label}</span>
						<span className="micro">{c.note}</span>
					</div>
				))}
				<a className="btn" data-variant="ghost" data-size="sm" href={`#/insights?range=${range}`}>
					All findings <ArrowRight size={13} />
				</a>
			</div>
		);

	return (
		<div className="explain" ref={ref}>
			<button
				type="button"
				className="btn"
				data-variant="ghost"
				data-size="sm"
				aria-expanded={open}
				onClick={() => setOpen(o => !o)}
			>
				<Lightbulb size={13} /> Explain
			</button>
			{phone ? (
				<Drawer
					open={open}
					onClose={() => setOpen(false)}
					title="What this shows"
					subtitle={`Findings and checks for ${rangeMeta(range).windowLabel}`}
				>
					{body}
				</Drawer>
			) : (
				open && (
					<div className="explain-pop" role="dialog" aria-label="What this chart shows">
						{body}
					</div>
				)
			)}
		</div>
	);
}

function ExplainItem({ finding, range }: { finding: Finding; range: TimeRange }) {
	return (
		<a className="explain-item" href={`#/insights?range=${range}`}>
			<span className="row">
				<SeverityBadge severity={finding.severity} />
				<span className="explain-title">{finding.title}</span>
			</span>
			<span className="micro">
				{finding.evidence
					.slice(0, 3)
					.map(m => `${m.label} ${formatMetric(m)}`)
					.join(" · ")}
			</span>
		</a>
	);
}
