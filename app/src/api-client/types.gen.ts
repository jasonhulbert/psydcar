// Generated from the Psydcar API contract.

export type ClientOptions = {
    baseUrl: `${string}://${string}` | (string & {});
};

/**
 * Conflict
 */
export type Conflict = {
    /**
     * Detail
     */
    detail: string;
    /**
     * Version
     */
    version: string | null;
};

/**
 * EntryChange
 */
export type EntryChange = {
    /**
     * Path
     */
    path: string;
    /**
     * Type
     */
    type: 'file' | 'folder';
};

/**
 * EntryLocation
 */
export type EntryLocation = {
    /**
     * Path
     */
    path: string;
};

/**
 * FileEntry
 */
export type FileEntry = {
    /**
     * Path
     */
    path: string;
    /**
     * Type
     */
    type: 'file' | 'folder';
    /**
     * Editable
     */
    editable?: boolean;
    /**
     * Image
     */
    image?: boolean;
    /**
     * Viewable
     */
    viewable?: boolean;
    /**
     * Tags
     */
    tags?: Array<string>;
    /**
     * Size
     */
    size?: number | null;
    /**
     * Mtime Ns
     */
    mtime_ns?: number | null;
};

/**
 * FileTree
 */
export type FileTree = {
    /**
     * Entries
     */
    entries: Array<FileEntry>;
    /**
     * Truncated
     */
    truncated: boolean;
};

/**
 * FileVersion
 */
export type FileVersion = {
    /**
     * Path
     */
    path: string;
    /**
     * Version
     */
    version: string;
    /**
     * Size
     */
    size: number;
    /**
     * Mtime Ns
     */
    mtime_ns: number;
    /**
     * Modified At
     */
    modified_at: string;
};

/**
 * HTTPValidationError
 */
export type HttpValidationError = {
    /**
     * Detail
     */
    detail?: Array<ValidationError>;
};

/**
 * MoveEntry
 */
export type MoveEntry = {
    /**
     * Source
     */
    source: string;
    /**
     * Target
     */
    target: string;
};

/**
 * Preview
 */
export type Preview = {
    /**
     * Path
     */
    path: string;
};

/**
 * ProposalDetail
 */
export type ProposalDetail = {
    /**
     * Id
     */
    id: string;
    /**
     * Path
     */
    path: string;
    /**
     * Agent Name
     */
    agent_name: string;
    /**
     * Summary
     */
    summary: string;
    /**
     * Status
     */
    status: 'pending' | 'approved' | 'rejected';
    /**
     * Created At
     */
    created_at: string;
    /**
     * Decided At
     */
    decided_at: string | null;
    /**
     * Stale
     */
    stale: boolean;
    /**
     * Diff
     */
    diff: string | null;
    /**
     * Before
     */
    before: string | null;
    /**
     * Proposed
     */
    proposed: string | null;
};

/**
 * ProposalList
 */
export type ProposalList = {
    /**
     * Items
     */
    items: Array<ProposalSummary>;
    /**
     * Pending Count
     */
    pending_count: number;
};

/**
 * ProposalReview
 */
export type ProposalReview = {
    /**
     * Decision
     */
    decision: 'approved' | 'rejected';
};

/**
 * ProposalSummary
 */
export type ProposalSummary = {
    /**
     * Id
     */
    id: string;
    /**
     * Path
     */
    path: string;
    /**
     * Agent Name
     */
    agent_name: string;
    /**
     * Summary
     */
    summary: string;
    /**
     * Status
     */
    status: 'pending' | 'approved' | 'rejected';
    /**
     * Created At
     */
    created_at: string;
    /**
     * Decided At
     */
    decided_at: string | null;
};

/**
 * SaveText
 */
export type SaveText = {
    /**
     * Content
     */
    content: string;
    /**
     * Version
     */
    version: string;
    /**
     * Newline
     */
    newline?: '\n' | '\r\n';
    /**
     * Bom
     */
    bom?: boolean;
};

