import { useMemo, useState } from "react";
import { getRecentRequests } from "../api";
import { clockNow } from "../snapshot";
import {
	formatCompact,
	formatDurationMs,
	formatErrorRate,
	formatEstimatedCost,
	formatFolder,
	formatInteger,
	formatMessageCost,
	formatRelativeTime,
	formatTimestamp,
} from "../data/formatters";
import { useQuery } from "../data/query";
import { rangeMeta } from "../data/range";
import { type RequestStatus, requestStatus, summarizeRequests } from "../data/view-models";
import { REQUEST_STATUS } from "../ui/RequestDrawer";
import type { MessageStats, TimeRange } from "../types";
import {
	Card,
	type Column,
	Dot,
	EmptyState,
	LabelCell,
	PageHeader,
	QueryView,
	SearchInput,
	Segmented,
	Skeleton,
	Stat,
	StatGrid,
	Table,
	TableSkeleton,
} from "../ui";

export interface RequestsRouteProps {
	active: boolean;
	range: TimeRange;
	onRequestClick: (id: number) => void;
}

/** How many of the newest requests to load; "Load more" steps through these. */
const LOAD_STEPS = [500, 2_000, 10_000] as const;

type StatusFilter = "all" | RequestStatus;

export function RequestsRoute({ active, range, onRequestClick }: RequestsRouteProps) {
	const [step, setStep] = useState(0);
	const limit = LOAD_STEPS[step];
	const log = useQuery(["requests-log", limit], ({ signal }) => getRecentRequests(limit, signal), { enabled: active });
	const [search, setSearch] = useState("");
	const [status, setStatus] = useState<StatusFilter>("all");
	const meta = rangeMeta(range);

	const view = useMemo(() => {
		const rows = log.data ?? [];
		const cutoff = meta.spanMs === null ? null : clockNow() - meta.spanMs;
		const inRange = cutoff === null ? rows : rows.filter(row => row.timestamp >= cutoff);
		// The loaded window covers the whole range when the server ran out of rows
		// or the oldest loaded row is already older than the range start. Stale
		// rows belong to a smaller limit that is being replaced.
		const complete = !log.stale && (rows.length < limit || (cutoff !== null && inRange.length < rows.length));
		const counts: Record<StatusFilter, number> = { all: inRange.length, ok: 0, aborted: 0, failed: 0 };
		for (const row of inRange) counts[requestStatus(row)]++;
		return { loaded: rows.length, inRange, complete, counts, summary: summarizeRequests(inRange) };
	}, [log.data, log.stale, limit, meta.spanMs]);

	const filtered = useMemo(() => {
		const needle = search.trim().toLowerCase();
		return view.inRange.filter(
			row =>
				(status === "all" || requestStatus(row) === status) &&
				(needle === "" ||
					row.model.toLowerCase().includes(needle) ||
					row.provider.toLowerCase().includes(needle) ||
					row.folder.toLowerCase().includes(needle)),
		);
	}, [view.inRange, search, status]);

	const { summary, counts, complete } = view;
	const nextStep = step + 1 < LOAD_STEPS.length ? LOAD_STEPS[step + 1] : null;
	const loadMore =
		nextStep !== null ? (
			<button
				type="button"
				className="btn"
				data-size="sm"
				disabled={log.refreshing}
				onClick={() => setStep(step + 1)}
			>
				{log.refreshing && log.stale ? "Loading…" : `Load latest ${formatInteger(nextStep)}`}
			</button>
		) : undefined;

	const statusOptions = (["all", "ok", "aborted", "failed"] as const).map(value => ({
		value,
		label: (
			<>
				{value === "all" ? "All" : REQUEST_STATUS[value].label}{" "}
				<span className="dim num">{formatCompact(counts[value])}</span>
			</>
		),
	}));

	return (
		<div className="page">
			<PageHeader
				title="Requests"
				description={`Every model call Hermes made in ${meta.windowLabel}, newest first. Rows carry metadata only: tokens and cost are each call's share of its session's usage, durations are message gaps.`}
			/>

			<QueryView query={log} skeleton={<Skeleton height={96} style={{ borderRadius: 12 }} />}>
				{() => (
					<div data-stale={log.stale}>
						<StatGrid min={160}>
							<Stat
								label="Requests"
								value={formatInteger(summary.requests)}
								hint={
									summary.oldest === null
										? `none in ${meta.windowLabel}`
										: `since ${formatRelativeTime(summary.oldest)}`
								}
							/>
							<Stat
								label="Failed"
								value={formatInteger(summary.failed)}
								hint={`${summary.requests > 0 ? formatErrorRate(summary.failed / summary.requests) : "–"} · ${formatInteger(summary.aborted)} aborted`}
							/>
							<Stat
								label="Tokens"
								title="Total tokens across these requests"
								value={formatCompact(summary.tokens)}
							/>
							<Stat
								label="API-equivalent cost"
								title="What these requests would cost at public API rates"
								value={formatEstimatedCost(summary.cost, summary.unpriced)}
								hint={summary.unpriced > 0 ? `${formatInteger(summary.unpriced)} unpriced` : undefined}
							/>
							<Stat
								label="Median duration"
								value={formatDurationMs(summary.medianDuration)}
								title="Inferred from the gap between a response and the message before it"
								hint={`p95 ${formatDurationMs(summary.p95Duration)}`}
							/>
						</StatGrid>
					</div>
				)}
			</QueryView>

			<Card
				index={1}
				title="Request log"
				description={
					log.data === null
						? "Loading the latest requests…"
						: complete
							? `${formatInteger(filtered.length)} of ${formatInteger(summary.requests)} requests in ${meta.windowLabel}`
							: `${formatInteger(filtered.length)} of the latest ${formatInteger(summary.requests)} requests`
				}
				actions={
					<>
						<SearchInput value={search} onChange={setSearch} placeholder="Model, provider or project" />
						<Segmented
							size="sm"
							aria-label="Status"
							options={statusOptions}
							value={status}
							onChange={setStatus}
						/>
					</>
				}
				flush
				stale={log.stale}
				footer={
					log.data !== null && !complete ? (
						<>
							<span>
								Showing the latest {formatInteger(view.loaded)} requests, back to{" "}
								{summary.oldest === null ? "–" : formatTimestamp(summary.oldest)}. Older requests in{" "}
								{meta.windowLabel} are not loaded.
							</span>
							{loadMore}
						</>
					) : undefined
				}
			>
				<QueryView query={log} skeleton={<TableSkeleton rows={12} />}>
					{() => (
						<Table
							rows={filtered}
							rowKey={row => row.id ?? `${row.sessionFile}:${row.entryId}`}
							onRowClick={row => row.id !== undefined && onRequestClick(row.id)}
							columns={REQUEST_COLUMNS}
							initialSort={{ key: "time", dir: "desc" }}
							limit={100}
							dense
							empty={
								<EmptyState
									title={summary.requests === 0 ? `No requests in ${meta.windowLabel}` : "No requests match"}
									hint={summary.requests === 0 ? undefined : "Clear the search or status filter."}
								/>
							}
						/>
					)}
				</QueryView>
			</Card>
		</div>
	);
}

