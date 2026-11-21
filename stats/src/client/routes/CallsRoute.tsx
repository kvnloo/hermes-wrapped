import { useMemo, useState } from "react";
import { getCallsDashboardStats } from "../api";
import { Explain } from "../insights/Summary";
import { Legend, TimeChart, useHiddenSeries } from "../charts";
import { SERIES_COLORS } from "../data/colors";
import { formatElapsed, formatInteger, formatPercent, formatRelativeTime } from "../data/formatters";
import { useQuery } from "../data/query";
import { bucketAxis, rangeMeta } from "../data/range";
import { densify } from "../data/series";
import type { CallsDashboardStats, Quantiles, TimeRange, VoiceCallRow, VoiceLaneStats } from "../types";
import {
	Badge,
	Card,
	ChartSkeleton,
	type Column,
	EmptyState,
	LabelCell,
	PageHeader,
	QueryView,
	Segmented,
	Stat,
	StatGrid,
	Table,
	TableSkeleton,
} from "../ui";

export interface CallsRouteProps {
	active: boolean;
	range: TimeRange;
}

type ActivityMetric = "turns" | "handoffs" | "calls";

const ACTIVITY_OPTIONS = [
	{ value: "turns" as const, label: "Turns" },
	{ value: "handoffs" as const, label: "Hand-offs" },
	{ value: "calls" as const, label: "Calls" },
];

const TURN_GAP = "End of the caller's speech to the first assistant audio, per committed user turn (z0live trace_report eos_to_audio)";
const FIRST_ANSWER = "Hand-off start to the first lane answer spoken back to the caller";
const HERMES_ANSWER = "Hand-off start to Hermes's answer, on the voice adapter's monotonic clock";
const NO_CALLS = <EmptyState title="No z0live calls in this range" hint="Pick a wider range in the top bar." />;

/** Seconds as the dashboard's duration style ("1.8s"); "-" when unmeasured. */
function seconds(value: number | null): string {
	if (value === null) return "-";
	return value < 60 ? `${value.toFixed(value < 10 ? 2 : 1)}s` : formatElapsed(value * 1000);
}

function p90Hint(q: Quantiles): string {
	return q.n > 0 ? `p90 ${seconds(q.p90)} · ${formatInteger(q.n)} timed` : "nothing timed";
}

