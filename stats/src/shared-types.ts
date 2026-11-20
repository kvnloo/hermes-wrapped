/**
 * Shared type definitions consumed by both the server-side stats code and the
 * standalone client bundle. Keep this file free of any imports from server-only
 * packages (e.g. `@oh-my-pi/pi-ai`, `bun:sqlite`) so the client can import it
 * without dragging server dependencies into its bundle.
 */

/**
 * Aggregated stats for a model or folder.
 */
export interface AggregatedStats {
	/** Total number of requests */
	totalRequests: number;
	/** Number of successful requests */
	successfulRequests: number;
	/** Number of failed requests */
	failedRequests: number;
	/** Error rate (0-1) */
	errorRate: number;
	/** Total input tokens */
	totalInputTokens: number;
	/** Total output tokens */
	totalOutputTokens: number;
	/** Total cache read tokens */
	totalCacheReadTokens: number;
	/** Total cache write tokens */
	totalCacheWriteTokens: number;
	/** Percentage of prompt input tokens served from cache (0-1). */
	cacheRate: number;
	/**
	 * Prompt-input cost saved relative to billing the same tokens uncached
	 * (0-1; negative when cache writes cost more than reads save). Null when the
	 * source records no per-component prices (Hermes stores one total per model).
	 */
	cacheSavings: number | null;
	/** Reasoning tokens (a subset of output that Hermes records separately). */
	totalReasoningTokens: number;
	/** Tool results returned to these requests. */
	totalToolCalls: number;
	/** Total cost */
	totalCost: number;
	/** Requests with token usage but no public-equivalent subscription price. */
	unpricedRequests: number;
	/** Total premium requests */
	totalPremiumRequests: number;
	/** Average duration in ms */
	avgDuration: number | null;
	/** Average TTFT in ms */
	avgTtft: number | null;
	/** Average tokens per second (output tokens / duration) */
	avgTokensPerSecond: number | null;
	/** Time range */
	firstTimestamp: number;
	lastTimestamp: number;
}

/**
 * Stats grouped by model.
 */
export interface ModelStats extends AggregatedStats {
	model: string;
	provider: string;
}

/**
 * Stats grouped by folder.
 */
export interface FolderStats extends AggregatedStats {
	folder: string;
}

/**
 * Time series data point.
 */
export interface TimeSeriesPoint {
	/** Bucket timestamp (start of hour/day) */
	timestamp: number;
	/** Request count */
	requests: number;
	/** Error count */
	errors: number;
	/** Total tokens */
	tokens: number;
	/** Total cost */
	cost: number;
}

/**
 * Model usage time series data point (daily buckets).
 */
export interface ModelTimeSeriesPoint {
	/** Bucket timestamp (start of day) */
	timestamp: number;
	/** Model name */
	model: string;
	/** Provider name */
	provider: string;
	/** Request count */
	requests: number;
}

/**
 * Model performance time series data point (daily buckets).
 */
export interface ModelPerformancePoint {
	/** Bucket timestamp (start of day) */
	timestamp: number;
	/** Model name */
	model: string;
	/** Provider name */
	provider: string;
	/** Request count */
	requests: number;
	/** Average TTFT in ms */
	avgTtft: number | null;
	/** Average tokens per second */
	avgTokensPerSecond: number | null;
}

/**
 * Cost time series data point (daily buckets).
 */
export interface CostTimeSeriesPoint {
	/** Bucket timestamp (start of day) */
	timestamp: number;
	/** Model name */
	model: string;
	/** Provider name */
	provider: string;
	/** Total cost for this bucket */
	cost: number;
	/** Requests excluded because no public-equivalent subscription price exists. */
	unpricedRequests: number;
	/** Request count */
	requests: number;
}

/**
 * One local calendar day of aggregate request activity, for the `/usage`
 * activity heatmap in the coding-agent TUI.
 */
export interface DailyActivityPoint {
	/** Local calendar date, `YYYY-MM-DD`. */
	day: string;
	/** Summed API-equivalent cost for the day. */
	cost: number;
	/** Request count for the day. */
	requests: number;
	/** Total tokens (input + output + cache) for the day. */
	totalTokens: number;
}

/**
 * Overall dashboard stats.
 */
export interface DashboardStats {
	overall: AggregatedStats;
	byModel: ModelStats[];
	byFolder: FolderStats[];
	byAgentType: AgentTypeStats[];
	bySideChannel: SideChannelStats[];
	timeSeries: TimeSeriesPoint[];
	modelSeries: ModelTimeSeriesPoint[];
	modelPerformanceSeries: ModelPerformancePoint[];
	costSeries: CostTimeSeriesPoint[];
}

/**
 * Which agent produced a message, derived from its transcript file location
 * inside the session directory: the top-level `<project>/<file>.jsonl` is the
 * `main` agent, an `__advisor.jsonl` is the passive `advisor`, and any other
 * nested transcript is a task `subagent`.
 */
export type AgentType = "main" | "subagent" | "advisor";