const REQUEST_COLUMNS: readonly Column<MessageStats>[] = [
	{
		key: "model",
		header: "Model",
		sort: row => row.model,
		render: row => <LabelCell primary={<span className="mono">{row.model}</span>} secondary={row.provider} />,
	},
	{
		key: "time",
		header: "When",
		phone: "caption",
		sort: row => row.timestamp,
		render: row => (
			<span className="muted" title={formatTimestamp(row.timestamp)}>
				{formatRelativeTime(row.timestamp)}
			</span>
		),
	},
	{
		key: "project",
		header: "Project",
		phone: "caption",
		sort: row => row.folder,
		render: row => (
			<span className="mono muted truncate" title={row.folder} style={{ display: "block", maxWidth: 180 }}>
				{formatFolder(row.folder)}
			</span>
		),
	},
	{
		key: "input",
		header: "Input",
		title: "Uncached input tokens",
		align: "right",
		phone: false,
		sort: row => row.usage.input,
		render: row => <span className="num">{formatCompact(row.usage.input)}</span>,
	},
	{
		key: "cache",
		header: "Cache read",
		title: "Cache read tokens (hover a cell for cache writes)",
		align: "right",
		phone: false,
		sort: row => row.usage.cacheRead,
		render: row => (
			<span className="num" title={`Cache write: ${formatInteger(row.usage.cacheWrite)}`}>
				{formatCompact(row.usage.cacheRead)}
			</span>
		),
	},
	{
		key: "output",
		header: "Output",
		align: "right",
		phone: false,
		sort: row => row.usage.output,
		render: row => <span className="num">{formatCompact(row.usage.output)}</span>,
	},
	{
		key: "cost",
		header: "Cost",
		title: "API-equivalent estimate",
		align: "right",
		phone: "value",
		sort: row => row.usage.cost.total,
		render: row => <span className="num">{formatMessageCost(row, 4)}</span>,
	},
	{
		key: "duration",
		header: "Duration",
		align: "right",
		phone: "caption",
		sort: row => row.duration ?? -1,
		render: row => <span className="num">{formatDurationMs(row.duration)}</span>,
	},
	{
		key: "status",
		header: "Status",
		phone: "value",
		sort: row => requestStatus(row),
		render: row => {
			const status = REQUEST_STATUS[requestStatus(row)];
			return (
				<span title={row.errorMessage ?? undefined}>
					<LabelCell
						lead={<Dot tone={status.tone} />}
						primary={status.label}
						secondary={<span className="mono">{row.stopReason}</span>}
					/>
				</span>
			);
		},
	},
];