export function CallsRoute({ active, range }: CallsRouteProps) {
	const query = useQuery(["calls", range], ({ signal }) => getCallsDashboardStats(range, signal), { enabled: active });
	const [metric, setMetric] = useState<ActivityMetric>("turns");
	const meta = rangeMeta(range);
	const isEmpty = (data: CallsDashboardStats) => data.overall.calls === 0;

	const series = useMemo(() => {
		const points = query.data?.series ?? [];
		const buckets = bucketAxis(
			range,
			points.map(p => p.timestamp),
		);
		return {
			buckets,
			turns: densify(points, buckets, p => p.turns),
			handoffs: densify(points, buckets, p => p.handoffs),
			calls: densify(points, buckets, p => p.calls),
		};
	}, [query.data, range]);

	const chartSeries = [
		{
			key: metric,
			label: ACTIVITY_OPTIONS.find(o => o.value === metric)?.label ?? metric,
			color: metric === "handoffs" ? "var(--chart-secondary)" : "var(--chart-primary)",
			values: series[metric],
		},
	];

	return (
		<div className="page">
			<PageHeader
				title="Calls"
				description={`Voice calls z0live handled in ${meta.windowLabel}: how fast it answered, and how hand-offs to Hermes went.`}
			/>

			<QueryView query={query} skeleton={<ChartSkeleton height={112} />} isEmpty={isEmpty} empty={NO_CALLS}>
				{({ overall }) => (
					<div data-stale={query.stale} className="stack" style={{ gap: 16 }}>
						<StatGrid min={190}>
							<Stat
								label="Calls"
								value={formatInteger(overall.calls)}
								hint={`${formatElapsed(overall.talkSeconds * 1000)} talk time${overall.open > 0 ? ` · ${overall.open} open` : ""}`}
								spark={series.calls}
							/>
							<Stat
								label="User turns"
								value={formatInteger(overall.userTurns)}
								hint={`${formatInteger(overall.handoffs)} hand-offs to Hermes`}
								spark={series.turns}
							/>
							<Stat label="Turn gap p50" title={TURN_GAP} value={seconds(overall.turnGap.p50)} hint={p90Hint(overall.turnGap)} />
							<Stat
								label="First answer p50"
								title={FIRST_ANSWER}
								value={seconds(overall.firstAnswer.p50)}
								hint={p90Hint(overall.firstAnswer)}
								sparkColor="var(--chart-secondary)"
							/>
							<Stat
								label="Hermes answer p50"
								title={HERMES_ANSWER}
								value={seconds(overall.hermesAnswer.p50)}
								hint={p90Hint(overall.hermesAnswer)}
							/>
						</StatGrid>
						<StatGrid min={140}>
							<Stat size="sm" label="Hand-offs" value={formatInteger(overall.handoffs)} />
							<Stat size="sm" label="Answered by Hermes" value={formatInteger(overall.answered)} />
							<Stat
								size="sm"
								label="Hung up first"
								title="Hand-offs whose call closed before Hermes answered"
								value={formatInteger(overall.closedBeforeAnswer)}
							/>
							<Stat size="sm" label="Setup p50" value={seconds(overall.setup.p50)} />
							<Stat size="sm" label="Setup p90" value={seconds(overall.setup.p90)} />
							<Stat
								size="sm"
								label="Turns per call"
								value={overall.calls > 0 ? (overall.userTurns / overall.calls).toFixed(1) : "-"}
							/>
						</StatGrid>
					</div>
				)}
			</QueryView>

			<div className="grid grid-main-side">
				<Card
					index={1}
					title="Activity"
					description={`Per ${meta.bucketMs < 3_600_000 ? "5 minutes" : meta.bucketMs < 86_400_000 ? "hour" : "day"}`}
					actions={<Segmented size="sm" options={ACTIVITY_OPTIONS} value={metric} onChange={setMetric} />}
					stale={query.stale}
				>
					<QueryView query={query} skeleton={<ChartSkeleton height={260} />}>
						{() => (
							<TimeChart
								buckets={series.buckets}
								bucketMs={meta.bucketMs}
								series={chartSeries}
								height={260}
								formatTooltip={formatInteger}
							/>
						)}
					</QueryView>
				</Card>

				<Card
					index={2}
					title="Lanes"
					description="Fast, medium and slow tiers racing each hand-off"
					actions={<Explain section="calls" range={range} />}
					stale={query.stale}
				>
					<QueryView query={query} skeleton={<ChartSkeleton height={260} />}>
						{({ lanes }) => <LaneSummary lanes={lanes} />}
					</QueryView>
				</Card>
			</div>

			<Card
				index={3}
				title="Lane timings"
				description="Per tier: first token and finish after the hand-off started, who answered, who declined (SKIP) and who failed"
				flush
				stale={query.stale}
			>
				<QueryView query={query} skeleton={<TableSkeleton rows={3} />}>
					{({ lanes }) => (
						<Table
							rows={lanes}
							rowKey={row => row.tier}
							columns={LANE_COLUMNS}
							empty={<EmptyState title="No lane runs in this range" />}
							dense
						/>
					)}
				</QueryView>
			</Card>

			<Card index={4} title="Recent calls" description="Newest first. Times are medians within each call." flush stale={query.stale}>
				<QueryView query={query} skeleton={<TableSkeleton rows={8} />} isEmpty={isEmpty} empty={NO_CALLS}>
					{({ calls }) => (
						<Table
							rows={calls}
							rowKey={row => row.key}
							columns={CALL_COLUMNS}
							initialSort={{ key: "when", dir: "desc" }}
							limit={20}
							dense
						/>
					)}
				</QueryView>
			</Card>
		</div>
	);
}

function LaneSummary({ lanes }: { lanes: VoiceLaneStats[] }) {
	const [hidden, toggle] = useHiddenSeries();
	if (lanes.length === 0) return <EmptyState title="No lane runs in this range" />;
	const spoken = lanes.reduce((n, l) => n + l.spokenFirst, 0);
	return (
		<div className="stack" style={{ gap: 14 }}>
			<Legend
				items={lanes.map((lane, i) => ({
					key: lane.tier,
					label: lane.tier,
					color: SERIES_COLORS[i % SERIES_COLORS.length],
					value: `${formatInteger(lane.spokenFirst)} first`,
				}))}
				hidden={hidden}
				onToggle={toggle}
			/>
			{lanes
				.filter(lane => !hidden.has(lane.tier))
				.map((lane, i) => (
					<div key={lane.tier} className="row" style={{ justifyContent: "space-between" }}>
						<span className="row" style={{ minWidth: 0 }}>
							<span className="swatch" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
							<span className="truncate">{lane.tier}</span>
							<span className="dim num">{formatInteger(lane.runs)} runs</span>
						</span>
						<span className="row" style={{ flexShrink: 0 }}>
							<span className="dim num" title="Median time to first token">
								{seconds(lane.firstToken.p50)}
							</span>
							<span className="num" style={{ minWidth: 48, textAlign: "right" }} title="Share of first spoken answers">
								{spoken > 0 ? formatPercent(lane.spokenFirst / spoken, 0) : "–"}
							</span>
						</span>
					</div>
				))}
			<p className="micro dim">Share of first spoken answers, with each tier's median first token.</p>
		</div>
	);
}

