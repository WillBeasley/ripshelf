// Show a setting on its section's page: open the Advanced fold or media server tab it's in,
// scroll to it, flash it and focus its input.
import { SHOWN_WITH } from './search.ts';

export function reveal(name: string): boolean {
	const el = document.getElementById(`setting-${name}`) ?? document.getElementById(`setting-${SHOWN_WITH[name]}`);
	if (!el) return false;
	for (let p: HTMLElement | null = el; p; p = p.parentElement) {
		if (p instanceof HTMLDetailsElement) p.open = true;
		if (p.hidden && p.dataset.server) document.querySelector<HTMLElement>(`[data-tab="${p.dataset.server}"]`)?.click();
	}
	requestAnimationFrame(() => {
		el.scrollIntoView({ block: 'center', behavior: 'smooth' });
		el.classList.remove('found');
		void el.offsetWidth; // restart the flash
		el.classList.add('found');
		el.querySelector<HTMLElement>('input:not([type=hidden]), select, textarea')?.focus({ preventScroll: true });
	});
	return true;
}
