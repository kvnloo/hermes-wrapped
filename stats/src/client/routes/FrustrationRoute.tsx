import { useMemo } from "react";
import { getVoiceFrustrationStats } from "../api";
import { Legend, TimeChart, useHiddenSeries } from "../charts";
import { formatInteger, formatPercent, formatRelativeTime, formatTimestamp } from "../data/formatters";
import { useQuery } from "../data/query";
import { bucketAxis, rangeMeta } from "../data/range";
import { densify } from "../data/series";
import type { ReactionCounts, TimeRange, VoiceFrustrationStats } from "../types";
import {
	Card,
	ChartSkeleton,
	type Column,
	EmptyState,
	PageHeader,
	QueryView,
	Stat,
	StatGrid,
	Table,
	TableSkeleton,
} from "../ui";

export interface FrustrationRouteProps {
	active: boolean;
	range: TimeRange;
}

/** Reaction flags z0live's detector writes on each committed user turn, in chart order. */
const FLAGS = [
	{ key: "corrections", label: "Corrections", color: "#f5b54a" },
	{ key: "frustration", label: "Frustration", color: "var(--bad)" },
	{ key: "repeats", label: "Repeats", color: "#9d7bff" },
	{ key: "bargeIns", label: "Barge-ins", color: "var(--chart-primary)" },
] as const satisfies readonly { key: keyof ReactionCounts; label: string; color: string }[];

const RATE_TITLE = "Committed user turns; reaction flags exist only where the trace writer recorded them";

function rate(count: number, of: number): string {
	return of > 0 ? `${formatPercent(count / of)} of measured turns` : "no measured turns";
}

/**
 * Frustration from z0live's per-turn reaction flags. Typed Hermes chats are
 * not judged: classifying them would mean reading message text.
 */
export function FrustrationRoute({ active, range }: FrustrationRouteProps) {
	const query = useQuery(["frustration", range], ({ signal }) => getVoiceFrustrationStats(range, signal), {
		enabled: active,
	});
	const [hidden, toggle] = useHiddenSeries();
	const meta = rangeMeta(range);
	const isEmpty = (data: VoiceFrustrationStats) => data.overall.turns === 0;
	const empty = <EmptyState title="No voice turns in this range" hint="Pick a wider range in the top bar." />;

	const chart = useMemo(() => {
		const points = query.data?.series ?? [];
		const buckets = bucketAxis(
			range,
			points.map(p => p.timestamp),
		);
		return {
			buckets,
			series: FLAGS.map(f => ({ ...f, values: densify(points, buckets, p => p[f.key]) })),
		};
	}, [query.data, range]);

	return (
		<div className="page">
			<PageHeader
				title="Frustration"
				description={`How callers reacted on z0live calls in ${meta.windowLabel}, from z0live's per-turn reaction flags. Typed Hermes chats are not judged, because that would mean reading message text.`}
			/>

			<QueryView query={query} skeleton={<ChartSkeleton height={112} />} isEmpty={isEmpty} empty={empty}>
				{({ overall, recordedSince }) => (
					<div data-stale={query.stale}>
						<StatGrid min={180}>
							<Stat
								label="User turns"
								title={RATE_TITLE}
								value={formatInteger(overall.turns)}
								hint={
									recordedSince === null
										? "no reaction flags recorded"
										: `${formatInteger(overall.recorded)} measured since ${formatTimestamp(recordedSince)}`
								}
							/>
							{FLAGS.map(f => (
								<Stat
									key={f.key}
									label={f.label}
									value={formatInteger(overall[f.key])}
									hint={rate(overall[f.key], overall.recorded)}
								/>
							))}
						</StatGrid>
					</div>
				)}
			</QueryView>

			<Card
				index={1}
				title="Reactions over time"
				description={`Per ${meta.bucketMs < 3_600_000 ? "5 minutes" : meta.bucketMs < 86_400_000 ? "hour" : "day"}, measured turns only`}
				stale={query.stale}
			>
				<QueryView query={query} skeleton={<ChartSkeleton height={240} />} isEmpty={isEmpty} empty={empty}>
					{() => (
						<div className="stack" style={{ gap: 12 }}>
							<TimeChart
								buckets={chart.buckets}
								bucketMs={meta.bucketMs}
								series={chart.series}
								hidden={hidden}
								height={240}
								formatTooltip={formatInteger}
								emptyLabel="No reactions flagged in this range"
							/>
							<Legend
								items={chart.series.map(s => ({
									key: s.key,
									label: s.label,
									color: s.color,
									value: formatInteger(s.values.reduce<number>((sum, v) => sum + (v ?? 0), 0)),
								}))}
								hidden={hidden}
								onToggle={toggle}
							/>
						</div>
					)}
				</QueryView>
			</Card>

			<Card index={2} title="By call" description="Calls with user turns in range, newest first" flush stale={query.stale}>
				<QueryView query={query} skeleton={<TableSkeleton rows={6} />} isEmpty={isEmpty} empty={empty}>
					{({ byCall }) => (
						<Table rows={byCall} rowKey={row => row.key} columns={CALL_COLUMNS} initialSort={{ key: "when", dir: "desc" }} dense />
					)}
				</QueryView>
			</Card>
		</div>
	);
}

type CallRow = VoiceFrustrationStats["byCall"][number];

const CALL_COLUMNS: Column<CallRow>[] = [
	{
		key: "when",
		header: "When",
		sort: row => row.startedAt,
		render: row => (
			<span className="muted" title={new Date(row.startedAt).toLocaleString()}>
				{formatRelativeTime(row.startedAt)}
			</span>
		),
	},
	{ key: "model", header: "Model", render: row => <span className="mono">{row.model}</span> },
	{ key: "turns", header: "Turns", align: "right", sort: row => row.turns, render: row => <span className="num">{formatInteger(row.turns)}</span> },
	{
		key: "recorded",
		header: "Measured",
		title: "Turns whose trace writer recorded reaction flags",
		align: "right",
		sort: row => row.recorded,
		render: row => <span className={row.recorded ? "num" : "num dim"}>{row.recorded ? formatInteger(row.recorded) : "not recorded"}</span>,
	},
	...FLAGS.map(
		(f): Column<CallRow> => ({
			key: f.key,
			header: f.label,
			align: "right",
			sort: row => row[f.key],
			render: row => <span className={row[f.key] ? "num" : "num dim"}>{row.recorded ? formatInteger(row[f.key]) : "–"}</span>,
		}),
	),
];
