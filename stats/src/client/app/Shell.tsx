import { Check, ChevronDown, Menu, MoreHorizontal } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { rangeMeta, TIME_RANGES } from "../data/range";
import { useLiveStatus } from "../data/live";
import type { TimeRange } from "../types";
import { Drawer, Segmented } from "../ui";
import { isDialogOpen, isTypingTarget } from "./keys";
import { LiveChip } from "./LiveChip";
import { type DashboardSection, NAV, navItem, PHONE_TABS } from "./nav";
import { ThemeToggle } from "./ThemeToggle";

export interface ShellProps {
	section: DashboardSection;
	onSectionChange: (section: DashboardSection) => void;
	range: TimeRange;
	onRangeChange: (range: TimeRange) => void;
	children: ReactNode;
}

const RANGE_OPTIONS = TIME_RANGES.map((range, i) => ({
	value: range,
	label: rangeMeta(range).label,
	title: `${rangeMeta(range).windowLabel} (${i + 1})`,
}));

/** The phone's bottom sheets; one at a time. */
type PhoneSheet = "pages" | "range" | null;

/** "the last 7 days" -> "Last 7 days", "all time" -> "All time". */
function rangeName(range: TimeRange): string {
	const words = rangeMeta(range).windowLabel.replace(/^the /, "");
	return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Fixed glass topbar + naked sidebar framing one inset content panel. Owns
 * the global keyboard shortcuts: `1`–`6` pick a range, `g` then a letter jumps
 * to a page. On a phone (styles: phone.css) the sidebar gives way to a tab bar
 * at the bottom, and the range and the full page list open as bottom sheets.
 */
export function Shell({ section, onSectionChange, range, onRangeChange, children }: ShellProps) {
	const [menuOpen, setMenuOpen] = useState(false);
	const [sheet, setSheet] = useState<PhoneSheet>(null);
	const scrolled = useScrolled();
	const { sync } = useLiveStatus();
	useShortcuts(onSectionChange, onRangeChange);

	const go = (next: DashboardSection) => {
		onSectionChange(next);
		setMenuOpen(false);
		setSheet(null);
		window.scrollTo({ top: 0 });
	};

	const progress = sync.phase === "syncing" ? (sync.total > 0 ? sync.current / sync.total : null) : undefined;
	const tab = PHONE_TABS.indexOf(section);
	const group = NAV.find(g => g.items.some(item => item.id === section))?.heading.toLowerCase();

	return (
		<>
			<div className="shell-ambient" />
			<header className="topbar" data-scrolled={scrolled}>
				<button
					type="button"
					className="btn topbar-menu"
					data-variant="ghost"
					data-icon="true"
					onClick={() => setMenuOpen(true)}
					aria-label="Open navigation"
				>
					<Menu size={16} />
				</button>
				<div className="topbar-brand">
					<svg className="topbar-mark" viewBox="0 0 64 64" width="22" height="22" aria-hidden="true">
						<defs>
							<linearGradient id="omp-mark-grad" x1="0" y1="0" x2="1" y2="1">
								<stop offset="0" stopColor="oklch(0.7 0.24 340)" />
								<stop offset=".5" stopColor="oklch(0.62 0.21 295)" />
								<stop offset="1" stopColor="oklch(0.81 0.14 200)" />
							</linearGradient>
						</defs>
						<path fill="url(#omp-mark-grad)" d="M12 10h10v18h20V10h10v44H42V37H22v17H12z" />
					</svg>
					<span>hermes</span>
					<span className="topbar-slash">/</span>
					<span className="topbar-title">stats</span>
				</div>
				<div className="topbar-spacer" />
				<div className="topbar-actions">
					<span className="topbar-hide-narrow">
						<LiveChip />
					</span>
					<Segmented options={RANGE_OPTIONS} value={range} onChange={onRangeChange} aria-label="Time range" />
					<button
						type="button"
						className="btn range-pill"
						aria-haspopup="dialog"
						aria-expanded={sheet === "range"}
						aria-label={`Range: ${rangeName(range)}`}
						onClick={() => setSheet("range")}
					>
						{rangeMeta(range).label}
						<ChevronDown size={14} />
					</button>
					<ThemeToggle />
				</div>
				{progress !== undefined && (
					<div className="topbar-progress">
						<div
							className="topbar-progress-fill"
							data-indeterminate={progress === null}
							style={{ width: `${(progress ?? 0) * 100}%` }}
						/>
					</div>
				)}
			</header>

			{menuOpen && <div className="drawer-scrim" onClick={() => setMenuOpen(false)} role="presentation" />}
			<nav className="sidebar" data-open={menuOpen} aria-label="Pages">
				<NavGroups section={section} onGo={go} />
				<div className="sidebar-foot micro">
					<span>
						<span className="kbd">1</span>–<span className="kbd">{TIME_RANGES.length}</span> range
					</span>
					<span>
						<span className="kbd">G</span> then letter to jump
					</span>
				</div>
			</nav>

			<main className="shell-panel" data-group={group}>
				<div className="shell-content">{children}</div>
			</main>

			<nav className="dock" aria-label="Pages">
				<div className="dock-tabs" style={{ "--k": tab < 0 ? PHONE_TABS.length : tab }}>
					<span className="dock-key" aria-hidden="true" />
					{PHONE_TABS.map(id => {
						const item = navItem(id);
						return (
							<button
								key={id}
								type="button"
								className="dock-tab"
								data-active={id === section}
								aria-current={id === section ? "page" : undefined}
								onClick={() => go(id)}
							>
								<item.icon size={20} />
								<span>{item.short ?? item.label}</span>
							</button>
						);
					})}
					{/* More opens every page; it lights up while you are on a page off the tab bar. */}
					<button
						type="button"
						className="dock-tab"
						data-active={tab < 0}
						aria-haspopup="dialog"
						aria-expanded={sheet === "pages"}
						onClick={() => setSheet("pages")}
					>
						<MoreHorizontal size={20} />
						<span>More</span>
					</button>
				</div>
			</nav>
			<Drawer open={sheet === "pages"} onClose={() => setSheet(null)} title="Pages" subtitle={rangeMeta(range).windowLabel}>
				<nav className="sheet-nav" aria-label="Pages">
					<NavGroups section={section} onGo={go} />
				</nav>
			</Drawer>
			<Drawer open={sheet === "range"} onClose={() => setSheet(null)} title="Range" subtitle="Every page shows this window">
				<div className="sheet-nav" role="radiogroup" aria-label="Time range">
					<div className="nav-group">
						{TIME_RANGES.map(option => (
							<button
								key={option}
								type="button"
								role="radio"
								aria-checked={option === range}
								className="nav-row"
								data-active={option === range}
								onClick={() => {
									onRangeChange(option);
									setSheet(null);
								}}
							>
								<span className="range-key">{rangeMeta(option).label}</span>
								<span className="nav-row-label">{rangeName(option)}</span>
								{option === range && <Check size={18} className="range-check" />}
							</button>
						))}
					</div>
				</div>
			</Drawer>
		</>
	);
}

/** Every page by group: the desktop sidebar, and the phone's page sheet. */
function NavGroups({ section, onGo }: { section: DashboardSection; onGo: (section: DashboardSection) => void }) {
	return NAV.map(group => (
		<div key={group.heading} className="nav-group">
			<div className="nav-heading">{group.heading}</div>
			{group.items.map(item => (
				<button
					key={item.id}
					type="button"
					className="nav-row"
					data-active={item.id === section}
					aria-current={item.id === section ? "page" : undefined}
					onClick={() => onGo(item.id)}
				>
					<item.icon size={15} />
					<span className="nav-row-label">{item.label}</span>
					<kbd>G {item.hotkey.toUpperCase()}</kbd>
				</button>
			))}
		</div>
	));
}

function useScrolled(): boolean {
	const [scrolled, setScrolled] = useState(false);
	useEffect(() => {
		const onScroll = () => setScrolled(window.scrollY > 4);
		onScroll();
		window.addEventListener("scroll", onScroll, { passive: true });
		return () => window.removeEventListener("scroll", onScroll);
	}, []);
	return scrolled;
}

function useShortcuts(onSection: (section: DashboardSection) => void, onRange: (range: TimeRange) => void): void {
	useEffect(() => {
		let pendingG = 0;
		const onKey = (e: KeyboardEvent) => {
			if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
			if (isTypingTarget(e.target) || isDialogOpen()) return;
			const key = e.key.toLowerCase();
			if (pendingG && Date.now() - pendingG < 1200) {
				pendingG = 0;
				const item = NAV.flatMap(group => group.items).find(i => i.hotkey === key);
				if (item) {
					e.preventDefault();
					onSection(item.id);
					window.scrollTo({ top: 0 });
				}
				return;
			}
			if (key === "g") {
				pendingG = Date.now();
				return;
			}
			const index = Number(key) - 1;
			if (Number.isInteger(index) && index >= 0 && index < TIME_RANGES.length) {
				e.preventDefault();
				onRange(TIME_RANGES[index]);
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [onSection, onRange]);
}
