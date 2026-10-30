import { goto } from '$app/navigation';
import { page } from '$app/state';

// The job detail panel is a ?job= query on whichever page is open, so
// notification links like /review?job=12 land on the right page with it open.
export function openJob(id: number) {
	const url = new URL(page.url);
	url.searchParams.set('job', String(id));
	goto(url, { noScroll: true, keepFocus: true });
}

export function closeJob() {
	const url = new URL(page.url);
	url.searchParams.delete('job');
	goto(url, { noScroll: true, keepFocus: true, replaceState: true });
}
