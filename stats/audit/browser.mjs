// The audit's browser: Playwright plus a Chromium binary, found on this machine without installing anything.
// PLAYWRIGHT_MODULE (a Playwright index.mjs) and CHROME (a chromium binary) override the search.
import fs from "node:fs";
import path from "node:path";

const HOME = process.env.HOME ?? "";

function findPlaywright() {
	const candidates = [
		process.env.PLAYWRIGHT_MODULE,
		path.join(HOME, "workspace/repos/agentsview/frontend/node_modules/playwright/index.mjs"),
		// npx caches: one directory per resolved package set.
		...(fs.existsSync(path.join(HOME, ".npm/_npx"))
			? fs.readdirSync(path.join(HOME, ".npm/_npx")).map(d => path.join(HOME, ".npm/_npx", d, "node_modules/playwright/index.mjs"))
			: []),
	];
	const found = candidates.find(p => p && fs.existsSync(p));
	if (!found) throw new Error("No Playwright found: set PLAYWRIGHT_MODULE to a playwright/index.mjs");
	return found;
}

function findChrome() {
	if (process.env.CHROME) return process.env.CHROME;
	const cache = path.join(HOME, "Library/Caches/ms-playwright");
	const shells = fs.existsSync(cache) ? fs.readdirSync(cache).filter(d => d.startsWith("chromium_headless_shell-")).sort().reverse() : [];
	for (const dir of shells) {
		const exe = path.join(cache, dir, "chrome-headless-shell-mac-arm64/chrome-headless-shell");
		if (fs.existsSync(exe)) return exe;
	}
	return undefined; // Playwright's own default
}

export const { chromium } = await import(findPlaywright());
export const executablePath = findChrome();
