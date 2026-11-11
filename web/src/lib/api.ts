import type {
	Candidate,
	ClashRequest,
	Collection,
	CollectionDetail,
	ContributionExtra,
	ContributionView,
	CoverScan,
	Listing,
	ListingView,
	PendingScan,
	PendingScans,
	ScanMatch,
	ScanText,
	DeleteJobsResult,
	Drive,
	DriveStats,
	Filesystem,
	FinishedPage,
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
	ReleaseDetails,
	ReleaseLookup,
	ScannerInfo,
	ScannerRun,
	ScannerScanRequest,
	ScanSplit,
	ScanUpdate,
	RemoteWorkers,
	ServerLibrary,
	SettingField,
	SettingsTestRequest,
	SettingsTestResult,
	SettingsUpdate,
	SettingsView,
	SetupTools,
	Show,
	Snapshots,
	Status,
	TitleMapUpdate,
	TvProposeRequest,
	TvSplitRequest,
	WebResult
} from './api-types';

export type MediaServer = 'emby' | 'jellyfin' | 'plex';
export type CoverSide = 'front' | 'back';
export type ScanKind = CoverScan['kind'];

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
	// The history older than job `before` (all of it without one). A limit of 0 only counts it.
	finished: (before: number | null, limit = 100) =>
		call<FinishedPage>('GET', `/jobs/finished?limit=${limit}${before != null ? `&before=${before}` : ''}`),
	job: (id: number) => call<JobDetail>('GET', `/jobs/${id}`),
	log: (id: number, lines = 30, kind: 'rip' | 'transcode' = 'rip') =>
		call<string[]>('GET', `/jobs/${id}/log?lines=${lines}&kind=${kind}`),
	confirm: (id: number, candidate: Candidate) =>
		call<Job>('POST', `/jobs/${id}/confirm`, { candidate }),
	search: (id: number, query: string, year: number | null) =>
		call<Candidate[]>('POST', `/jobs/${id}/search`, { query, year }),
	webSearch: (query: string) => call<WebResult[]>('POST', '/web-search', { query }),
	snapshots: (id: number) => call<Snapshots>('GET', `/jobs/${id}/snapshots`),
	snapshotUrl: (id: number, title: number, frame: number) => `/api/jobs/${id}/snapshots/${title}/${frame}.jpg`,
	retry: (id: number) => call<Job>('POST', `/jobs/${id}/retry`),
	resolveClash: (id: number, action: ClashAction) => call<Job>('POST', `/jobs/${id}/clash`, { action }),
	checkDisc: (id: number, pauseRip = false) =>
		call<Job>('POST', `/jobs/${id}/check-disc${pauseRip ? '?pause_rip=true' : ''}`),
	identifyAgain: (id: number) => call<Job>('POST', `/jobs/${id}/identify-again`),
	reidentify: (id: number) => call<Job>('POST', `/jobs/${id}/reidentify`),
	keepIdentity: (id: number) => call<Job>('POST', `/jobs/${id}/reidentify/keep`),
	cancel: (id: number) => call<Job>('POST', `/jobs/${id}/cancel`),
	setMedia: (id: number, media: MediaKind) => call<Job>('POST', `/jobs/${id}/media`, { media }),
	contribution: (id: number) => call<ContributionView>('GET', `/jobs/${id}/thediscdb`),
	contribute: (id: number) => call<ContributionView>('POST', `/jobs/${id}/thediscdb/send`),
	contributionExtras: (id: number, extras: ContributionExtra[]) =>
		call<ContributionView>('PUT', `/jobs/${id}/thediscdb/extras`, { extras }),
	contributionRelease: (id: number, newRelease: boolean) =>
		call<ContributionView>('POST', `/jobs/${id}/thediscdb/release`, { new_release: newRelease }),
	contributionReread: (id: number) => call<ContributionView>('POST', `/jobs/${id}/thediscdb/reread`),
	contributionDetails: (id: number, details: ReleaseDetails) =>
		call<ContributionView>('PUT', `/jobs/${id}/thediscdb/details`, details),
	releaseLookup: (id: number, upc: string | null) =>
		call<ReleaseLookup>('POST', `/jobs/${id}/thediscdb/lookup`, { upc }),
	coverUrl: (id: number, side: CoverSide, v: string | number) => `/api/jobs/${id}/thediscdb/cover/${side}?v=${v}`,
	uploadCover: (id: number, side: CoverSide, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return call<ContributionView>('PUT', `/jobs/${id}/thediscdb/cover/${side}`, form);
	},
	coverFromUrl: (id: number, side: CoverSide, url: string) =>
		call<ContributionView>('POST', `/jobs/${id}/thediscdb/cover/${side}/from-url`, { url }),
	deleteCover: (id: number, side: CoverSide) =>
		call<ContributionView>('DELETE', `/jobs/${id}/thediscdb/cover/${side}`),
	scanner: () => call<ScannerInfo>('GET', '/scanner'),
	listing: (id: number) => call<ListingView>('GET', `/jobs/${id}/listing`),
	pendingScans: (driveId: string) => call<PendingScans>('GET', `/drives/${encodeURIComponent(driveId)}/pending-scans`),
	addPendingScan: (driveId: string, file: File, kind: ScanKind, source: 'upload' | 'camera') => {
		const form = new FormData();
		form.append('file', file);
		form.append('kind', kind);
		form.append('source', source);
		return call<PendingScan>('POST', `/drives/${encodeURIComponent(driveId)}/pending-scans`, form);
	},
	scanPending: (driveId: string, body: ScannerScanRequest) =>
		call<ScannerRun>('POST', `/drives/${encodeURIComponent(driveId)}/pending-scans/scanner`, body),
	clearPendingScanner: (driveId: string) =>
		call<void>('DELETE', `/drives/${encodeURIComponent(driveId)}/pending-scans/scanner`),
	deletePendingScan: (driveId: string, scan: string) =>
		call<void>('DELETE', `/drives/${encodeURIComponent(driveId)}/pending-scans/${scan}`),
	pendingScanUrl: (scan: string, thumb = false) => `/api/pending-scans/${scan}${thumb ? '?thumb=true' : ''}`,
	scanMatch: (id: number) => call<ScanMatch | null>('GET', `/jobs/${id}/scan-match`),
	ripExtras: (id: number) => call<Job>('POST', `/jobs/${id}/extras/rip`),
	useScan: (id: number) => call<Job>('POST', `/jobs/${id}/scan-match/use`),
	readWithVision: (id: number, scan: string) => call<{ queued: boolean }>('POST', `/jobs/${id}/scans/${scan}/vision`),
	saveListing: (id: number, listing: Listing) => call<ListingView>('PUT', `/jobs/${id}/listing`, listing),
	dropListingEdits: (id: number) => call<ListingView>('DELETE', `/jobs/${id}/listing/edits`),
	readAgain: (id: number) => call<ListingView>('POST', `/jobs/${id}/listing/read`),
	scanText: (id: number, scan: string) => call<ScanText>('GET', `/jobs/${id}/scans/${scan}/text`),
	scanUrl: (id: number, scan: string) => `/api/jobs/${id}/scans/${scan}`,
	scanThumbUrl: (id: number, sc: CoverScan, large = false) =>
		`/api/jobs/${id}/scans/${sc.id}/thumb?v=${sc.rotate}-${(sc.crop ?? []).join('.')}${large ? '&large=true' : ''}`,
	scanWholeUrl: (id: number, sc: CoverScan) => `/api/jobs/${id}/scans/${sc.id}/thumb?whole=true&v=${sc.rotate}`,
	uploadScan: (id: number, file: File, kind: ScanKind, source: 'upload' | 'camera') => {
		const form = new FormData();
		form.append('file', file);
		form.append('kind', kind);
		form.append('source', source);
		return call<CoverScan>('POST', `/jobs/${id}/scans`, form);
	},
	scanFromScanner: (id: number, body: ScannerScanRequest) =>
		call<ScannerRun>('POST', `/jobs/${id}/scans/scanner`, body),
	cancelScanner: (id: number) => call<void>('DELETE', `/jobs/${id}/scans/scanner`),
	updateScan: (id: number, scan: string, body: ScanUpdate) =>
		call<CoverScan>('PATCH', `/jobs/${id}/scans/${scan}`, body),
	deleteScan: (id: number, scan: string) => call<void>('DELETE', `/jobs/${id}/scans/${scan}`),
	scanCovers: (id: number, scan: string, split?: ScanSplit) =>
		call<ContributionView>('POST', `/jobs/${id}/scans/${scan}/covers`, split ?? null),
	movieSearch: (q: string, year: number | null) =>
		call<Candidate[]>('GET', `/movies/search?q=${encodeURIComponent(q)}${year ? `&year=${year}` : ''}`),
	setPreset: (id: number, preset: Preset) => call<Job>('PUT', `/jobs/${id}/preset`, preset),
	clearPreset: (id: number) => call<Job>('DELETE', `/jobs/${id}/preset`),
	tvSearch: (q: string) => call<Show[]>('GET', `/tv/search?q=${encodeURIComponent(q)}`),
	tvPropose: (id: number, body: TvProposeRequest) => call<Job>('POST', `/jobs/${id}/tv/propose`, body),
	tvMatchText: (id: number, text: string) => call<Job>('POST', `/jobs/${id}/tv/match-text`, { text }),
	tvConfirm: (id: number, mapping: TitleMapUpdate[]) =>
		call<Job>('POST', `/jobs/${id}/tv/confirm`, { mapping }),
	// Titles changed by hand, kept unconfirmed (the box's picks leave them alone).
	tvSetMapping: (id: number, mapping: TitleMapUpdate[]) =>
		call<Job>('PUT', `/jobs/${id}/tv/mapping`, { mapping }),
	tvSplit: (id: number, body: TvSplitRequest) => call<Job>('POST', `/jobs/${id}/tv/split`, body),
	thumbUrl: (id: number, title: number) => `/api/jobs/${id}/thumbs/${title}.jpg`,
	// ``at`` (s) keeps the browser's copy right when a cut by hand renumbers the marks.
	splitThumbUrl: (id: number, title: number, mark: number, at: number) =>
		`/api/jobs/${id}/split-thumbs/${title}/${mark}.jpg?t=${Math.round(at * 1000)}`,
	collections: () => call<Collection[]>('GET', '/collections'),
	collection: (id: number) => call<CollectionDetail>('GET', `/collections/${id}`),
	removeCollectionDisc: (id: number, jobId: number) =>
		call<CollectionDetail | null>('DELETE', `/collections/${id}/discs/${jobId}`),
	eject: (driveId: string) => call<void>('POST', `/drives/${driveId}/eject`),
	closeTray: (driveId: string) => call<void>('POST', `/drives/${driveId}/close`),
	redetect: () => call<Drive[]>('POST', '/drives/redetect'),
	renameDrive: (driveId: string, name: string) =>
		call<Drive | null>('PUT', `/drives/${encodeURIComponent(driveId)}/name`, { name }),
	driveStats: () => call<DriveStats[]>('GET', '/drives/stats'),
	deleteJobs: (ids: number[], deleteFiles = false) =>
		call<DeleteJobsResult>('POST', '/jobs/delete', { ids, delete_files: deleteFiles }),
	logs: (after: number, level: string) =>
		call<LogPage>('GET', `/logs?after=${after}&level=${encodeURIComponent(level)}`),
	rescan: (driveId: string) => call<Job>('POST', `/drives/${driveId}/scan`),
	devInsert: (driveId: string) => call<void>('POST', `/dev/insert/${driveId}`),
	storage: () => call<Filesystem[]>('GET', '/system/storage'),
	setupTools: () => call<SetupTools>('GET', '/setup/tools'),
	folder: (path: string, create = false) => call<FolderState>('POST', '/setup/folder', { path, create }),
	hardware: (refresh = false) => call<Hardware>('GET', `/system/hardware${refresh ? '?refresh=true' : ''}`),
	settings: () => call<SettingsView>('GET', '/settings'),
	settingFields: () => call<SettingField[]>('GET', '/settings/fields'),
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
