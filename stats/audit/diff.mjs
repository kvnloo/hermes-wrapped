// Pixel-compare two audit runs: every PNG present in both dirs, decoded in Chromium's canvas (no image deps).
//   node stats/audit/diff.mjs <before-dir> <after-dir> [--glob 1440]
import fs from "node:fs";
import path from "node:path";
import { chromium, executablePath } from "./browser.mjs";

const [a, b] = process.argv.slice(2);
const filter = process.argv.includes("--glob") ? process.argv[process.argv.indexOf("--glob") + 1] : "";
const browser = await chromium.launch({ executablePath });
const page = await browser.newPage();

/** Runs in the page: decode both PNGs and count pixels whose RGB differs. */
async function compare([x, y]) {
	const load = async src => {
		const img = new Image();
		img.src = src;
		await img.decode();
		const canvas = new OffscreenCanvas(img.naturalWidth, img.naturalHeight);
		const ctx = canvas.getContext("2d");
		ctx.drawImage(img, 0, 0);
		return ctx.getImageData(0, 0, canvas.width, canvas.height);
	};
	const [p, q] = await Promise.all([load(x), load(y)]);
	if (p.width !== q.width || p.height !== q.height) return { size: `${p.width}x${p.height} -> ${q.width}x${q.height}` };
	let diff = 0;
	for (let i = 0; i < p.data.length; i += 4) {
		if (p.data[i] !== q.data[i] || p.data[i + 1] !== q.data[i + 1] || p.data[i + 2] !== q.data[i + 2]) diff++;
	}
	return { diff, total: p.width * p.height };
}

const uri = file => `data:image/png;base64,${fs.readFileSync(file).toString("base64")}`;
let same = 0;
const changed = [];
for (const name of fs.readdirSync(a).filter(n => n.endsWith(".png") && n.includes(filter)).sort()) {
	if (!fs.existsSync(path.join(b, name))) continue;
	const r = await page.evaluate(compare, [uri(path.join(a, name)), uri(path.join(b, name))]);
	if (r.size) changed.push(`${name}: size ${r.size}`);
	else if (r.diff) changed.push(`${name}: ${r.diff} px (${((100 * r.diff) / r.total).toFixed(3)}%)`);
	else same++;
}
await browser.close();
console.log(`identical: ${same}, changed: ${changed.length}`);
for (const line of changed) console.log(`  ${line}`);