/**
 * Request, token and cost totals of one usage-attribution group over the
 * active range. Token columns are explicit so the dashboard's share
 * denominator matches the counts it renders (input + output + cache read +
 * cache write).
 */
export interface UsageAttributionTotals {
	/** Total number of requests */
	totalRequests: number;
	/** Total input tokens */
	totalInputTokens: number;
	/** Total output tokens */
	totalOutputTokens: number;
	/** Total cache read tokens */
	totalCacheReadTokens: number;
	/** Total cache write tokens */
	totalCacheWriteTokens: number;
	/** Total cost */
	totalCost: number;
}

/**
 * Usage of conversation turns (assistant messages, discarded ones included)
 * aggregated by {@link AgentType}. Off-transcript model calls are not in here:
 * see {@link SideChannelStats}.
 */
export interface AgentTypeStats extends UsageAttributionTotals {
	agentType: AgentType;
}

/**
 * Usage of side channels (model calls that are not conversation turns: title
 * generation, job captions, judge queries, …) aggregated per use case, apart
 * for calls served by a decision model and calls served by a language model.
 * Busiest group first.
 */
export interface SideChannelStats extends UsageAttributionTotals {
	/** Use case, such as `title` or `progress`; a purpose without a known category is its own. */
	category: string;
	/** Whether the calls ran on a decision model (an api that answers judgments natively). */
	decision: boolean;
}

/**
 * Aggregated usage for a single tool over the active range.
 *
 * Token/cost fields are the usage of the assistant turns that invoked the
 * tool, split evenly across that turn's tool calls so the numbers stay
 * additive (a turn with 3 calls contributes a third of its usage to each tool).
 * Hermes records no tool error flags or payload sizes; durations are inferred
 * from the calling response's timestamp to the tool result's.
 */
export interface ToolUsageStats {
	/** Tool name as recorded on the tool call. */
	tool: string;
	/** Number of tool calls. */
	calls: number;
	/** Median ms from the calling response to the result (inferred). */
	p50Ms: number | null;
	/** 90th-percentile ms from the calling response to the result (inferred). */
	p90Ms: number | null;
	/** Total provider tokens of invoking turns, attributed per call share. */
	totalTokensShare: number;
	/** Output tokens of invoking turns, attributed per call share. */
	outputTokensShare: number;
	/** Cost (USD) of invoking turns, attributed per call share. */
	costShare: number;
	/** Share of unpriced subscription requests attributed to this tool. */
	unpricedRequestsShare: number;
	/** Unix ms of the most recent call in range. */
	lastUsed: number;
}

/** Per-(tool, model) breakdown with the same attribution as {@link ToolUsageStats}. */
export interface ToolModelStats extends ToolUsageStats {
	model: string;
	provider: string;
}

/** Tool-call time-series point (one bucket per tool). */
export interface ToolTimeSeriesPoint {
	timestamp: number;
	tool: string;
	calls: number;
}

/** Complete tools dashboard payload. */
export interface ToolDashboardStats {
	byTool: ToolUsageStats[];
	byToolModel: ToolModelStats[];
	series: ToolTimeSeriesPoint[];
	/** Inferred durations across every timed call in range. */
	durations: { samples: number; p50Ms: number | null; p90Ms: number | null };
}

/**
 * Aggregated request/token/cost totals for one provider over the active range.
 */
export interface ProviderAggregate {
	provider: string;
	totalRequests: number;
	failedRequests: number;
	/** Distinct models used through this provider in the range. */
	models: number;
	totalInputTokens: number;
	totalOutputTokens: number;
	totalCacheReadTokens: number;
	totalCacheWriteTokens: number;
	/** Uncached input + cache reads + cache writes + output. */
	totalTokens: number;
	totalCost: number;
	/** Requests excluded because no public-equivalent subscription price exists. */
	unpricedRequests: number;
	totalPremiumRequests: number;
	avgTokensPerSecond: number | null;
}

/**
 * Token burn attributed to one local hour-of-day (0-23) for one provider.
 * Powers the "peak burn hours" histogram.
 */
export interface ProviderHourlyPoint {
	provider: string;
	/** Local hour of day, 0-23. */
	hour: number;
	totalTokens: number;
	outputTokens: number;
	requests: number;
}

/** Provider token/cost time-series point (bucketed like the model series). */
export interface ProviderTimeSeriesPoint {
	timestamp: number;
	provider: string;
	totalTokens: number;
	cost: number;
	/** Requests excluded because no public-equivalent subscription price exists. */
	unpricedRequests: number;
	requests: number;
}

/** Ingest state of the dashboard host's background session sync. */
export interface LiveSyncStatus {
	phase: "idle" | "syncing" | "error";
	/** Files completed in the running sync (0 when not yet known). */
	current: number;
	/** Files in the running sync's work set (0 while listing → indeterminate). */
	total: number;
	/** Rows inserted by the running (or last) sync. */
	processed: number;
	/** Wall-clock time the last sync finished, if any. */
	lastSyncedAt: number | null;
	/** Failure message of the last sync when `phase` is `"error"`. */
	error: string | null;
}

