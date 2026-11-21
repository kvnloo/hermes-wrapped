// Bundles the dashboard client into dist/, which the Python server serves and the snapshot inlines.
// Adapted from Oh My Pi's packages/stats/build.ts (MIT).
import * as fs from "node:fs/promises";

await fs.rm("./dist", { recursive: true, force: true });

const result = await Bun.build({
	entrypoints: ["./src/client/index.tsx"],
	outdir: "./dist",
	minify: true,
	naming: "[dir]/[name].[ext]",
});

if (!result.success) {
	for (const message of result.logs) console.error(message);
	process.exit(1);
}

// The phone layout's titles are set in Instrument Serif Italic (OFL, assets/fonts). Bun leaves a CSS url() as a
// file path, and the snapshot inlines only index.css, so the face goes in as a data URL.
const serif = Buffer.from(await Bun.file("../assets/fonts/InstrumentSerif-Italic.ttf").arrayBuffer()).toString("base64");
const face = `@font-face{font-family:"Instrument Serif";font-style:italic;font-display:swap;src:url(data:font/ttf;base64,${serif}) format("truetype")}`;
await Bun.write("./dist/index.css", face + (await Bun.file("./dist/index.css").text()));

// The inline script applies the persisted theme before first paint (no flash). Light by default.
// viewport-fit=cover lets the phone layout pad itself with env(safe-area-inset-*).
const indexHtml = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>hermes stats</title>
    <script>
      (function () {
        try {
          var stored = localStorage.getItem("hermes-stats-theme");
          var system = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
          var theme = stored === "dark" || stored === "light" ? stored : stored === "system" ? system : "light";
          document.documentElement.dataset.theme = theme;
          document.documentElement.style.colorScheme = theme;
        } catch (e) {}
      })();
    </script>
    <link rel="stylesheet" href="index.css">
    <link rel="icon" type="image/x-icon" href="favicon.ico">
</head>
<body>
    <div id="root"></div>
    <script src="index.js" type="module"></script>
</body>
</html>
`;

await fs.copyFile("./src/client/favicon.ico", "./dist/favicon.ico");
await Bun.write("./dist/index.html", indexHtml);
console.log("Built stats/dist");
