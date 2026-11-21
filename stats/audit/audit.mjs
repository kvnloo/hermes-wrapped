// Phone + desktop layout audit for the stats dashboard.
//
// Renders every page of a snapshot HTML in headless Chromium and records, per page x viewport x theme:
// a screenshot, horizontal overflow (scrollWidth vs clientWidth, plus the elements that stick out),
// interactive elements smaller than 44x44 CSS px, and console errors.
//
//   node stats/audit/audit.mjs --html <snapshot.html> --out <dir> [--rebundle] [--desktop] [--range 7d]
//
// --rebundle swaps the snapshot's inline CSS and JS for stats/dist, so a published snapshot's data can be
// re-rendered with new client code: before/after shots then differ only by code, never by data.
// The browser comes from browser.mjs (PLAYWRIGHT_MODULE and CHROME override it).
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium, executablePath } from "./browser.mjs";

const args = process.argv.slice(2);
const opt = name => {
	const i = args.indexOf(`--${name}`);
	return i < 0 ? undefined : args[i + 1];
};
const flag = name => args.includes(`--${name}`);
const out = path.resolve(opt("out") ?? "audit-out");
const range = opt("range") ?? "7d";
const only = opt("pages")?.split(",");
fs.mkdirSync(out, { recursive: true });

const SECTIONS = [
	"overview",
	"insights",
	"models",
	"providers",
	"costs",
	"requests",
	"errors",
	"traces",
	"calls",
	"tools",
	"frustration",
	"projects",
];
const PHONES = [
	{ name: "390", width: 390, height: 844 },
	{ name: "430", width: 430, height: 932 },
];
const DESKTOP = [{ name: "1440", width: 1440, height: 900, desktop: true }];

/** [name, page, action]: the phone states worth a look beyond each page's first screen. */
const STATES = [
	["sheet", "models", page => page.tap(".dock-tab:last-child")],
	["range", "costs", page => page.tap(".range-pill")],
	[
		"chart-tap",
		"overview",
		async page => {
			const plot = page.locator(".chart svg > rect[fill='transparent']").first();
			await plot.evaluate(el => el.scrollIntoView({ block: "center" }));
			await page.waitForTimeout(300);
			const box = await plot.boundingBox();
			await page.touchscreen.tap(box.x + box.width * 0.9, box.y + box.height * 0.6);
		},
	],
	["request", "requests", page => page.locator("tr[data-clickable='true']").first().tap()],
	[
		"explain",
		"costs",
		async page => {
			await page.locator(".explain > .btn").first().tap();
		},
	],
];

let html = path.resolve(opt("html"));
if (flag("rebundle")) html = rebundle(html, path.resolve(import.meta.dirname, "../dist"), path.join(out, "snapshot.html"));
const url = pathToFileURL(html).href;
// Relative times ("3h ago") read the wall clock; pin it an hour after the snapshot so two runs match.
const generatedAt = Number(/"generatedAt":(\d+)/.exec(fs.readFileSync(html, "utf8"))?.[1] ?? Date.now());

/** Keep a snapshot's data, take everything else from dist/: build_snapshot's replacements over dist/index.html. */
function rebundle(source, dist, target) {
	const page = fs.readFileSync(source, "utf8");
	const dataAt = page.indexOf("<script>window.__HERMES_STATS__=");
	if (dataAt < 0) throw new Error(`${source}: not a stats snapshot`);
	// The payload escapes every "</", so the first close tag after it is its own.
	const data = page.slice(dataAt, page.indexOf("</script>", dataAt) + "</script>".length);
	const css = fs.readFileSync(path.join(dist, "index.css"), "utf8");
	const js = fs.readFileSync(path.join(dist, "index.js"), "utf8").replaceAll("</script", "<\\/script");
	const html = fs
		.readFileSync(path.join(dist, "index.html"), "utf8")
		.replace('<link rel="stylesheet" href="index.css">', () => `<style>${css}</style>`)
		.replace('<link rel="icon" type="image/x-icon" href="favicon.ico">', "")
		.replace('<script src="index.js" type="module"></script>', () => `${data}\n<script type="module">${js}</script>`);
	fs.writeFileSync(target, html);
	return target;
}

