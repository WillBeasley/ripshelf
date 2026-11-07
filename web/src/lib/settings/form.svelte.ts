// The Settings form: the saved settings (view), the values as typed (f), and the secrets
// as typed. Shared by every section of Settings, and by the setup page.
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

export class SettingsForm {
	view = $state<SettingsView | null>(null);
	fields = $state<Record<string, SettingField>>({});
	// Filled from the view before anything shows it (see ready).
	f = $state({} as Values);
	// Secrets as typed: blank keeps the saved one.
	secrets = $state<Partial<Record<SecretName, string>>>({});
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

	fill(v: SettingsView) {
		this.view = v;
		const out: Record<string, unknown> = {};
		for (const [k, val] of Object.entries(v)) {
			if (k.endsWith('_set') || !(k in this.fields) || FOLDERS.includes(k as Folder)) continue;
			out[k] = val === null ? '' : clone(val);
		}
		out.library_dir = v.library_dir;
		out.scratch_dir = v.scratch_dir;
		out.movies_dir = v.custom_movies_dir;
		out.tv_dir = v.custom_tv_dir;
		out.disc_backups_dir = v.custom_disc_backups_dir;
		this.f = out as Values;
		this.secrets = {};
		live.threshold = v.auto_confirm_threshold;
	}

	// The folders as saved, to send only the ones changed: saving other settings must
	// not pin the folders, which may come from the environment (a container's mounts).
	savedFolder(k: Folder): string {
		const v = this.view;
		if (!v) return '';
		return { library_dir: v.library_dir, scratch_dir: v.scratch_dir, movies_dir: v.custom_movies_dir,
			tv_dir: v.custom_tv_dir, disc_backups_dir: v.custom_disc_backups_dir }[k];
	}

	isSet(name: SecretName): boolean {
		return Boolean(this.view?.[`${name}_set` as SecretSet]);
	}

	// The form as typed, secrets included only when typed (blank means the saved one).
	body = (): SettingsUpdate => {
		const b: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(this.f)) {
			if (FOLDERS.includes(k as Folder)) {
				if (String(v).trim() !== this.savedFolder(k as Folder)) b[k] = String(v).trim();
			} else b[k] = v;
		}
		for (const [k, v] of Object.entries(this.secrets)) if (v?.trim()) b[k] = v.trim();
		return b as SettingsUpdate;
	};

	async save() {
		this.saved = false;
		const v = await this.act.run(() => api.saveSettings(this.body()));
		if (v) {
			this.fill(v);
			this.saved = true;
		}
	}

	async clearSecret(name: SecretName) {
		const v = await this.act.run(() => api.saveSettings({ [name]: '' }));
		if (v) this.fill(v);
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
