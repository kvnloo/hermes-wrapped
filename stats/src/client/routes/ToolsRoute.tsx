import { X } from "lucide-react";
import { useMemo, useState } from "react";
import { getToolDashboardStats } from "../api";
import { Legend, Sparkline, TimeChart, useHiddenSeries } from "../charts";
import { buildColorLookup, OTHER_COLOR } from "../data/colors";
import {
	formatCompact,
	formatDurationMs,
	formatEstimatedCost,
	formatInteger,
	formatPercent,
	formatRelativeTime,
} from "../data/formatters";
import { useQuery } from "../data/query";
import { bucketAxis, rangeMeta } from "../data/range";
import { densify, pivotSeries } from "../data/series";
import { buildToolRows, type ToolRowView } from "../data/view-models";
import type { TimeRange, ToolDashboardStats, ToolModelStats, ToolTimeSeriesPoint, ToolUsageStats } from "../types";
import {
	Card,
	ChartSkeleton,
	type Column,
	EmptyState,
	LabelCell,
	MeterCell,
	PageHeader,
	QueryView,
	Stat,
	StatGrid,
	Swatch,
	Table,
	TableSkeleton,
} from "../ui";
import "./tools.css";

export interface ToolsRouteProps {
	active: boolean;
	range: TimeRange;
}

/** Tools stacked individually in the calls chart; the rest fold into "Other". */
const TOP_TOOLS = 6;

const ATTRIBUTION_NOTE =
	"Tokens and API-equivalent cost of each invoking turn, split evenly across that turn's tool calls";

const DURATION_NOTE = "From the calling model response to the tool result, inferred from message timestamps";

const NO_CALLS = <EmptyState title="No tool calls in this range" />;

export function ToolsRoute({ active, range }: ToolsRouteProps) {
	const tools = useQuery(["tools", range], ({ signal }) => getToolDashboardStats(range, signal), { enabled: active });
	const [hidden, toggleHidden] = useHiddenSeries();
	const [pickedTool, setToolFilter] = useState<string | null>(null);
	const meta = rangeMeta(range);

	const view = useMemo(() => buildToolsView(tools.data, range), [tools.data, range]);
	// A pick from another range only applies while that tool still has calls.
	const toolFilter = pickedTool !== null && view.toolNames.includes(pickedTool) ? pickedTool : null;

	const chartSeries = view.callSeries;
	const isEmpty = (data: ToolDashboardStats) => data.byTool.length === 0;

	return (
		<div className="page">
			<PageHeader
				title="Tools"
				description={`Which tools Hermes called in ${meta.windowLabel}, how long their results took, and what the calling turns cost.`}
			/>

			<QueryView query={tools} skeleton={<ChartSkeleton height={112} />}>
				{data => {
					const t = view.totals;
					return (
						<div data-stale={tools.stale} className="stack" style={{ gap: 16 }}>
							<StatGrid min={190}>
								<Stat
									label="Tool calls"
									value={formatInteger(t.calls)}
									hint={`${formatInteger(data.durations.samples)} timed`}
									spark={view.totalCalls}
								/>
								<Stat
									label="Distinct tools"
									value={formatInteger(t.tools)}
									hint={view.rows[0] ? `Most used: ${view.rows[0].tool}` : undefined}
								/>
								<Stat
									label="Median duration"
									title={DURATION_NOTE}
									value={formatDurationMs(data.durations.p50Ms)}
									hint={`p90 ${formatDurationMs(data.durations.p90Ms)}`}
								/>
								<Stat
									label="Attributed tokens"
									title={ATTRIBUTION_NOTE}
									value={formatCompact(Math.round(t.tokens))}
									hint={`${formatCompact(Math.round(t.output))} output`}
								/>
								<Stat
									label="Attributed cost"
									title={`${ATTRIBUTION_NOTE}; API-equivalent estimate`}
									value={formatEstimatedCost(t.cost, t.unpriced)}
									hint={
										t.unpriced > 0
											? `${formatInteger(Math.round(t.unpriced))} unpriced requests`
											: "API-equivalent"
									}
								/>
							</StatGrid>
						</div>
					);
				}}
			</QueryView>

			<Card
				index={1}
				title="Calls over time"
				description={`Per ${meta.bucketMs < 3_600_000 ? "5 minutes" : meta.bucketMs < 86_400_000 ? "hour" : "day"}, top ${TOP_TOOLS} tools stacked`}
				stale={tools.stale}
			>
				<QueryView query={tools} skeleton={<ChartSkeleton height={260} />} isEmpty={isEmpty} empty={NO_CALLS}>
					{() => (
						<div className="stack" style={{ gap: 12 }}>
							<TimeChart
								buckets={view.buckets}
								bucketMs={meta.bucketMs}
								series={chartSeries}
								hidden={hidden}
								height={260}
								formatTooltip={formatInteger}
							/>
							<Legend
								items={chartSeries.map(s => ({
									key: s.key,
									label: s.label,
									color: s.color,
									value: formatCompact(s.values.reduce<number>((sum, v) => sum + (v ?? 0), 0)),
								}))}
								hidden={hidden}
								onToggle={toggleHidden}
							/>
						</div>
					)}
				</QueryView>
			</Card>

			<Card
				index={2}
				title="By tool"
				description={`${ATTRIBUTION_NOTE}. Select a tool to break it down by model.`}
				flush
				stale={tools.stale}
			>
				<QueryView query={tools} skeleton={<TableSkeleton rows={8} />} isEmpty={isEmpty} empty={NO_CALLS}>
					{() => (
						<Table
							rows={view.rows}
							rowKey={row => row.tool}
							columns={view.toolColumns}
							initialSort={{ key: "calls", dir: "desc" }}
							limit={20}
							selectedKey={toolFilter}
							onRowClick={row => setToolFilter(prev => (prev === row.tool ? null : row.tool))}
							dense
						/>
					)}
				</QueryView>
			</Card>

			<Card
				index={3}
				title="By tool and model"
				description="Which models call which tools, and how long the results took"
				flush
				stale={tools.stale}
				actions={
					<div className="row" style={{ gap: 6 }}>
						<select
							className="input tools-filter"
							value={toolFilter ?? ""}
							onChange={e => setToolFilter(e.target.value || null)}
							aria-label="Filter by tool"
						>
							<option value="">All tools</option>
							{view.toolNames.map(name => (
								<option key={name} value={name}>
									{name}
								</option>
							))}
						</select>
						{toolFilter !== null && (
							<button
								type="button"
								className="btn"
								data-size="sm"
								data-variant="ghost"
								data-icon="true"
								title="Clear tool filter"
								aria-label="Clear tool filter"
								onClick={() => setToolFilter(null)}
							>
								<X size={13} />
							</button>
						)}
					</div>
				}
			>
				<QueryView query={tools} skeleton={<TableSkeleton rows={8} />} isEmpty={isEmpty} empty={NO_CALLS}>
					{data => <ToolModelTable rows={data.byToolModel} tool={toolFilter} colors={view.colors} />}
				</QueryView>
			</Card>
		</div>
	);
}