function models(list: { model: string; count: number }[]): string {
	return list.length ? list.map(m => `${m.model} ×${m.count}`).join(", ") : "–";
}

const LANE_COLUMNS: Column<VoiceLaneStats>[] = [
	{ key: "tier", header: "Tier", render: row => <span className="mono">{row.tier}</span> },
	{ key: "runs", header: "Runs", align: "right", render: row => <span className="num">{formatInteger(row.runs)}</span> },
	{
		key: "answered",
		header: "Answered",
		align: "right",
		render: row => (
			<span className="num">
				{formatInteger(row.answered)}
				<span className="dim"> {row.runs > 0 ? formatPercent(row.answered / row.runs, 0) : ""}</span>
			</span>
		),
	},
	{
		key: "spoken",
		header: "Spoken",
		title: "First answers / follow-up updates spoken to the caller",
		align: "right",
		render: row => (
			<span className="num">
				{formatInteger(row.spokenFirst)}
				<span className="dim"> / {formatInteger(row.spokenUpdate)}</span>
			</span>
		),
	},
	{ key: "ft50", header: "First token p50", align: "right", render: row => <span className="num">{seconds(row.firstToken.p50)}</span> },
	{ key: "ft90", header: "p90", align: "right", render: row => <span className="num muted">{seconds(row.firstToken.p90)}</span> },
	{ key: "fin50", header: "Finish p50", align: "right", render: row => <span className="num">{seconds(row.finish.p50)}</span> },
	{ key: "fin90", header: "p90", align: "right", render: row => <span className="num muted">{seconds(row.finish.p90)}</span> },
	{ key: "wins", header: "Answered by", wrap: true, render: row => <span className="mono micro">{models(row.wins)}</span> },
	{ key: "skips", header: "Skipped", wrap: true, render: row => <span className="mono micro dim">{models(row.skips)}</span> },
	{ key: "fails", header: "Errors", wrap: true, render: row => <span className="mono micro dim">{models(row.failures)}</span> },
];

const REASON_TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = {
	client_closed: "ok",
	duration_limit: "warn",
	open: "neutral",
};

const CALL_COLUMNS: Column<VoiceCallRow>[] = [
	{
		key: "when",
		header: "When",
		phone: "caption",
		sort: row => row.startedAt,
		render: row => (
			<span className="muted" title={new Date(row.startedAt).toLocaleString()}>
				{formatRelativeTime(row.startedAt)}
			</span>
		),
	},
	{
		key: "model",
		header: "Model",
		phone: "title",
		render: row => <LabelCell primary={<span className="mono">{row.model}</span>} secondary={`${row.engine} · ${row.profile}`} />,
	},
	{
		key: "length",
		header: "Length",
		align: "right",
		phone: "value",
		sort: row => row.seconds ?? -1,
		render: row => <span className="num">{row.seconds === null ? "-" : formatElapsed(row.seconds * 1000)}</span>,
	},
	{ key: "turns", header: "Turns", align: "right", sort: row => row.turns, render: row => <span className="num">{formatInteger(row.turns)}</span> },
	{
		key: "handoffs",
		header: "Hand-offs",
		align: "right",
		sort: row => row.handoffs,
		render: row => <span className="num">{formatInteger(row.handoffs)}</span>,
	},
	{
		key: "gap",
		header: "Turn gap",
		title: TURN_GAP,
		align: "right",
		sort: row => row.turnGapP50 ?? -1,
		render: row => <span className="num">{seconds(row.turnGapP50)}</span>,
	},
	{
		key: "first",
		header: "First answer",
		title: FIRST_ANSWER,
		align: "right",
		sort: row => row.firstAnswerP50 ?? -1,
		render: row => <span className="num">{seconds(row.firstAnswerP50)}</span>,
	},
	{
		key: "hermes",
		header: "Hermes answer",
		title: HERMES_ANSWER,
		align: "right",
		sort: row => row.hermesAnswerP50 ?? -1,
		render: row => <span className="num">{seconds(row.hermesAnswerP50)}</span>,
	},
	{
		key: "reason",
		header: "Ended",
		align: "right",
		phone: "caption",
		render: row =>
			row.reason === null ? (
				<Badge tone="neutral">no close event</Badge>
			) : (
				<Badge tone={REASON_TONE[row.reason] ?? "neutral"}>{row.reason.replace(/_/g, " ")}</Badge>
			),
	},
];
