import type {
	Candidate,
	ClashRequest,
	Collection,
	CollectionDetail,
	ContributionExtra,
	ContributionView,
	DeleteJobsResult,
	Drive,
	DriveStats,
	Filesystem,
	FolderState,
	Hardware,
	ImportPrivateDataResult,
	Job,
	JobDetail,
	LogPage,
	MakeMKVFile,
	MediaKind,
	NewRemoteWorker,
	NewRemoteWorkerRequest,
	Preset,
	RemoteWorkers,
	ServerLibrary,
	SettingsTestRequest,
	SettingsTestResult,
	SettingsUpdate,
	SettingsView,
	SetupTools,
	Show,
	Status,
	TitleMapUpdate,
	TvProposeRequest
} from './api-types';

export type MediaServer = 'emby' | 'jellyfin' | 'plex';

type ClashAction = ClashRequest['action'];

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
	const init: RequestInit = { method, headers: {} };
	if (body instanceof FormData) {
		init.body = body;
	} else if (body !== undefined) {
		init.body = JSON.stringify(body);
		(init.headers as Record<string, string>)['Content-Type'] = 'application/json';
	}
	const r = await fetch(`/api${path}`, init);
	if (!r.ok) {
		let msg = `${r.status} ${r.statusText}`;
		try {
			const j = await r.json();
			if (typeof j.detail === 'string') msg = j.detail;
			else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join('; ');
		} catch {
			/* not JSON */
		}
		throw new ApiError(r.status, msg);
	}
	return r.status === 204 ? (undefined as T) : r.json();
}

export const api = {
	status: () => call<Status>('GET', '/status'),
	drives: () => call<Drive[]>('GET', '/drives'),
	jobs: (limit = 200) => call<Job[]>('GET', `/jobs?limit=${limit}`),
	job: (id: number) => call<JobDetail>('GET', `/jobs/${id}`),
	log: (id: number, lines = 30, kind: 'rip' | 'transcode' = 'rip') =>
		call<string[]>('GET', `/jobs/${id}/log?lines=${lines}&kind=${kind}`),
	confirm: (id: number, candidate: Candidate) =>
		call<Job>('POST', `/jobs/${id}/confirm`, { candidate }),
	search: (id: number, query: string, year: number | null) =>
		call<Candidate[]>('POST', `/jobs/${id}/search`, { query, year }),
	retry: (id: number) => call<Job>('POST', `/jobs/${id}/retry`),
	resolveClash: (id: number, action: ClashAction) => call<Job>('POST', `/jobs/${id}/clash`, { action }),
	checkDisc: (id: number, pauseRip = false) =>
		call<Job>('POST', `/jobs/${id}/check-disc${pauseRip ? '?pause_rip=true' : ''}`),
	identifyAgain: (id: number) => call<Job>('POST', `/jobs/${id}/identify-again`),
	cancel: (id: number) => call<Job>('POST', `/jobs/${id}/cancel`),
	setMedia: (id: number, media: MediaKind) => call<Job>('POST', `/jobs/${id}/media`, { media }),
	contribution: (id: number) => call<ContributionView>('GET', `/jobs/${id}/thediscdb`),
	contribute: (id: number) => call<ContributionView>('POST', `/jobs/${id}/thediscdb/send`),
	contributionExtras: (id: number, extras: ContributionExtra[]) =>
		call<ContributionView>('PUT', `/jobs/${id}/thediscdb/extras`, { extras }),
	contributionRelease: (id: number, newRelease: boolean) =>
		call<ContributionView>('POST', `/jobs/${id}/thediscdb/release`, { new_release: newRelease }),
	movieSearch: (q: string, year: number | null) =>
		call<Candidate[]>('GET', `/movies/search?q=${encodeURIComponent(q)}${year ? `&year=${year}` : ''}`),
	setPreset: (id: number, preset: Preset) => call<Job>('PUT', `/jobs/${id}/preset`, preset),
	clearPreset: (id: number) => call<Job>('DELETE', `/jobs/${id}/preset`),
	tvSearch: (q: string) => call<Show[]>('GET', `/tv/search?q=${encodeURIComponent(q)}`),
	tvPropose: (id: number, body: TvProposeRequest) => call<Job>('POST', `/jobs/${id}/tv/propose`, body),
	tvMatchText: (id: number, text: string) => call<Job>('POST', `/jobs/${id}/tv/match-text`, { text }),
	tvConfirm: (id: number, mapping: TitleMapUpdate[]) =>
		call<Job>('POST', `/jobs/${id}/tv/confirm`, { mapping }),
	thumbUrl: (id: number, title: number) => `/api/jobs/${id}/thumbs/${title}.jpg`,
	collections: () => call<Collection[]>('GET', '/collections'),
	collection: (id: number) => call<CollectionDetail>('GET', `/collections/${id}`),
	eject: (driveId: string) => call<void>('POST', `/drives/${driveId}/eject`),
	closeTray: (driveId: string) => call<void>('POST', `/drives/${driveId}/close`),
	redetect: () => call<Drive[]>('POST', '/drives/redetect'),
	renameDrive: (driveId: string, name: string) =>
		call<Drive | null>('PUT', `/drives/${encodeURIComponent(driveId)}/name`, { name }),
	driveStats: () => call<DriveStats[]>('GET', '/drives/stats'),
	deleteJobs: (ids: number[]) => call<DeleteJobsResult>('POST', '/jobs/delete', { ids }),
	logs: (after: number, level: string) =>
		call<LogPage>('GET', `/logs?after=${after}&level=${encodeURIComponent(level)}`),
	rescan: (driveId: string) => call<Job>('POST', `/drives/${driveId}/scan`),
	devInsert: (driveId: string) => call<void>('POST', `/dev/insert/${driveId}`),
	storage: () => call<Filesystem[]>('GET', '/system/storage'),
	setupTools: () => call<SetupTools>('GET', '/setup/tools'),
	folder: (path: string, create = false) => call<FolderState>('POST', '/setup/folder', { path, create }),
	hardware: (refresh = false) => call<Hardware>('GET', `/system/hardware${refresh ? '?refresh=true' : ''}`),
	settings: () => call<SettingsView>('GET', '/settings'),
	saveSettings: (s: SettingsUpdate) => call<SettingsView>('PUT', '/settings', s),
	testSettings: (body: SettingsTestRequest) => call<SettingsTestResult>('POST', '/settings/test', body),
	serverLibraries: (server: MediaServer, url: string, api_key: string | null) =>
		call<ServerLibrary[]>('POST', `/media-servers/${server}/libraries`, { url, api_key }),
	importPrivateData: (form: FormData) =>
		call<ImportPrivateDataResult>('POST', '/setup/makemkv/private-data', form),
	makemkvFiles: () => call<MakeMKVFile[]>('GET', '/setup/makemkv/files'),
	deleteMakemkvFile: (sha256: string, name: string) =>
		call<void>('DELETE', `/setup/makemkv/files/${sha256}?name=${encodeURIComponent(name)}`),
	remoteWorkers: () => call<RemoteWorkers>('GET', '/remote-workers'),
	addRemoteWorker: (body: NewRemoteWorkerRequest) => call<NewRemoteWorker>('POST', '/remote-workers', body),
	revokeRemoteWorker: (id: number) => call<void>('DELETE', `/remote-workers/${id}`)
};