/**
 * Payload of every `GET /api/events` server-sent event (and `GET /api/status`).
 * `version` increases whenever stored stats may have changed, so clients
 * revalidate their queries when it moves.
 */
export interface LiveStatus {
	version: number;
	sync: LiveSyncStatus;
	/** Hours whose rollups are still being (re)built; ranges covering them may be incomplete. */
	indexingHours: number;
}

/** Providers dashboard payload: local request stats only (fast, DB-backed). */
export interface ProviderDashboardStats {
	providers: ProviderAggregate[];
	hourly: ProviderHourlyPoint[];
	series: ProviderTimeSeriesPoint[];
}

/**
 * One row of the Traces session list: a root session with every child
 * transcript (task subagents, advisors) folded in.
 */
export interface SessionSummary {
	/** Absolute root session file path (trace key). */
	file: string;
	/** Decoded project path (e.g. `/work/pi`). */
	folder: string;
	title: string | null;
	/** ms epoch of first activity. */
	startedAt: number;
	/** ms epoch of last activity, children included. */
	endedAt: number;
	/** Assistant messages, children folded in. */
	requests: number;
	toolCalls: number;
	/** Child transcript count. */
	subagents: number;
	totalTokens: number;
	costTotal: number;
	/** Requests whose zero cost is unknown spend (scheduled card, no timestamp). */
	unpricedRequests: number;
	models: string[];
}

export type TraceSpanKind = "turn" | "model" | "tool" | "subagent" | "background";

/** One rendered block on a trace track lane. */
export interface TraceSpan {
	/** `${track.id}:${entryId}` (+`:${toolCallId}` for tool spans); stable across refetch. */
	id: string;
	kind: TraceSpanKind;
	/** ms epoch. */
	start: number;
	/** ms epoch, >= start. */
	end: number;
	/** ≤80 chars: tool name / model id / user-text head / agent name. */
	label: string;
	/** ≤160 chars: args projection / result head / task text. */
	detail?: string;
	/** Journal entry id for /api/session/entry. */
	entryId?: string;
	toolCallId?: string;
	model?: string;
	/** usage.totalTokens (model spans). */
	tokens?: number;
	/** usage.cost.total (model spans). */
	cost?: number;
	/** Time to first token, ms offset from start. */
	ttft?: number;
	isError?: boolean;
	/** End synthesized (no result / pending at session exit). */
	unterminated?: boolean;
	/** Set on `subagent` spans whose child transcript became a track. */
	childTrackId?: string;
}

/** Point event drawn on a track header row. */
export interface TraceMarker {
	/** ms epoch. */
	time: number;
	kind: "compaction" | "model_change" | "mode_change" | "reset" | "session_exit";
	/** e.g. "compaction 142k→38k", model id, mode name, exit kind. */
	label: string;
}

/** One transcript (main session, subagent, advisor) in a trace. */
export interface TraceTrack {
	/** "main" or slash-joined child key: "Scout1", "Scout1/Nested2", "__advisor". */
	id: string;
	parentId: string | null;
	label: string;
	/** session_init.agent when recorded. */
	agent: string | null;
	/** session_init.resolvedModel ?? first assistant model. */
	model: string | null;
	/** Absolute transcript path for live entry fetch, or the embedded snapshot key. */
	file: string;
	/** Sorted by start. */
	spans: TraceSpan[];
	markers: TraceMarker[];
}

/** Per-tool duration aggregate across all tracks of one trace. */
export interface TraceToolStat {
	tool: string;
	calls: number;
	errors: number;
	totalMs: number;
	maxMs: number;
}

/** Headline aggregates for one trace. */
export interface TraceSummary {
	wallMs: number;
	/** Summed model-span duration on the main track. */
	modelMs: number;
	/** Summed tool-span duration on the main track. */
	toolMs: number;
	/** Wall time not covered by any span on any track. */
	idleMs: number;
	turns: number;
	requests: number;
	toolCalls: number;
	subagents: number;
	totalTokens: number;
	costTotal: number;
	/** Model requests whose zero cost is unknown spend, not free usage. */
	unpricedRequests: number;
	/** Sorted totalMs desc. */
	toolStats: TraceToolStat[];
}

/** Complete span tree for one session, `/api/session/trace` payload. */
export interface SessionTrace {
	file: string;
	title: string | null;
	cwd: string | null;
	startedAt: number;
	endedAt: number;
	/** Root transcript mtime, or recorded root activity/header time for offline snapshots. */
	mtimeMs: number;
	/**
	 * Conditional-request fingerprint `"<rootMs>:<rootSize>:<digest>"` (see
	 * traceFingerprintForEtag): the digest folds the child-transcript set into a
	 * fixed-size key, so a subagent-only append changes the ETag even when the
	 * root is untouched without making `If-None-Match` unbounded.
	 * Offline snapshots use "snapshot" and do not support HTTP revalidation.
	 */
	etag: string;
	/** DFS order, main first. */
	tracks: TraceTrack[];
	summary: TraceSummary;
}