/**
 * Session
 */
export type Session = {
    /**
     * Token
     */
    token: string;
};

/**
 * SidecarSummary
 */
export type SidecarSummary = {
    /**
     * Id
     */
    id: string;
    /**
     * Name
     */
    name: string;
    /**
     * Source Path
     */
    source_path: string;
    /**
     * Status
     */
    status: string;
};

/**
 * SyncConflictDetail
 */
export type SyncConflictDetail = {
    /**
     * Id
     */
    id: string;
    /**
     * Path
     */
    path: string;
    /**
     * Diff
     */
    diff: string | null;
    local: SyncVersion | null;
    cloud: SyncVersion | null;
};

/**
 * SyncConflictSummary
 */
export type SyncConflictSummary = {
    /**
     * Id
     */
    id: string;
    /**
     * Path
     */
    path: string;
};

/**
 * SyncPairing
 */
export type SyncPairing = {
    /**
     * Code
     */
    code: string;
};

/**
 * SyncResolution
 */
export type SyncResolution = {
    /**
     * Decision
     */
    decision: 'local' | 'cloud' | 'merge';
    /**
     * Local Digest
     */
    local_digest: string | null;
    /**
     * Merged
     */
    merged?: string | null;
};

/**
 * SyncSetup
 */
export type SyncSetup = {
    /**
     * Enrollment Key
     */
    enrollment_key?: string;
};

/**
 * SyncStatus
 */
export type SyncStatus = {
    /**
     * Available
     */
    available: boolean;
    /**
     * Account Active
     */
    account_active: boolean;
    /**
     * Linked
     */
    linked: boolean;
    /**
     * Last Synced At
     */
    last_synced_at: string | null;
    /**
     * Error
     */
    error: string | null;
    /**
     * Revision
     */
    revision: number;
    /**
     * Conflicts
     */
    conflicts: Array<SyncConflictSummary>;
};

/**
 * SyncVersion
 */
export type SyncVersion = {
    /**
     * Digest
     */
    digest: string;
    /**
     * Size
     */
    size: number;
    /**
     * Text
     */
    text: string | null;
};

/**
 * TextFile
 */
export type TextFile = {
    /**
     * Path
     */
    path: string;
    /**
     * Version
     */
    version: string;
    /**
     * Size
     */
    size: number;
    /**
     * Mtime Ns
     */
    mtime_ns: number;
    /**
     * Modified At
     */
    modified_at: string;
    /**
     * Content
     */
    content: string;
    /**
     * Newline
     */
    newline: '\n' | '\r\n';
    /**
     * Bom
     */
    bom: boolean;
};

/**
 * ValidationError
 */
export type ValidationError = {
    /**
     * Location
     */
    loc: Array<string | number>;
    /**
     * Message
     */
    msg: string;
    /**
     * Error Type
     */
    type: string;
    /**
     * Input
     */
    input?: unknown;
    /**
     * Context
     */
    ctx?: {
        [key: string]: unknown;
    };
};

export type SessionData = {
    body?: never;
    path?: never;
    query?: never;
    url: '/api/v1/session';
};

export type SessionResponses = {
    /**
     * Successful Response
     */
    200: Session;
};

export type SessionResponse = SessionResponses[keyof SessionResponses];

export type ListSidecarsData = {
    body?: never;
    path?: never;
    query?: never;
    url: '/api/v1/sidecars';
};

export type ListSidecarsResponses = {
    /**
     * Response Listsidecars
     *
     * Successful Response
     */
    200: Array<SidecarSummary>;
};

export type ListSidecarsResponse = ListSidecarsResponses[keyof ListSidecarsResponses];

export type DeleteEntryData = {
    body: EntryLocation;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/files';
};

export type DeleteEntryErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type DeleteEntryError = DeleteEntryErrors[keyof DeleteEntryErrors];

export type DeleteEntryResponses = {
    /**
     * Successful Response
     */
    200: EntryChange;
};