/** Runs in the page: overflow, small tap targets. */
function measure() {
	const doc = document.documentElement;
	const vw = doc.clientWidth;
	const visible = el => {
		const r = el.getBoundingClientRect();
		if (r.width === 0 || r.height === 0) return null;
		const cs = getComputedStyle(el);
		if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return null;
		if (el.closest("[hidden], [aria-hidden='true'], [inert]")) return null;
		return r;
	};
	const describe = el => {
		const label = el.getAttribute("aria-label") ?? el.textContent?.trim().replace(/\s+/g, " ").slice(0, 32) ?? "";
		const cls = typeof el.className === "string" ? el.className.split(" ").filter(Boolean).slice(0, 2).join(".") : "";
		return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ""}${label ? ` "${label}"` : ""}`;
	};
	// Something sticks out only when no ancestor clips or scrolls it sideways.
	const clipped = (el, kinds = ["auto", "scroll", "hidden", "clip"]) => {
		for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
			if (kinds.includes(getComputedStyle(p).overflowX)) return true;
		}
		return false;
	};
	// Off-canvas and out of reach (a closed drawer translated away), unless a sideways scroller brings it in.
	const unreachable = (el, r) => (r.right <= 0 || r.left >= vw) && !clipped(el, ["auto", "scroll"]);
	const sticking = [];
	for (const el of document.body.querySelectorAll("*")) {
		const r = visible(el);
		if (r && (r.right > vw + 1 || r.left < -1) && !clipped(el)) sticking.push(`${describe(el)} [${Math.round(r.left)}..${Math.round(r.right)}]`);
	}
	const interactive =
		"button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=link], [tabindex]:not([tabindex='-1'])";
	const small = [];
	let targets = 0;
	for (const el of document.querySelectorAll(interactive)) {
		// A control inside its <label> is hit anywhere on the label, so the label is the target.
		const r = visible(el.matches("input") ? (el.closest("label") ?? el) : el);
		if (!r || unreachable(el, r)) continue;
		targets++;
		if (r.width < 44 || r.height < 44) small.push(`${describe(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
	}
	return {
		scrollWidth: doc.scrollWidth,
		clientWidth: vw,
		bodyScrollWidth: document.body.scrollWidth,
		overflow: doc.scrollWidth > vw || document.body.scrollWidth > vw,
		sticking: sticking.slice(0, 12),
		targets,
		small,
	};
}

const browser = await chromium.launch({ executablePath });
const report = [];
const viewports = flag("desktop") ? DESKTOP : PHONES;
const themes = (opt("themes") ?? "light,dark").split(",");

for (const vp of viewports) {
	for (const theme of themes) {
		const ctx = await browser.newContext({
			viewport: { width: vp.width, height: vp.height },
			deviceScaleFactor: vp.desktop ? 1 : 3,
			isMobile: !vp.desktop,
			hasTouch: !vp.desktop,
			reducedMotion: flag("reduced-motion") ? "reduce" : "no-preference",
		});
		// Fail a missing control fast instead of after the 30s default.
		ctx.setDefaultTimeout(5000);
		await ctx.clock.setFixedTime(new Date(generatedAt + 3_600_000));
		await ctx.addInitScript(t => {
			try {
				localStorage.setItem("hermes-stats-theme", t);
			} catch {
				// about:blank, between states, has no storage.
			}
		}, theme);
		const page = await ctx.newPage();
		let errors = [];
		page.on("console", m => m.type() === "error" && errors.push(m.text()));
		page.on("pageerror", e => errors.push(String(e)));
		for (const section of only ?? SECTIONS) {
			errors = [];
			await page.goto(`${url}#/${section}?range=${range}`);
			await page.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 15000 }).catch(() => {});
			await page.waitForTimeout(500);
			const stem = `${section}-${vp.name}-${theme}`;
			await page.screenshot({ path: path.join(out, `${stem}.png`), animations: "disabled" });
			if (!flag("no-full")) await page.screenshot({ path: path.join(out, `${stem}-full.png`), fullPage: true, animations: "disabled" });
			const m = await page.evaluate(measure);
			report.push({ section, viewport: vp.name, theme, ...m, errors });
		}
		// Phone interactions: each state is shot and measured like a page.
		if (!vp.desktop && !only) {
			for (const [state, section, act] of STATES) {
				errors = [];
				// A hash change keeps the app (and an open sheet or drawer); each state starts from a fresh load.
				await page.goto("about:blank");
				await page.goto(`${url}#/${section}?range=${range}`);
				await page.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 15000 }).catch(() => {});
				await page.waitForTimeout(400);
				try {
					await act(page);
				} catch (e) {
					// A build without the control (the pre-phone baseline) has no such state.
					report.push({ section: `state:${state}`, viewport: vp.name, theme, skipped: String(e).split("\n")[0] });
					continue;
				}
				await page.waitForTimeout(700);
				await page.screenshot({ path: path.join(out, `state-${state}-${vp.name}-${theme}.png`), animations: "disabled" });
				const m = await page.evaluate(measure);
				report.push({ section: `state:${state}`, viewport: vp.name, theme, ...m, errors });
			}
		}
		await ctx.close();
	}
}
await browser.close();

fs.writeFileSync(path.join(out, "report.json"), JSON.stringify(report, null, 2));
const skipped = report.filter(r => r.skipped);
if (skipped.length) console.log(`states skipped: ${skipped.map(r => `${r.section}@${r.viewport}/${r.theme}`).join(", ")}`);
report.splice(0, report.length, ...report.filter(r => !r.skipped));
const pages = report.length;
const overflowing = report.filter(r => r.overflow);
const smallTotal = report.reduce((n, r) => n + r.small.length, 0);
const targetTotal = report.reduce((n, r) => n + r.targets, 0);
const errored = report.filter(r => r.errors.length);
console.log(`pages rendered: ${pages} (${viewports.map(v => v.name).join(", ")} x ${themes.join(", ")}), range ${range}`);
console.log(`overflow: ${overflowing.length}/${pages}${overflowing.length ? ` -> ${overflowing.map(r => `${r.section}@${r.viewport}/${r.theme} ${r.scrollWidth}>${r.clientWidth}`).join("; ")}` : ""}`);
console.log(`tap targets < 44px: ${smallTotal} of ${targetTotal} visible interactive elements`);
console.log(`console errors: ${errored.length} pages${errored.length ? ` -> ${errored.map(r => `${r.section}: ${r.errors[0]}`).join("; ")}` : ""}`);
console.log(`report: ${path.join(out, "report.json")}`);
