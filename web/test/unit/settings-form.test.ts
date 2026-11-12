import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SettingField, SettingsView } from '#lib/api-types.ts';

const server = vi.hoisted(() => ({ view: {} as Record<string, unknown>, saves: [] as Record<string, unknown>[] }));

// The server: GET returns the saved settings, PUT merges a change into them.
vi.mock('#lib/api.ts', () => ({
	api: {
		settings: async () => structuredClone(server.view),
		settingFields: async () => FIELDS,
		saveSettings: async (body: Record<string, unknown>) => {
			server.saves.push(body);
			for (const [k, v] of Object.entries(body)) {
				if (k.endsWith('_api_key')) server.view[`${k}_set`] = v !== '';
				else server.view[k] = v === '' ? null : v;
			}
			return structuredClone(server.view);
		}
	}
}));

const { SettingsForm } = await import('#lib/settings/form.svelte.ts');
const { search } = await import('#lib/settings/search.ts');
const { live } = await import('#lib/live.svelte.ts');

function field(name: string, section: SettingField['section'], title: string, more: Partial<SettingField> = {}) {
	return { name, section, title, help: '', keywords: '', secret: false, kind: 'text', ...more } as SettingField;
}

const FIELDS = [
	field('auto_confirm_threshold', 'identify', 'Auto-confirm threshold', { kind: 'range', help: 'How sure a match must be.' }),
	field('tmdb_api_key', 'identify', 'TMDB API key', { kind: 'secret', secret: true, keywords: 'themoviedb' }),
	field('auto_eject', 'drives', 'Eject when done', { kind: 'bool' }),
	field('ocr_languages', 'scanner', 'Text languages', { keywords: 'tesseract ocr' }),
	field('searxng_url', 'identify', 'SearXNG address', { nullable: true }),
	field('library_dir', 'library', 'Library folder', { kind: 'path' }),
	field('movies_dir', 'library', 'Films folder', { kind: 'path' }),
	field('setup_complete', null, 'Not on the page', { kind: 'bool' })
];

function savedView(): Record<string, unknown> {
	return {
		auto_confirm_threshold: 0.9,
		tmdb_api_key_set: true,
		auto_eject: true,
		ocr_languages: 'eng',
		searxng_url: null,
		setup_complete: true,
		providers: [],
		library_dir: '/srv/library',
		scratch_dir: '/srv/scratch',
		movies_dir: '/srv/library/Movies',
		custom_movies_dir: '',
		tv_dir: '/srv/library/TV',
		custom_tv_dir: '',
		disc_backups_dir: '/srv/library/Disc backups',
		custom_disc_backups_dir: ''
	};
}

async function loaded() {
	const form = new SettingsForm();
	await form.load();
	return form;
}

beforeEach(() => {
	server.view = savedView();
	server.saves = [];
});

