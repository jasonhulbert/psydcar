import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
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
export class App implements OnInit {
  private readonly api = inject(ApiService);

  readonly sidecars = signal<Sidecar[]>([]);
  readonly selectedId = signal<string | null>(null);
  readonly selectedSidecar = computed(() => {
    const selectedId = this.selectedId();
    return this.sidecars().find((sidecar) => sidecar.id === selectedId) ?? null;
  });

  readonly files = signal<FileEntry[]>([]);
  readonly errors = signal<IndexingError[]>([]);
  readonly mcpConfig = signal<McpConfig | null>(null);
  readonly searchResults = signal<SearchResult[]>([]);
  readonly directoryBrowse = signal<DirectoryBrowse | null>(null);

  activeTab: Tab = 'files';
  loadingSidecars = true;
  loadingDetails = false;
  creating = false;
  operating = false;
  browsing = false;
  searching = false;

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

  async loadSidecars(selectId?: string) {
    this.loadingSidecars = true;
    this.appError = '';
    try {
      const sidecars = await firstValueFrom(this.api.listSidecars());
      this.sidecars.set(sidecars);
      const nextSelected = selectId ?? this.selectedId() ?? sidecars[0]?.id ?? null;
      this.selectedId.set(sidecars.some((sidecar) => sidecar.id === nextSelected) ? nextSelected : null);
      await this.loadDetails();
    } catch (error) {
      this.appError = this.describeError(error);
    } finally {
      this.loadingSidecars = false;
    }
  }

  async selectSidecar(sidecarId: string) {
    this.selectedId.set(sidecarId);
    this.copyStatus = '';
    await this.loadDetails();
  }

  async createSidecar() {
    if (!this.createForm.sourceRoot.trim()) {
      this.createError = 'Enter or select a source directory path.';
      return;
    }

    this.creating = true;
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
      this.creating = false;
    }
  }

  async runOperation(operation: 'refresh' | 'rebuild') {
    const sidecar = this.selectedSidecar();
    if (!sidecar) {
      return;
    }

    this.operating = true;
    this.detailError = '';
    try {
      if (operation === 'refresh') {
        await firstValueFrom(this.api.refreshSidecar(sidecar.id));
      } else {
        await firstValueFrom(this.api.rebuildSidecar(sidecar.id));
      }
      await this.loadSidecars(sidecar.id);
    } catch (error) {
      this.detailError = this.describeError(error);
    } finally {
      this.operating = false;
    }
  }

  async loadDetails() {
    const sidecar = this.selectedSidecar();
    this.files.set([]);
    this.errors.set([]);
    this.mcpConfig.set(null);
    this.searchResults.set([]);
    this.searchError = '';
    this.detailError = '';
    if (!sidecar) {
      return;
    }

    this.loadingDetails = true;
    try {
      const [files, errors, mcpConfig] = await Promise.all([
        firstValueFrom(this.api.listFiles(sidecar.id)),
        firstValueFrom(this.api.listErrors(sidecar.id)),
        firstValueFrom(this.api.getMcpConfig(sidecar.id)),
      ]);
      this.files.set(files.files);
      this.errors.set(errors.errors);
      this.mcpConfig.set(mcpConfig);
    } catch (error) {
      this.detailError = this.describeError(error);
    } finally {
      this.loadingDetails = false;
    }
  }

  async loadDirectory(path?: string | null) {
    this.browsing = true;
    this.browseError = '';
    try {
      const browse = await firstValueFrom(this.api.browseDirectories(path ?? undefined));
      this.directoryBrowse.set(browse);
      this.browsePath = browse.path;
    } catch (error) {
      this.browseError = this.describeError(error);
    } finally {
      this.browsing = false;
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

    this.searching = true;
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
      this.searching = false;
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
