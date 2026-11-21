import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { isPhone } from "../usePhone";

export interface DrawerProps {
	open: boolean;
	onClose: () => void;
	title: ReactNode;
	subtitle?: ReactNode;
	/** Header controls left of the close button. */
	actions?: ReactNode;
	/** Sheet width in px. Default 560. */
	width?: number;
	children: ReactNode;
}

/**
 * Right-side glass sheet over a scrim, portalled to `document.body`. Esc and
 * scrim clicks close it; focus returns to the opener.
 */
export function Drawer({ open, onClose, title, subtitle, actions, width, children }: DrawerProps) {
	const onCloseRef = useRef(onClose);
	onCloseRef.current = onClose;
	const panelRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (!open) return;
		const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		panelRef.current?.focus();
		const onKey = (e: KeyboardEvent) => {
			if (e.key === "Escape") {
				e.preventDefault();
				onCloseRef.current();
			}
		};
		window.addEventListener("keydown", onKey);
		return () => {
			window.removeEventListener("keydown", onKey);
			opener?.focus();
		};
	}, [open]);

	// On a phone the drawer is a bottom sheet (phone.css): pull its handle or header down to dismiss it.
	const drag = (e: React.PointerEvent<HTMLElement>) => {
		const panel = panelRef.current;
		if (!panel || !isPhone() || (e.target as Element).closest("button, a, input")) return;
		const handle = e.currentTarget;
		const y0 = e.clientY;
		const t0 = performance.now();
		handle.setPointerCapture(e.pointerId);
		// The entrance animation's fill would otherwise win over the inline transform.
		panel.style.animation = "none";
		panel.style.transition = "none";
		const move = (ev: PointerEvent) => {
			panel.style.transform = `translateY(${Math.max(0, ev.clientY - y0)}px)`;
		};
		const up = (ev: PointerEvent) => {
			handle.removeEventListener("pointermove", move);
			handle.removeEventListener("pointerup", up);
			handle.removeEventListener("pointercancel", up);
			const dy = ev.clientY - y0;
			const flung = dy > 24 && dy / Math.max(1, performance.now() - t0) > 0.5;
			if (dy > 96 || flung) {
				panel.style.transition = "transform 220ms cubic-bezier(0.4, 0, 1, 1)";
				panel.style.transform = "translateY(100%)";
				setTimeout(() => onCloseRef.current(), 200);
				return;
			}
			panel.style.transition = "transform 320ms cubic-bezier(0.34, 1.56, 0.64, 1)";
			panel.style.transform = "";
		};
		handle.addEventListener("pointermove", move);
		handle.addEventListener("pointerup", up);
		handle.addEventListener("pointercancel", up);
	};

	if (!open) return null;
	return createPortal(
		<>
			<div className="drawer-scrim" onClick={onClose} role="presentation" />
			<div
				ref={panelRef}
				className="drawer"
				role="dialog"
				aria-modal="true"
				tabIndex={-1}
				style={width !== undefined ? { "--drawer-w": `${width}px` } : undefined}
			>
				<div className="drawer-grab" onPointerDown={drag} aria-hidden="true">
					<i />
				</div>
				<header className="drawer-header" onPointerDown={drag}>
					<div className="drawer-titles">
						<h2 className="drawer-title">{title}</h2>
						{subtitle !== undefined && <div className="drawer-subtitle">{subtitle}</div>}
					</div>
					{actions}
					<button
						type="button"
						className="btn"
						data-variant="ghost"
						data-icon="true"
						onClick={onClose}
						aria-label="Close"
					>
						<X size={16} />
					</button>
				</header>
				<div className="drawer-body">{children}</div>
			</div>
		</>,
		document.body,
	);
}

/** Label/value grid for detail panels. */
export function KeyValues({ items }: { items: readonly { key: string; label: ReactNode; value: ReactNode }[] }) {
	return (
		<dl className="kv" style={{ margin: 0 }}>
			{items.map(item => (
				<div key={item.key} className="kv-item">
					<dt className="kv-key">{item.label}</dt>
					<dd className="kv-value" style={{ margin: 0 }}>
						{item.value}
					</dd>
				</div>
			))}
		</dl>
	);
}