describe('SettingsForm', () => {
	it('loads the saved settings, with unset text as blank and folders as set', async () => {
		const form = await loaded();
		expect(form.ready).toBe(true);
		expect(form.f.searxng_url).toBe('');
		expect(form.f.movies_dir).toBe(''); // <library>/Movies, so not set
		expect(form.f.library_dir).toBe('/srv/library');
		expect(form.changed()).toEqual([]);
		expect(live.threshold).toBe(0.9);
	});

	it('lists the fields of a section, leaving out those not on the page', async () => {
		const form = await loaded();
		expect(form.names('library')).toEqual(['library_dir', 'movies_dir']);
		expect(form.names(['identify', 'drives'])).toEqual(['auto_confirm_threshold', 'tmdb_api_key', 'auto_eject', 'searxng_url']);
		expect(form.names()).not.toContain('setup_complete');
	});

	it('sends only what changed, trimmed', async () => {
		const form = await loaded();
		form.f.ocr_languages = '  eng+fra ';
		form.f.auto_eject = false;
		form.f.searxng_url = '   '; // only spaces, so not a change
		expect(form.changed()).toEqual(['auto_eject', 'ocr_languages']);
		expect(form.changes()).toEqual({ auto_eject: false, ocr_languages: 'eng+fra' });
	});

	it('saves one section and keeps what is typed in the others', async () => {
		const form = await loaded();
		form.f.ocr_languages = 'fra';
		form.f.auto_confirm_threshold = 0.75;
		expect(await form.save(form.names('scanner'))).toBe(true);
		expect(server.saves).toEqual([{ ocr_languages: 'fra' }]);
		expect(form.dirty('ocr_languages')).toBe(false);
		expect(form.dirty('auto_confirm_threshold')).toBe(true);
		expect(form.f.auto_confirm_threshold).toBe(0.75);
		expect(form.saved).toBe(true);
	});

	it('does not call the server when nothing changed', async () => {
		const form = await loaded();
		expect(await form.save()).toBe(true);
		expect(server.saves).toEqual([]);
	});

	it('discards what was typed', async () => {
		const form = await loaded();
		form.f.ocr_languages = 'fra';
		form.secrets.tmdb_api_key = 'typed';
		form.discard();
		expect(form.f.ocr_languages).toBe('eng');
		expect(form.secrets).toEqual({});
		expect(form.changed()).toEqual([]);
	});

	it('keeps a secret unless one is typed or it is cleared', async () => {
		const form = await loaded();
		expect(form.isSet('tmdb_api_key')).toBe(true);
		form.secrets.tmdb_api_key = '   ';
		expect(form.dirty('tmdb_api_key')).toBe(false);

		form.secrets.tmdb_api_key = ' new-key ';
		expect(form.changes()).toEqual({ tmdb_api_key: 'new-key' });

		form.clearSecret('tmdb_api_key');
		expect(form.isSet('tmdb_api_key')).toBe(false);
		expect(form.changes()).toEqual({ tmdb_api_key: '' });
		form.keepSecret('tmdb_api_key');
		expect(form.changed()).toEqual([]);

		form.clearSecret('tmdb_api_key');
		await form.save();
		expect(server.view.tmdb_api_key_set).toBe(false);
		expect(form.isSet('tmdb_api_key')).toBe(false);
		expect(form.cleared).toEqual([]);

		form.secrets.tmdb_api_key = 'another-key';
		await form.save();
		expect(server.saves.at(-1)).toEqual({ tmdb_api_key: 'another-key' });
		expect(form.isSet('tmdb_api_key')).toBe(true);
		expect(form.secrets).toEqual({}); // saved, so the box is blank again
	});

	it('tests with the form as typed, folders only when changed', async () => {
		const form = await loaded();
		form.f.ocr_languages = ' fra ';
		form.secrets.tmdb_api_key = 'typed';
		const body = form.body() as Record<string, unknown>;
		expect(body.ocr_languages).toBe('fra');
		expect(body.tmdb_api_key).toBe('typed');
		expect(body).not.toHaveProperty('library_dir');
		form.f.library_dir = '/mnt/new';
		expect((form.body() as Record<string, unknown>).library_dir).toBe('/mnt/new');
	});

	it('updates the auto-confirm threshold everywhere on save', async () => {
		const form = await loaded();
		form.f.auto_confirm_threshold = 0.8;
		await form.save();
		expect(live.threshold).toBe(0.8);
	});
});

describe('settings search', () => {
	it('matches every word, best in the label first', async () => {
		const form = await loaded();
		expect(search(form, 'folder').map((h) => h.name)).toEqual(['library_dir', 'movies_dir']);
		expect(search(form, 'films folder').map((h) => h.name)).toEqual(['movies_dir']);
		expect(search(form, 'themoviedb')[0]).toMatchObject({ name: 'tmdb_api_key', section: 'identify', sectionTitle: 'Identify' });
	});

	it('finds things on the page that are not settings, and ignores case and accents', async () => {
		const form = await loaded();
		expect(search(form, 'GPU').map((h) => h.name)).toContain('hardware');
		expect(search(form, 'TÉSSERACT').map((h) => h.name)).toEqual(['ocr_languages']);
	});

	it('finds nothing for a blank query, and stops at the limit', async () => {
		const form = await loaded();
		expect(search(form, '   ')).toEqual([]);
		expect(search(form, 'e', 3)).toHaveLength(3);
	});
});

// Keep the mock's view in step with the real type, so a renamed field shows up in svelte-check.
savedView() satisfies Partial<Record<keyof SettingsView, unknown>>;
