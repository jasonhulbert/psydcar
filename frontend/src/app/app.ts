import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import {
  ApiService,
  DirectoryBrowse,
  FileEntry,
  IndexingError,
  McpConfig,
  SearchResult,
  Sidecar,
} from './api.service';

type SearchMode = 'keyword' | 'semantic' | 'hybrid';
type Tab = 'files' | 'errors' | 'mcp' | 'search';

@Component({
  selector: 'app-root',
  imports: [CommonModule, FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly indexingPollMs = 1200;
  private indexingPollHandle: ReturnType<typeof setTimeout> | null = null;
  private pollingIndexState = false;
  private detailLoadToken = 0;

  readonly sidecars = signal<Sidecar[]>([]);
  readonly selectedId = signal<string | null>(null);
  readonly selectedSidecar = computed(() => {
    const selectedId = this.selectedId();
    return this.sidecars().find((sidecar) => sidecar.id === selectedId) ?? null;
  });
  readonly selectedIsIndexing = computed(
    () => this.selectedSidecar()?.indexing_status === 'indexing',
  );

  readonly files = signal<FileEntry[]>([]);
  readonly errors = signal<IndexingError[]>([]);
  readonly mcpConfig = signal<McpConfig | null>(null);
  readonly searchResults = signal<SearchResult[]>([]);
  readonly directoryBrowse = signal<DirectoryBrowse | null>(null);

  activeTab: Tab = 'files';
  readonly loadingSidecars = signal(true);
  readonly loadingDetails = signal(false);
  readonly creating = signal(false);
  readonly operating = signal(false);
  readonly browsing = signal(false);
  readonly searching = signal(false);

  appError = '';
  createError = '';
  detailError = '';
  browseError = '';
  searchError = '';
  copyStatus = '';

  createForm = {
    id: '',
    name: '',
    sourceRoot: '',
    maxFileSizeBytes: 1_000_000,
  };

  browsePath = '';
  searchQuery = '';
  searchMode: SearchMode = 'hybrid';
  searchLimit = 10;

  async ngOnInit() {
    await this.loadSidecars();
    await this.loadDirectory();
  }

  ngOnDestroy() {
    this.clearIndexingPoll();
  }

  async loadSidecars(
    selectId?: string,
    options: { showLoading?: boolean; loadDetails?: boolean } = {},
  ) {
    const showLoading = options.showLoading ?? true;
    const loadDetails = options.loadDetails ?? true;
    const previousSelected = this.selectedSidecar();
    if (showLoading) {
      this.loadingSidecars.set(true);
    }
    this.appError = '';
    try {
      const sidecars = await firstValueFrom(this.api.listSidecars());
      this.sidecars.set(sidecars);
      const nextSelected = selectId ?? this.selectedId() ?? sidecars[0]?.id ?? null;
      this.selectedId.set(sidecars.some((sidecar) => sidecar.id === nextSelected) ? nextSelected : null);
      const selected = this.selectedSidecar();
      if (loadDetails && selected?.indexing_status !== 'indexing') {
        await this.loadDetails();
      } else if (
        selected &&
        previousSelected?.id === selected?.id &&
        previousSelected.indexing_status === 'indexing' &&
        selected.indexing_status !== 'indexing'
      ) {
        await this.loadDetails();
      } else if (selected?.indexing_status === 'indexing') {
        this.cancelDetailLoading();
      }
      this.syncIndexingPoll();
    } catch (error) {
      this.appError = this.describeError(error);
    } finally {
      if (showLoading) {
        this.loadingSidecars.set(false);
      }
    }
  }

  async selectSidecar(sidecarId: string) {
    this.selectedId.set(sidecarId);
    this.copyStatus = '';
    if (this.selectedSidecar()?.indexing_status === 'indexing') {
      this.cancelDetailLoading();
      this.syncIndexingPoll();
      return;
    }
    await this.loadDetails();
  }

  async createSidecar() {
    if (!this.createForm.sourceRoot.trim()) {
      this.createError = 'Enter or select a source directory path.';
      return;
    }

    this.creating.set(true);
    this.createError = '';
    try {
      const sidecar = await firstValueFrom(
        this.api.createSidecar({
          id: this.cleanOptional(this.createForm.id),
          name: this.cleanOptional(this.createForm.name),
          source_root: this.createForm.sourceRoot.trim(),
          max_file_size_bytes: this.createForm.maxFileSizeBytes,
        }),
      );
      this.createForm = {
        id: '',
        name: '',
        sourceRoot: '',
        maxFileSizeBytes: 1_000_000,
      };
      await this.loadSidecars(sidecar.id);
    } catch (error) {
      this.createError = this.describeError(error);
    } finally {
      this.creating.set(false);
    }
  }

  async runOperation(operation: 'refresh' | 'rebuild') {
    const sidecar = this.selectedSidecar();
    if (!sidecar) {
      return;
    }

    this.operating.set(true);
    this.detailError = '';
    try {
      if (operation === 'refresh') {
        await firstValueFrom(this.api.refreshSidecar(sidecar.id));
      } else {
        await firstValueFrom(this.api.rebuildSidecar(sidecar.id));
      }
      await this.loadSidecars(sidecar.id, { showLoading: false, loadDetails: false });
    } catch (error) {
      this.detailError = this.describeError(error);
    } finally {
      this.operating.set(false);
    }
  }

  async loadDetails() {
    const token = ++this.detailLoadToken;
    const sidecar = this.selectedSidecar();
    this.files.set([]);
    this.errors.set([]);
    this.mcpConfig.set(null);
    this.searchResults.set([]);
    this.searchError = '';
    this.detailError = '';
    if (!sidecar) {
      this.loadingDetails.set(false);
      return;
    }

    this.loadingDetails.set(true);
    try {
      const [files, errors, mcpConfig] = await Promise.all([
        firstValueFrom(this.api.listFiles(sidecar.id)),
        firstValueFrom(this.api.listErrors(sidecar.id)),
        firstValueFrom(this.api.getMcpConfig(sidecar.id)),
      ]);
      if (token !== this.detailLoadToken) {
        return;
      }
      this.files.set(files.files);
      this.errors.set(errors.errors);
      this.mcpConfig.set(mcpConfig);
    } catch (error) {
      if (token !== this.detailLoadToken) {
        return;
      }
      this.detailError = this.describeError(error);
    } finally {
      if (token === this.detailLoadToken) {
        this.loadingDetails.set(false);
      }
    }
  }

  async loadDirectory(path?: string | null) {
    this.browsing.set(true);
    this.browseError = '';
    try {
      const browse = await firstValueFrom(this.api.browseDirectories(path ?? undefined));
      this.directoryBrowse.set(browse);
      this.browsePath = browse.path;
    } catch (error) {
      this.browseError = this.describeError(error);
    } finally {
      this.browsing.set(false);
    }
  }

  useDirectory(path: string) {
    this.createForm.sourceRoot = path;
  }

  async search() {
    const sidecar = this.selectedSidecar();
    if (!sidecar || !this.searchQuery.trim()) {
      this.searchError = 'Enter a search query.';
      return;
    }

    this.searching.set(true);
    this.searchError = '';
    try {
      const response = await firstValueFrom(
        this.api.searchSidecar(
          sidecar.id,
          this.searchQuery.trim(),
          this.searchMode,
          this.searchLimit,
        ),
      );
      this.searchResults.set(response.results);
    } catch (error) {
      this.searchError = this.describeError(error);
      this.searchResults.set([]);
    } finally {
      this.searching.set(false);
    }
  }

  async copyMcpConfig() {
    const config = this.mcpConfig();
    if (!config) {
      return;
    }

    await navigator.clipboard.writeText(this.formattedMcpConfig());
    this.copyStatus = 'Copied';
  }

  formattedMcpConfig() {
    return JSON.stringify(this.mcpConfig()?.config ?? {}, null, 2);
  }

  trackSidecar(_: number, sidecar: Sidecar) {
    return sidecar.id;
  }

  trackPath(_: number, entry: { path?: string; relative_path?: string }) {
    return entry.path ?? entry.relative_path ?? _;
  }

  private cleanOptional(value: string) {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }

  private syncIndexingPoll() {
    if (this.sidecars().some((sidecar) => sidecar.indexing_status === 'indexing')) {
      this.scheduleIndexingPoll();
      return;
    }
    this.clearIndexingPoll();
  }

  private scheduleIndexingPoll() {
    if (this.indexingPollHandle) {
      return;
    }
    this.indexingPollHandle = setTimeout(() => {
      this.indexingPollHandle = null;
      void this.pollIndexingState();
    }, this.indexingPollMs);
  }

  private async pollIndexingState() {
    if (this.pollingIndexState) {
      this.scheduleIndexingPoll();
      return;
    }
    this.pollingIndexState = true;
    try {
      await this.loadSidecars(undefined, { showLoading: false, loadDetails: false });
    } finally {
      this.pollingIndexState = false;
    }
  }

  private clearIndexingPoll() {
    if (this.indexingPollHandle) {
      clearTimeout(this.indexingPollHandle);
      this.indexingPollHandle = null;
    }
  }

  private cancelDetailLoading() {
    this.detailLoadToken++;
    this.loadingDetails.set(false);
  }

  private describeError(error: unknown) {
    if (error instanceof HttpErrorResponse) {
      if (typeof error.error?.detail === 'string') {
        return error.error.detail;
      }
      if (Array.isArray(error.error?.detail)) {
        return error.error.detail.map((detail: { msg?: string }) => detail.msg ?? 'Invalid value').join(' ');
      }
      return `${error.status} ${error.statusText || 'Request failed'}`.trim();
    }
    return error instanceof Error ? error.message : 'Request failed';
  }
}