export type DeleteEntryResponse = DeleteEntryResponses[keyof DeleteEntryResponses];

export type ListFilesData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/files';
};

export type ListFilesErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type ListFilesError = ListFilesErrors[keyof ListFilesErrors];

export type ListFilesResponses = {
    /**
     * Successful Response
     */
    200: FileTree;
};

export type ListFilesResponse = ListFilesResponses[keyof ListFilesResponses];

export type CreateEntryData = {
    body: EntryChange;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/files';
};

export type CreateEntryErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type CreateEntryError = CreateEntryErrors[keyof CreateEntryErrors];

export type CreateEntryResponses = {
    /**
     * Successful Response
     */
    201: EntryChange;
};

export type CreateEntryResponse = CreateEntryResponses[keyof CreateEntryResponses];

export type ListProposalsData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: {
        /**
         * Reviewed
         */
        reviewed?: boolean;
    };
    url: '/api/v1/sidecars/{sidecar_id}/proposals';
};

export type ListProposalsErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type ListProposalsError = ListProposalsErrors[keyof ListProposalsErrors];

export type ListProposalsResponses = {
    /**
     * Successful Response
     */
    200: ProposalList;
};

export type ListProposalsResponse = ListProposalsResponses[keyof ListProposalsResponses];

export type OpenProposalData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
        /**
         * Proposal Id
         */
        proposal_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/proposals/{proposal_id}';
};

export type OpenProposalErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type OpenProposalError = OpenProposalErrors[keyof OpenProposalErrors];

export type OpenProposalResponses = {
    /**
     * Successful Response
     */
    200: ProposalDetail;
};

export type OpenProposalResponse = OpenProposalResponses[keyof OpenProposalResponses];

export type ReviewProposalData = {
    body: ProposalReview;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
        /**
         * Proposal Id
         */
        proposal_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/proposals/{proposal_id}';
};

export type ReviewProposalErrors = {
    /**
     * Conflict
     */
    409: Conflict;
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type ReviewProposalError = ReviewProposalErrors[keyof ReviewProposalErrors];

export type ReviewProposalResponses = {
    /**
     * Successful Response
     */
    200: ProposalSummary;
};

export type ReviewProposalResponse = ReviewProposalResponses[keyof ReviewProposalResponses];

export type SyncStatusData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync';
};

export type SyncStatusErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type SyncStatusError = SyncStatusErrors[keyof SyncStatusErrors];

export type SyncStatusResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type SyncStatusResponse = SyncStatusResponses[keyof SyncStatusResponses];

export type SyncNowData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync';
};

export type SyncNowErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type SyncNowError = SyncNowErrors[keyof SyncNowErrors];

export type SyncNowResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type SyncNowResponse = SyncNowResponses[keyof SyncNowResponses];

export type EnableSyncData = {
    body: SyncSetup;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/enable';
};

export type EnableSyncErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type EnableSyncError = EnableSyncErrors[keyof EnableSyncErrors];

export type EnableSyncResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type EnableSyncResponse = EnableSyncResponses[keyof EnableSyncResponses];

export type JoinSyncData = {
    body: SyncPairing;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/join';
};

export type JoinSyncErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type JoinSyncError = JoinSyncErrors[keyof JoinSyncErrors];

export type JoinSyncResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type JoinSyncResponse = JoinSyncResponses[keyof JoinSyncResponses];

export type PairSyncData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/pair';
};

export type PairSyncErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type PairSyncError = PairSyncErrors[keyof PairSyncErrors];

export type PairSyncResponses = {
    /**
     * Successful Response
     */
    200: SyncPairing;
};

export type PairSyncResponse = PairSyncResponses[keyof PairSyncResponses];

export type RevokeSyncData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/revoke';
};

export type RevokeSyncErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type RevokeSyncError = RevokeSyncErrors[keyof RevokeSyncErrors];

