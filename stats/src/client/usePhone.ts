import { useSyncExternalStore } from "react";

/** The phone layout's breakpoint; phone.css uses the same query. */
export const PHONE_QUERY = "(max-width: 600px)";

const query = typeof matchMedia === "function" ? matchMedia(PHONE_QUERY) : null;

function subscribe(onChange: () => void): () => void {
	query?.addEventListener("change", onChange);
	return () => query?.removeEventListener("change", onChange);
}

/** Whether the phone layout applies now; re-renders when the window crosses the breakpoint. */
export function usePhone(): boolean {
	return useSyncExternalStore(subscribe, () => query?.matches ?? false);
}

export function isPhone(): boolean {
	return query?.matches ?? false;
}
