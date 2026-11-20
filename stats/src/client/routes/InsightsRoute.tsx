import { ArrowRight, CircleCheck, CircleDashed, CircleDot, MessageCircleQuestion } from "lucide-react";
import { type FormEvent, useState } from "react";
import { ApiError, askInsights, getInsights } from "../api";
import { formatCost, formatInteger } from "../data/formatters";
import { useQuery } from "../data/query";
import { rangeMeta } from "../data/range";
import { formatMetric, linkLabel, SentPayloadBlock, SeverityBadge, Summary } from "../insights/Summary";
import { SNAPSHOT } from "../snapshot";
import type { AskResult, Finding, InsightCheck, InsightsReport, TimeRange } from "../types";
import { Card, ChartSkeleton, PageHeader, QueryView } from "../ui";

export interface InsightsRouteProps {
	active: boolean;
	range: TimeRange;
}

const CHECK_ICON = { fired: CircleDot, quiet: CircleCheck, "needs-data": CircleDashed } as const;
const CHECK_WORD = { fired: "Found", quiet: "Fine", "needs-data": "Waiting" } as const;

export function InsightsRoute({ active, range }: InsightsRouteProps) {
	const report = useQuery(["insights", range], ({ signal }) => getInsights(range, signal), { enabled: active });
	const meta = rangeMeta(range);

	return (
		<div className="page">
			<PageHeader
				title="What to change"
				description={`Findings from ${meta.windowLabel}, computed from your records. Dollar figures are API-equivalent estimates. A model may summarize them; it sees only these numbers.`}
			/>

			<QueryView query={report} skeleton={<ChartSkeleton height={160} />}>
				{data => (
					<div className="stack" style={{ gap: 16 }} data-stale={report.stale}>
						<Card index={0} title="Summary" description={summaryLine(data)} stale={report.stale}>
							<Summary range={range} report={data} />
						</Card>

						<Card
							index={1}
							title={`Findings (${data.findings.length})`}
							description="Most severe and costly first. Each names what to change and where to look."
							flush
						>
							{data.findings.length === 0 ? (
								<p className="muted" style={{ padding: "4px 16px 16px" }}>
									Nothing to change in {meta.windowLabel}. The checks below say what was looked at.
								</p>
							) : (
								<ul className="insight-list">
									{data.findings.map(f => (
										<FindingRow key={f.id} finding={f} />
									))}
								</ul>
							)}
						</Card>

						<div className="grid grid-main-side">
							<Ask range={range} />
							<Card index={3} title="What was checked" description="A quiet check is a result too.">
								<ul className="insight-checks">
									{data.checks.map(c => (
										<CheckRow key={c.detector} check={c} />
									))}
								</ul>
								{data.notes.map(note => (
									<p key={note} className="micro" style={{ marginTop: 10 }}>
										{note}
									</p>
								))}
							</Card>
						</div>
					</div>
				)}
			</QueryView>
		</div>
	);
}

function summaryLine(data: InsightsReport): string {
	const cost = formatCost(data.totals.costUsd);
	return `${formatInteger(data.totals.requests)} requests, ${cost} API-equivalent, ${formatInteger(data.totals.sessions)} sessions. History: ${data.historyDays} days.`;
}

function FindingRow({ finding }: { finding: Finding }) {
	return (
		<li className="insight-row" id={`finding-${finding.id}`}>
			<div className="insight-row-head">
				<SeverityBadge severity={finding.severity} />
				<span className="insight-row-title">{finding.title}</span>
				{finding.impact && (
					<span className="insight-impact" title={finding.impact.label}>
						<span className="num">{formatMetric(finding.impact)}</span>
						<span className="micro">addressable</span>
					</span>
				)}
			</div>
			<dl className="insight-evidence">
				{finding.evidence.map(m => (
					<div key={m.key}>
						<dt>{m.label}</dt>
						<dd className="num">{formatMetric(m)}</dd>
					</div>
				))}
			</dl>
			<p className="insight-do">
				<span className="insight-do-label">Do</span>
				{finding.action}
			</p>
			<a className="btn insight-link" data-variant="ghost" data-size="sm" href={finding.link}>
				{linkLabel(finding.link)} <ArrowRight size={13} />
			</a>
		</li>
	);
}

function CheckRow({ check }: { check: InsightCheck }) {
	const Icon = CHECK_ICON[check.status];
	return (
		<li className="insight-check" data-status={check.status}>
			<Icon size={14} aria-hidden />
			<div className="stack" style={{ gap: 2 }}>
				<span>
					{check.label} <span className="dim">· {CHECK_WORD[check.status]}</span>
				</span>
				<span className="micro">{check.note}</span>
			</div>
		</li>
	);
}

const EXAMPLES = ["Where did most of the cost go?", "Which change saves the most?", "Why is the slow lane slow?"];

/** Ask over the same payload as the summary, with the same grounding check. Live server only. */
function Ask({ range }: { range: TimeRange }) {
	const [question, setQuestion] = useState("");
	const [busy, setBusy] = useState(false);
	const [result, setResult] = useState<AskResult | null>(null);
	const [error, setError] = useState<string | null>(null);

	const submit = async (e: FormEvent) => {
		e.preventDefault();
		if (!question.trim()) return;
		setBusy(true);
		setError(null);
		try {
			setResult(await askInsights(range, question));
		} catch (err) {
			setError(err instanceof ApiError ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	};

	return (
		<Card
			index={2}
			title={
				<>
					<MessageCircleQuestion size={15} /> Ask about this window
				</>
			}
			description="Answered from the findings and totals only. Figures that are not in them are refused."
		>
			{SNAPSHOT ? (
				<p className="muted">This snapshot is read-only. Run `hermes wrapped stats` to ask questions.</p>
			) : (
				<form className="stack" style={{ gap: 10 }} onSubmit={submit}>
					<div className="row">
						<input
							className="input"
							style={{ flex: 1 }}
							value={question}
							maxLength={500}
							placeholder={EXAMPLES[0]}
							onChange={e => setQuestion(e.target.value)}
							aria-label="Question"
						/>
						<button type="submit" className="btn" data-variant="accent" disabled={busy || !question.trim()}>
							{busy ? "Asking…" : "Ask"}
						</button>
					</div>
					<div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
						{EXAMPLES.map(q => (
							<button key={q} type="button" className="btn" data-size="sm" data-variant="ghost" onClick={() => setQuestion(q)}>
								{q}
							</button>
						))}
					</div>
					{error && <span className="micro" style={{ color: "var(--bad)" }}>{error}</span>}
					{result && <Answer result={result} />}
				</form>
			)}
		</Card>
	);
}

function Answer({ result }: { result: AskResult }) {
	if (result.status === "verified") {
		return (
			<div className="stack insight-answer" style={{ gap: 8 }}>
				<p>{result.answer}</p>
				<span className="micro">
					{result.recorded ? "From the findings and totals" : "Not recorded by Hermes"} · {result.model ?? "model"}
					{result.provider ? ` via ${result.provider}` : ""}
				</span>
				<SentPayloadBlock sent={result.sent} provider={result.provider} />
			</div>
		);
	}
	const why =
		result.status === "unverified"
			? `The answer cited ${(result.rejected ?? []).slice(0, 4).join(", ")}, which is not in the data it was given, so it is hidden.`
			: (result.reason ?? "No answer.");
	return <p className="micro insight-answer">{why}</p>;
}
