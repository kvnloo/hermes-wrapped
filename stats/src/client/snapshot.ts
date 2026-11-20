/**
 * Static snapshot mode. `hermes-wrapped stats --snapshot` inlines every payload the pages ask
 * for under `window.__HERMES_STATS__`; the API layer answers from it instead of the network,
 * and range math uses the snapshot's clock so a page opened later still shows its own window.
 */

import type { MessageStats, SessionTrace } from "./types";

interface Snapshot {
	generatedAt: number;
	lastWriteAt: number | null;
	api: Record<string, unknown>;
}

declare global {
	interface Window {
		__HERMES_STATS__?: Snapshot;
	}
}

export const SNAPSHOT: Snapshot | null = typeof window !== "undefined" ? (window.__HERMES_STATS__ ?? null) : null;

/** The dashboard's "now": the snapshot's build time, else the wall clock. */
export function clockNow(): number {
	return SNAPSHOT?.generatedAt ?? Date.now();
}

function list<T>(key: string): T[] {
	const value = SNAPSHOT?.api[key];
	return Array.isArray(value) ? (value as T[]) : [];
}

/** Resolve an API endpoint from the snapshot, or `undefined` when the snapshot lacks it. */
export function fromSnapshot(endpoint: string): unknown {
	if (!SNAPSHOT) return undefined;
	const url = new URL(endpoint, "http://snapshot.invalid");
	const params = url.searchParams;
	const exact = SNAPSHOT.api[`${url.pathname}${url.search}`];
	if (exact !== undefined) return exact;
	const limit = Number(params.get("limit") ?? Infinity);
	switch (url.pathname) {
		case "/api/stats/recent":
		case "/api/sessions":
			return list(url.pathname).slice(0, limit);
		case "/api/stats/errors":
			return list(`/api/stats/errors?range=${params.get("range")}`).slice(0, limit);
		case "/api/session/entry": {
			const trace = SNAPSHOT.api[`/api/session/trace?file=${params.get("file")}`] as SessionTrace | undefined;
			const span = trace?.tracks.flatMap(t => t.spans).find(s => s.entryId === params.get("id"));
			return span ? { entry: { kind: span.kind, label: span.label, start: span.start, end: span.end } } : undefined;
		}
	}
	if (url.pathname.startsWith("/api/request/")) {
		const id = Number(url.pathname.slice("/api/request/".length));
		const pools = Object.keys(SNAPSHOT.api).filter(k => k.startsWith("/api/stats/errors") || k === "/api/stats/recent");
		for (const key of pools) {
			const row = list<MessageStats>(key).find(r => r.id === id);
			if (row) return row;
		}
	}
	return undefined;
}
