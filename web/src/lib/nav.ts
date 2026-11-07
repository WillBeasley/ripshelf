import { goto } from '$app/navigation';
import { page } from '$app/state';

// The job detail panel is a ?job= query on whichever page is open, so
// notification links like /finished?job=12 land on the right page with it open.
export function openJob(id: number, section?: 'scans') {
	const url = new URL(page.url.href);
	url.searchParams.set('job', String(id));
	if (section) url.searchParams.set('show', section);
	else url.searchParams.delete('show');
	goto(url, { reset: false });
}

export function closeJob() {
	const url = new URL(page.url.href);
	url.searchParams.delete('job');
	url.searchParams.delete('show');
	goto(url, { reset: false, replace: true });
}
