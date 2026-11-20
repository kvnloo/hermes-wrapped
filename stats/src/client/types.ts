/**
 * Client-side type definitions.
 *
 * Shared shapes (aggregations, time-series, dashboard payloads) live in
 * `../shared-types` and are re-exported here. The types declared inline below
 * are deliberately client-only because:
 *   - `Usage` is redeclared locally so the client bundle avoids importing
 *     `@oh-my-pi/pi-ai` (the server-side AI types package).
 *   - `MessageStats.stopReason` is widened from the server's `StopReason`
 *     enum to `string`, again to keep the client free of pi-ai types.
 *   - `TimeRange`, `OverviewStats`, `ModelDashboardStats`,
 *     `CostDashboardStats` are UI-only view shapes the server never produces.
 */

import type {
	AgentTypeStats,
	AggregatedStats,
	CostTimeSeriesPoint,
	ModelPerformancePoint,
	ModelStats,
	ModelTimeSeriesPoint,
	SideChannelStats,
	TimeSeriesPoint,
} from "../shared-types";

export * from "../shared-types";

export interface Usage {
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
	totalTokens: number;
	premiumRequests?: number;
	/** Hermes records one API-equivalent total per model, not a per-component split. */
	cost: {
		total: number;
	};
}

export interface MessageStats {
	id?: number;
	sessionFile: string;
	entryId: string;
	folder: string;
	model: string;
	provider: string;
	api: string;
	timestamp: number;
	duration: number | null;
	ttft: number | null;
	stopReason: string;
	errorMessage: string | null;
	usage: Usage;
	/** Ingest found no tariff to price this request from; its zero is unknown spend. */
	costUnpriced?: boolean;
}

/** The drawer's payload. Hermes sends the metadata row only: no messages, no output. */
export type RequestDetails = MessageStats;

export type TimeRange = "1h" | "24h" | "7d" | "30d" | "90d" | "all";

export interface OverviewStats {
	overall: AggregatedStats;
	byAgentType: AgentTypeStats[];
	bySideChannel: SideChannelStats[];
	timeSeries: TimeSeriesPoint[];
	/** Hermes profiles whose stores were read. */
	profiles: string[];
	/** Read problems worth showing (unreadable store, skipped rows). */
	notices: string[];
}

export interface ModelDashboardStats {
	byModel: ModelStats[];
	modelSeries: ModelTimeSeriesPoint[];
	modelPerformanceSeries: ModelPerformancePoint[];
}

export interface ProfileCost {
	profile: string;
	cost: number;
	requests: number;
	unpricedRequests: number;
}

export interface CostDashboardStats {
	costSeries: CostTimeSeriesPoint[];
	/** API-equivalent estimate per Hermes profile, largest first. */
	byProfile: ProfileCost[];
}

/** p50/p90 in seconds over `n` samples (nearest rank, like z0live's trace_report.py). */
export interface Quantiles {
	n: number;
	p50: number | null;
	p90: number | null;
}

export interface VoiceLaneStats {
	tier: string;
	runs: number;
	answered: number;
	spokenFirst: number;
	spokenUpdate: number;
	firstToken: Quantiles;
	finish: Quantiles;
	wins: { model: string; count: number }[];
	failures: { model: string; count: number }[];
	/** Models that declined on purpose (SKIP: the question needs Hermes) or returned nothing. Not errors. */
	skips: { model: string; count: number }[];
}

export interface VoiceCallRow {
	key: string;
	startedAt: number;
	seconds: number | null;
	reason: string | null;
	engine: string;
	model: string;
	profile: string;
	turns: number;
	handoffs: number;
	turnGapP50: number | null;
	firstAnswerP50: number | null;
	hermesAnswerP50: number | null;
}

/** Payload of `GET /api/stats/calls`: z0live calls in range. */
export interface CallsDashboardStats {
	overall: {
		calls: number;
		open: number;
		talkSeconds: number;
		userTurns: number;
		handoffs: number;
		answered: number;
		closedBeforeAnswer: number;
		turnGap: Quantiles;
		firstAnswer: Quantiles;
		hermesAnswer: Quantiles;
		setup: Quantiles;
	};
	series: { timestamp: number; calls: number; turns: number; handoffs: number }[];
	lanes: VoiceLaneStats[];
	calls: VoiceCallRow[];
	notices: string[];
}

export interface ReactionCounts {
	/** Committed user turns. */
	turns: number;
	/** Turns whose writer recorded reaction flags. */
	recorded: number;
	corrections: number;
	frustration: number;
	repeats: number;
	bargeIns: number;
	/** Turns with a correction, frustration or repeat flag. */
	flagged: number;
}

/** One evidence number. `usd` is an API-equivalent estimate; values are already rounded for display. */
export interface InsightMetric {
	key: string;
	label: string;
	value: number;
	unit: "usd" | "pct" | "count" | "s" | "h";
}

export type Severity = "high" | "medium" | "low";

export interface Finding {
	id: string;
	detector: string;
	severity: Severity;
	title: string;
	evidence: InsightMetric[];
	/** What to change; names a real Hermes or z0live setting where one exists. */
	action: string;
	/** Addressable API-equivalent cost, never a promised saving. Null when only counts are honest. */
	impact: InsightMetric | null;
	/** Dashboard hash route (`#/traces?range=7d&s=...`). */
	link: string;
}

export interface InsightCheck {
	detector: string;
	label: string;
	status: "fired" | "quiet" | "needs-data";
	note: string;
}

/** Payload of `GET /api/stats/insights`: deterministic findings for one range. */
export interface InsightsReport {
	range: TimeRange;
	window: { start: number | null; end: number; days: number | null };
	historyDays: number;
	totals: { requests: number; costUsd: number; unpricedRequests: number; sessions: number };
	findings: Finding[];
	checks: InsightCheck[];
	notes: string[];
}

/** What the model received: keys, size and the payload itself. */
export interface SentPayload {
	keys: string[];
	bytes: number;
	payload: Record<string, unknown>;
}

interface LlmResultBase {
	sent: SentPayload;
	/** How the model is reached, e.g. "hermes auxiliary (wrapped_insights)". Null when none is configured. */
	provider: string | null;
	model?: string | null;
	generatedAt?: number;
	cached?: boolean;
	reason?: string;
	/** Figures, slots or findings that failed the grounding check. */
	rejected?: string[];
}

/** `GET|POST /api/insights/narrative`. Only `verified` carries the model's text. */
export type Narrative =
	| (LlmResultBase & {
			status: "verified";
			summary: string;
			actions: { finding: string; text: string }[];
	  })
	| (LlmResultBase & { status: "unverified" | "failed" | "unavailable" | "not_generated" });

/** `POST /api/insights/ask`. */
export type AskResult =
	| (LlmResultBase & { status: "verified"; question: string; answer: string; findings: string[]; recorded: boolean })
	| (LlmResultBase & { status: "unverified" | "failed" | "unavailable"; question: string });

/** Payload of `GET /api/stats/frustration`: z0live `user.reaction` flags. */
export interface VoiceFrustrationStats {
	overall: ReactionCounts;
	/** First recorded reaction in range, ms. */
	recordedSince: number | null;
	series: ({ timestamp: number } & ReactionCounts)[];
	byCall: ({ key: string; startedAt: number; model: string } & ReactionCounts)[];
}