export type RevokeSyncResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type RevokeSyncResponse = RevokeSyncResponses[keyof RevokeSyncResponses];

export type SyncConflictData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
        /**
         * Conflict Id
         */
        conflict_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}';
};

export type SyncConflictErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type SyncConflictError = SyncConflictErrors[keyof SyncConflictErrors];

export type SyncConflictResponses = {
    /**
     * Successful Response
     */
    200: SyncConflictDetail;
};

export type SyncConflictResponse = SyncConflictResponses[keyof SyncConflictResponses];

export type ResolveSyncConflictData = {
    body: SyncResolution;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
        /**
         * Conflict Id
         */
        conflict_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}';
};

export type ResolveSyncConflictErrors = {
    /**
     * Conflict
     */
    409: Conflict;
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type ResolveSyncConflictError = ResolveSyncConflictErrors[keyof ResolveSyncConflictErrors];

export type ResolveSyncConflictResponses = {
    /**
     * Successful Response
     */
    200: SyncStatus;
};

export type ResolveSyncConflictResponse = ResolveSyncConflictResponses[keyof ResolveSyncConflictResponses];

export type DownloadSyncConflictData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
        /**
         * Conflict Id
         */
        conflict_id: string;
    };
    query: {
        /**
         * Copy
         */
        copy: 'local' | 'cloud';
        /**
         * Digest
         */
        digest: string;
    };
    url: '/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}/download';
};

export type DownloadSyncConflictErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type DownloadSyncConflictError = DownloadSyncConflictErrors[keyof DownloadSyncConflictErrors];

export type DownloadSyncConflictResponses = {
    /**
     * Successful Response
     */
    200: unknown;
};

export type MoveEntryData = {
    body: MoveEntry;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/files/move';
};

export type MoveEntryErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type MoveEntryError = MoveEntryErrors[keyof MoveEntryErrors];

export type MoveEntryResponses = {
    /**
     * Successful Response
     */
    200: EntryChange;
};

export type MoveEntryResponse = MoveEntryResponses[keyof MoveEntryResponses];

export type StartPreviewData = {
    body: EntryLocation;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query?: never;
    url: '/api/v1/sidecars/{sidecar_id}/previews';
};

export type StartPreviewErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type StartPreviewError = StartPreviewErrors[keyof StartPreviewErrors];

export type StartPreviewResponses = {
    /**
     * Successful Response
     */
    201: Preview;
};

export type StartPreviewResponse = StartPreviewResponses[keyof StartPreviewResponses];

export type OpenFileData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query: {
        /**
         * Path
         */
        path: string;
    };
    url: '/api/v1/sidecars/{sidecar_id}/file';
};

export type OpenFileErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type OpenFileError = OpenFileErrors[keyof OpenFileErrors];

export type OpenFileResponses = {
    /**
     * Successful Response
     */
    200: TextFile;
};

export type OpenFileResponse = OpenFileResponses[keyof OpenFileResponses];

export type SaveFileData = {
    body: SaveText;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query: {
        /**
         * Path
         */
        path: string;
    };
    url: '/api/v1/sidecars/{sidecar_id}/file';
};

export type SaveFileErrors = {
    /**
     * Conflict
     */
    409: Conflict;
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type SaveFileError = SaveFileErrors[keyof SaveFileErrors];

export type SaveFileResponses = {
    /**
     * Successful Response
     */
    200: FileVersion;
};

export type SaveFileResponse = SaveFileResponses[keyof SaveFileResponses];

export type ViewImageData = {
    body?: never;
    path: {
        /**
         * Sidecar Id
         */
        sidecar_id: string;
    };
    query: {
        /**
         * Path
         */
        path: string;
    };
    url: '/api/v1/sidecars/{sidecar_id}/image';
};

export type ViewImageErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
};

export type ViewImageError = ViewImageErrors[keyof ViewImageErrors];

export type ViewImageResponses = {
    /**
     * Successful Response
     */
    200: unknown;
};