// ---------------------------------------------------------------------------
// View model
// ---------------------------------------------------------------------------

interface ToolTotals {
	calls: number;
	tools: number;
	tokens: number;
	output: number;
	cost: number;
	unpriced: number;
}

function buildToolsView(data: ToolDashboardStats | null, range: TimeRange) {
	const byTool = data?.byTool ?? [];
	const points = data?.series ?? [];
	const rows = buildToolRows(byTool).sort((a, b) => b.calls - a.calls);
	const colors = buildColorLookup(byTool.map(t => ({ key: t.tool, weight: t.calls })));
	const buckets = bucketAxis(
		range,
		points.map(p => p.timestamp),
	);

	const totals: ToolTotals = {
		calls: 0,
		tools: byTool.length,
		tokens: 0,
		output: 0,
		cost: 0,
		unpriced: 0,
	};
	for (const t of byTool) {
		totals.calls += t.calls;
		totals.tokens += t.totalTokensShare;
		totals.output += t.outputTokensShare;
		totals.cost += t.costShare;
		totals.unpriced += t.unpricedRequestsShare;
	}

	// Every tool keeps its table swatch color; zero slots become gaps so the
	// tooltip lists only tools active in that bucket.
	const callSeries = pivotSeries(points, {
		buckets,
		key: p => p.tool,
		value: (p: ToolTimeSeriesPoint) => p.calls,
		limit: TOP_TOOLS,
		colors,
	}).map(s => ({ ...s, values: s.values.map(v => v || null) }));
	// Unlimited pivot for the per-row sparklines.
	const trends = new Map(
		pivotSeries(points, { buckets, key: p => p.tool, value: p => p.calls }).map(s => [s.key, s.values as number[]]),
	);

	const maxCalls = rows[0]?.calls ?? 0;
	const toolColumns = buildToolColumns(maxCalls, colors, trends);

	return {
		rows,
		colors,
		buckets,
		totals,
		callSeries,
		totalCalls: densify(points, buckets, p => p.calls),
		toolNames: rows.map(r => r.tool).sort((a, b) => a.localeCompare(b)),
		toolColumns,
	};
}

