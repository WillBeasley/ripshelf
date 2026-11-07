// The sections of Settings, in order (config.SECTIONS), with a one-line summary of each
// for the list on a phone.
import type { Component } from 'svelte';
import { live } from '#lib/live.svelte.ts';
import type { Section, SettingsForm } from './form.svelte.ts';
import Identify from './sections/Identify.svelte';
import DrivesRipping from './sections/DrivesRipping.svelte';
import ScannerCovers from './sections/ScannerCovers.svelte';
import Library from './sections/Library.svelte';
import MediaServers from './sections/MediaServers.svelte';
import Transcoding from './sections/Transcoding.svelte';
import Notifications from './sections/Notifications.svelte';
import Workers from './sections/Workers.svelte';
import System from './sections/System.svelte';

export type SectionInfo = {
	id: Section;
	title: string;
	component: Component<{ form: SettingsForm }>;
	summary: (form: SettingsForm) => string;
	// Setup problems (their codes) that point here.
	problems?: string[];
};

const on = (b: boolean, yes: string, no: string) => (b ? yes : no);

export const SECTIONS: SectionInfo[] = [
	{
		id: 'identify',
		title: 'Identify',
		component: Identify,
		summary: (form) => {
			const v = form.view!;
			const keys = [v.tmdb_api_key_set && 'TMDB', v.tvdb_api_key_set && 'TheTVDB', v.omdb_api_key_set && 'OMDb'].filter(Boolean);
			return `${keys.length ? keys.join(', ') : 'No keys set'} · auto-confirm at ${v.auto_confirm_threshold.toFixed(2)}`;
		},
		problems: ['tmdb_key']
	},
	{
		id: 'drives',
		title: 'Drives and ripping',
		component: DrivesRipping as Component<{ form: SettingsForm }>,
		summary: (form) => {
			const v = form.view!;
			const n = live.drives.filter((d) => !v.disabled_drives.includes(d.id)).length;
			return `${n} drive${n === 1 ? '' : 's'} in use · ${on(v.rip_mode === 'backup', 'full disc backups', 'titles as MKV')}`;
		},
		problems: ['makemkv_eula', 'worker_makemkv_eula', 'makemkv_key_unset', 'private_data_missing', 'java_missing']
	},
	{
		id: 'scanner',
		title: 'Scanner and covers',
		component: ScannerCovers,
		summary: (form) => {
			const v = form.view!;
			return `${on(v.scanner_enabled, 'Scanner on', 'No scanner')} · vision model ${on(v.vision_mode !== 'off', 'on', 'off')}`;
		}
	},
	{
		id: 'library',
		title: 'Library and filing',
		component: Library,
		summary: (form) => form.view!.library_dir,
		problems: ['library_missing']
	},
	{
		id: 'media-servers',
		title: 'Media servers',
		component: MediaServers,
		summary: (form) => {
			const v = form.view!;
			const set = [v.emby_url && 'Emby', v.jellyfin_url && 'Jellyfin', v.plex_url && 'Plex'].filter(Boolean);
			return set.length ? set.join(', ') : 'None set up';
		}
	},
	{
		id: 'transcoding',
		title: 'Transcoding',
		component: Transcoding,
		summary: (form) => {
			const v = form.view!;
			return v.transcode_enabled ? `On · ${v.transcode_encoder === 'auto' ? 'automatic encoder' : v.transcode_encoder}` : 'Off';
		}
	},
	{
		id: 'notifications',
		title: 'Notifications',
		component: Notifications,
		summary: (form) => on(form.isSet('pushover_user_key') && form.isSet('pushover_app_token'), 'Pushover on', 'Pushover off')
	},
	{
		id: 'workers',
		title: 'Workers',
		component: Workers as Component<{ form: SettingsForm }>,
		summary: () => {
			const workers = new Set(live.drives.map((d) => d.worker).filter(Boolean));
			return workers.size ? `${workers.size} connected` : 'Other machines that rip for this one';
		}
	},
	{
		id: 'system',
		title: 'System',
		component: System,
		summary: (form) => `${form.view!.backup_keep} daily backups · ${form.view!.public_url || 'no address set'}`,
		problems: ['public_url', 'backup_dir']
	}
];

export const sectionById = (id: string) => SECTIONS.find((s) => s.id === id);
