// Settings search: every field's label, help and keywords (config.Settings), and a few
// things on the page that aren't settings, matched word by word.
import type { Section, SettingsForm } from './form.svelte.ts';
import { sectionById } from './sections.ts';

export type Hit = { name: string; title: string; section: Section; sectionTitle: string; help: string };

// Shown on a page but not a saved setting (or not one field).
const EXTRA: { name: string; title: string; section: Section; help: string; keywords: string }[] = [
	{ name: 'alert_sound', title: 'Alert sound', section: 'notifications', help: 'This device only.', keywords: 'chime browser beep' },
	{ name: 'private_data', title: 'MakeMKV private data', section: 'drives', help: 'Import _private_data.tar or a firmware dump.', keywords: 'libredrive volume key dump firmware import uhd' },
	{ name: 'remote_workers', title: 'Remote workers', section: 'workers', help: 'Other machines whose drives rip for this one.', keywords: 'worker token compose add revoke' },
	{ name: 'hardware', title: 'Hardware on this machine', section: 'transcoding', help: 'GPUs and the encoders that work.', keywords: 'gpu vaapi qsv nvenc encoder' },
	{ name: 'about', title: 'About ripshelf', section: 'system', help: 'Version and credits.', keywords: 'version licence credits' }
];

// Where a setting shows only when another is on: search shows that one instead.
export const SHOWN_WITH: Record<string, string> = {
	thediscdb_contribute_url: 'thediscdb_contribute',
	thediscdb_api_key: 'thediscdb_contribute',
	vision_provider: 'vision_mode',
	vision_url: 'vision_mode',
	vision_model: 'vision_mode',
	vision_api_key: 'vision_mode',
	label_ai_model: 'label_ai'
};

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');

export function search(form: SettingsForm, query: string, limit = 8): Hit[] {
	const words = norm(query).split(/\s+/).filter(Boolean);
	if (!words.length) return [];
	const rows = [
		...Object.values(form.fields)
			.filter((f) => f.section && f.title)
			.map((f) => ({ name: f.name, title: f.title, section: f.section as Section, help: f.help, keywords: f.keywords })),
		...EXTRA
	];
	const scored: [number, Hit][] = [];
	for (const r of rows) {
		const sectionTitle = sectionById(r.section)?.title ?? r.section;
		const title = norm(r.title);
		const hay = `${title} ${norm(r.help)} ${norm(r.keywords)} ${norm(sectionTitle)} ${r.name.replace(/_/g, ' ')}`;
		if (!words.every((w) => hay.includes(w))) continue;
		// Matches in the label first, then those starting a word of it.
		const score = words.reduce((n, w) => n + (title.includes(w) ? 2 : 0) + (title.startsWith(w) || title.includes(` ${w}`) ? 1 : 0), 0);
		scored.push([score, { name: r.name, title: r.title, section: r.section, sectionTitle, help: r.help }]);
	}
	return scored.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, h]) => h);
}
