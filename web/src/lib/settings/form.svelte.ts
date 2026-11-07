// The Settings form: the saved settings (view), the values as typed (f) and as saved (base),
// and the secrets as typed. Shared by every section of Settings, and by the setup page.
import type { ProviderInfo, SettingField, SettingsUpdate, SettingsView, SettingsViewBase } from '#lib/api-types.ts';
import { api } from '#lib/api.ts';
import { Action } from '#lib/actions.svelte.ts';
import { live } from '#lib/live.svelte.ts';

// Folders are in the view twice (as set, and where they end up); the form has them as set.
export const FOLDERS = ['library_dir', 'movies_dir', 'tv_dir', 'disc_backups_dir', 'scratch_dir'] as const;
type Folder = (typeof FOLDERS)[number];
type SecretSet = Extract<keyof SettingsView, `${string}_set`>;
type Saved = Omit<SettingsView, SecretSet | keyof SettingsViewBase>;
// Text that may be unset is '' in the form (sent as '', which the server saves as unset).
export type Values = { [K in keyof Saved]: Exclude<Saved[K], null> } & Record<Folder, string>;
export type SecretName = SecretSet extends `${infer N}_set` ? N : never;
export type Section = NonNullable<SettingField['section']>;

// The fields' labels, limits and defaults (config.Settings), fetched once.
let fieldList: Promise<SettingField[]> | null = null;
export function settingFields(): Promise<SettingField[]> {
	fieldList ??= api.settingFields().catch((e) => {
		fieldList = null;
		throw e;
	});
	return fieldList;
}

const clone = <T>(v: T): T => (v === null || typeof v !== 'object' ? v : JSON.parse(JSON.stringify(v)));
const same = (a: unknown, b: unknown) =>
	typeof a === 'string' && typeof b === 'string' ? a.trim() === b.trim() : JSON.stringify(a) === JSON.stringify(b);

// The view's settings as the form keeps them.
function values(v: SettingsView, fields: Record<string, SettingField>): Values {
	const out: Record<string, unknown> = {};
	for (const [k, val] of Object.entries(v)) {
		if (k.endsWith('_set') || !(k in fields) || FOLDERS.includes(k as Folder)) continue;
		out[k] = val === null ? '' : clone(val);
	}
	out.library_dir = v.library_dir;
	out.scratch_dir = v.scratch_dir;
	out.movies_dir = v.custom_movies_dir;
	out.tv_dir = v.custom_tv_dir;
	out.disc_backups_dir = v.custom_disc_backups_dir;
	return out as Values;
}

export class SettingsForm {
	view = $state<SettingsView | null>(null);
	fields = $state<Record<string, SettingField>>({});
	f = $state({} as Values); // as typed
	base = $state({} as Values); // as saved
	// Secrets as typed (blank keeps the saved one), and those to clear on Save.
	secrets = $state<Partial<Record<SecretName, string>>>({});
	cleared = $state<SecretName[]>([]);
	act = new Action();
	saved = $state(false);

	ready = $derived(this.view !== null && Object.keys(this.fields).length > 0);

	async load() {
		const [v, fields] = await Promise.all([
			this.act.run(() => api.settings()),
			this.act.run(() => settingFields())
		]);
		if (fields) this.fields = Object.fromEntries(fields.map((x) => [x.name, x]));
		if (v) this.fill(v);
	}

	// Everything as saved, dropping what was typed.
	fill(v: SettingsView) {
		this.view = v;
		this.base = values(v, this.fields);
		this.f = values(v, this.fields);
		this.secrets = {};
		this.cleared = [];
		live.threshold = v.auto_confirm_threshold;
	}

	// The settings of a section (or every one shown somewhere).
	names(section?: Section | Section[]): string[] {
		const want = section === undefined ? null : new Set([section].flat());
		return Object.values(this.fields)
			.filter((x) => x.section && (!want || want.has(x.section)))
			.map((x) => x.name);
	}

	isSet(name: SecretName): boolean {
		return Boolean(this.view?.[`${name}_set` as SecretSet]) && !this.cleared.includes(name);
	}

	dirty(name: string): boolean {
		if (this.fields[name]?.secret)
			return Boolean(this.secrets[name as SecretName]?.trim()) || this.cleared.includes(name as SecretName);
		const f = this.f as Record<string, unknown>;
		const base = this.base as Record<string, unknown>;
		return name in f && !same(f[name], base[name]);
	}

	changed(names: string[] = this.names()): string[] {
		return names.filter((n) => this.dirty(n));
	}

	// What Save sends: only the settings changed, so saving one section leaves the others
	// (and folders that come from the environment, a container's mounts) as they are.
	changes(names: string[] = this.names()): SettingsUpdate {
		const b: Record<string, unknown> = {};
		const f = this.f as Record<string, unknown>;
		for (const n of this.changed(names)) {
			if (this.fields[n].secret) b[n] = this.cleared.includes(n as SecretName) ? '' : this.secrets[n as SecretName]!.trim();
			else b[n] = typeof f[n] === 'string' ? (f[n] as string).trim() : f[n];
		}
		return b as SettingsUpdate;
	}

	// The form as typed, for the Test buttons: secrets only when typed (blank means the saved one).
	body = (): SettingsUpdate => {
		const b: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(this.f)) {
			if (!FOLDERS.includes(k as Folder) || this.dirty(k)) b[k] = typeof v === 'string' ? v.trim() : v;
		}
		for (const [k, v] of Object.entries(this.secrets)) if (v?.trim()) b[k] = v.trim();
		return b as SettingsUpdate;
	};

	// Save these settings (if changed), keeping what's typed in the others.
	async save(names: string[] = this.names()): Promise<boolean> {
		this.saved = false;
		const body = this.changes(names);
		if (!Object.keys(body).length) return true;
		const v = await this.act.run(() => api.saveSettings(body));
		if (!v) return false;
		this.view = v;
		const fresh = values(v, this.fields);
		this.base = fresh;
		const f = this.f as Record<string, unknown>;
		for (const k of Object.keys(body)) {
			if (k in fresh) f[k] = clone((fresh as Record<string, unknown>)[k]);
			delete this.secrets[k as SecretName];
		}
		this.cleared = this.cleared.filter((n) => !(n in body));
		live.threshold = v.auto_confirm_threshold;
		this.saved = true;
		return true;
	}

	discard(names: string[] = this.names()) {
		const f = this.f as Record<string, unknown>;
		const base = this.base as Record<string, unknown>;
		for (const n of names) {
			if (n in base) f[n] = clone(base[n]);
			delete this.secrets[n as SecretName];
		}
		this.cleared = this.cleared.filter((n) => !names.includes(n));
		this.saved = false;
	}

	// Cleared on Save, like any other change.
	clearSecret(name: SecretName) {
		delete this.secrets[name];
		if (!this.cleared.includes(name)) this.cleared = [...this.cleared, name];
	}

	keepSecret(name: SecretName) {
		this.cleared = this.cleared.filter((n) => n !== name);
	}

	// The saved settings again, after something saved by its own button (an import, a
	// drive's name), keeping what's typed.
	async refresh() {
		const v = await api.settings().catch(() => null);
		if (v) this.view = v;
	}

	// Metadata providers, their descriptions, and whether their keys are set (or typed).
	providerInfo = $derived(
		Object.fromEntries((this.view?.providers ?? []).map((p) => [p.name, p])) as Record<string, ProviderInfo>
	);
	keyed = (name: string) => {
		const p = this.providerInfo[name];
		if (!p || p.configured) return true;
		return p.keys.every((k) => this.secrets[k as SecretName]?.trim());
	};
}