function buildToolColumns(
	maxCalls: number,
	colors: ReadonlyMap<string, string>,
	trends: ReadonlyMap<string, number[]>,
): Column<ToolRowView>[] {
	return [
		{
			key: "tool",
			header: "Tool",
			sort: row => row.tool,
			render: row => (
				<span className="row" style={{ gap: 8 }}>
					<Swatch color={colors.get(row.tool) ?? OTHER_COLOR} />
					<span className="mono truncate tools-name" title={row.tool}>
						{row.tool}
					</span>
				</span>
			),
		},
		{
			key: "trend",
			header: "Trend",
			title: "Calls per bucket over the range",
			phone: false,
			render: row => {
				const values = trends.get(row.tool);
				return values && values.length > 1 ? (
					<Sparkline values={values} width={80} height={20} color={colors.get(row.tool) ?? OTHER_COLOR} />
				) : (
					<span className="dim">–</span>
				);
			},
		},
		{
			key: "calls",
			header: "Calls",
			align: "right",
			sort: row => row.calls,
			render: row => (
				<MeterCell
					value={row.calls}
					max={maxCalls}
					display={
						<span title={`${formatPercent(row.callFraction)} of all tool calls`}>
							{formatInteger(row.calls)}
							<span className="dim tools-share">{formatPercent(row.callFraction, 0)}</span>
						</span>
					}
				/>
			),
		},
		...durationColumns<ToolRowView>(),
		{
			key: "tokens",
			header: "Attr. tokens",
			title: ATTRIBUTION_NOTE,
			align: "right",
			sort: row => row.totalTokensShare,
			render: row => (
				<span className="num" title={`${formatPercent(row.tokenFraction)} of attributed tokens`}>
					{formatCompact(Math.round(row.totalTokensShare))}
					<span className="dim tools-share">{formatPercent(row.tokenFraction, 0)}</span>
				</span>
			),
		},
		{
			key: "cost",
			header: "Attr. cost",
			title: `${ATTRIBUTION_NOTE}; API-equivalent estimate`,
			align: "right",
			sort: row => row.costShare,
			render: row => (
				<span className="num" title={`${formatPercent(row.costFraction)} of attributed cost`}>
					{formatEstimatedCost(row.costShare, row.unpricedRequestsShare)}
					<span className="dim tools-share">{formatPercent(row.costFraction, 0)}</span>
				</span>
			),
		},
		{
			key: "lastUsed",
			header: "Last used",
			align: "right",
			sort: row => row.lastUsed,
			render: row => <span className="muted">{formatRelativeTime(row.lastUsed)}</span>,
		},
	];
}

/** Inferred p50/p90 from the calling response to the tool result. */
function durationColumns<T extends ToolUsageStats>(): Column<T>[] {
	return [
		{
			key: "p50",
			header: "p50",
			title: DURATION_NOTE,
			align: "right",
			sort: row => row.p50Ms ?? -1,
			render: row => <span className="num">{formatDurationMs(row.p50Ms)}</span>,
		},
		{
			key: "p90",
			header: "p90",
			title: DURATION_NOTE,
			align: "right",
			sort: row => row.p90Ms ?? -1,
			render: row => <span className="num muted">{formatDurationMs(row.p90Ms)}</span>,
		},
	];
}

// ---------------------------------------------------------------------------
// Tool × model breakdown
// ---------------------------------------------------------------------------

function ToolModelTable({
	rows,
	tool,
	colors,
}: {
	rows: ToolModelStats[];
	tool: string | null;
	colors: ReadonlyMap<string, string>;
}) {
	const filtered = useMemo(() => (tool === null ? rows : rows.filter(r => r.tool === tool)), [rows, tool]);
	const columns = useMemo<Column<ToolModelStats>[]>(() => {
		const maxCalls = filtered.reduce((max, r) => Math.max(max, r.calls), 0);
		return [
			{
				key: "tool",
				header: "Tool",
				sort: row => row.tool,
				render: row => (
					<span className="row" style={{ gap: 8 }}>
						<Swatch color={colors.get(row.tool) ?? OTHER_COLOR} />
						<span className="mono truncate tools-name" title={row.tool}>
							{row.tool}
						</span>
					</span>
				),
			},
			{
				key: "model",
				header: "Model",
				sort: row => row.model,
				render: row => (
					<LabelCell primary={<span className="mono">{row.model || "(unknown)"}</span>} secondary={row.provider} />
				),
			},
			{
				key: "calls",
				header: "Calls",
				align: "right",
				sort: row => row.calls,
				render: row => <MeterCell value={row.calls} max={maxCalls} display={formatInteger(row.calls)} />,
			},
			...durationColumns<ToolModelStats>(),
			{
				key: "tokens",
				header: "Attr. tokens",
				title: ATTRIBUTION_NOTE,
				align: "right",
				sort: row => row.totalTokensShare,
				render: row => <span className="num">{formatCompact(Math.round(row.totalTokensShare))}</span>,
			},
			{
				key: "cost",
				header: "Attr. cost",
				title: `${ATTRIBUTION_NOTE}; API-equivalent estimate`,
				align: "right",
				sort: row => row.costShare,
				render: row => <span className="num">{formatEstimatedCost(row.costShare, row.unpricedRequestsShare)}</span>,
			},
			{
				key: "lastUsed",
				header: "Last used",
				align: "right",
				sort: row => row.lastUsed,
				render: row => <span className="muted">{formatRelativeTime(row.lastUsed)}</span>,
			},
		];
	}, [filtered, colors]);

	return (
		<Table
			rows={filtered}
			rowKey={row => `${row.tool}::${row.model}::${row.provider}`}
			columns={columns}
			initialSort={{ key: "calls", dir: "desc" }}
			limit={25}
			dense
			empty={<EmptyState title={tool ? `No calls to ${tool} in this range` : "No tool calls in this range"} />}
		/>
	);
}
