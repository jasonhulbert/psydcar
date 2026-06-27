import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

export interface Sidecar {
  id: string;
  name: string;
  root_path: string;
  created_at: string;
  updated_at: string;
  indexing_status: string;
  last_refresh_at: string | null;
  indexed_file_count: number;
  chunk_count: number;
  error_count: number;
  config: {
    max_file_size_bytes: number;
  };
}

export interface DirectoryEntry {
  name: string;
  path: string;
}

export interface DirectoryBrowse {
  path: string;
  parent_path: string | null;
  entries: DirectoryEntry[];
}

export interface FileEntry {
  relative_path: string;
  extension: string;
  size_bytes: number;
}

export interface IndexingError {
  relative_path?: string;
  message?: string;
  error?: string;
  [key: string]: string | undefined;
}

export interface McpConfig {
  sidecar_id: string;
  command: string;
  args: string[];
  claude_code_config: Record<string, unknown>;
  codex_config: string;
}

export interface SearchResult {
  chunk_id?: number;
  relative_path: string;
  score?: number;
  preview?: string;
  snippet?: string;
  start_line?: number;
  end_line?: number;
  line_number?: number;
  [key: string]: unknown;
}

export interface SearchResponse {
  sidecar_id: string;
  query: string;
  mode: 'keyword' | 'semantic' | 'hybrid';
  results: SearchResult[];
}

export interface CreateSidecarPayload {
  id?: string;
  name?: string;
  source_root: string;
  max_file_size_bytes: number;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  listSidecars() {
    return this.http.get<Sidecar[]>('/api/sidecars');
  }

  createSidecar(payload: CreateSidecarPayload) {
    return this.http.post<Sidecar>('/api/sidecars', payload);
  }

  deleteSidecar(sidecarId: string) {
    return this.http.delete<void>(`/api/sidecars/${encodeURIComponent(sidecarId)}`);
  }

  rebuildSidecar(sidecarId: string) {
    return this.http.post(`/api/sidecars/${encodeURIComponent(sidecarId)}/rebuild`, {});
  }

  refreshSidecar(sidecarId: string) {
    return this.http.post(`/api/sidecars/${encodeURIComponent(sidecarId)}/refresh`, {});
  }

  listFiles(sidecarId: string) {
    return this.http.get<{ sidecar_id: string; files: FileEntry[] }>(
      `/api/sidecars/${encodeURIComponent(sidecarId)}/files`,
    );
  }

  listErrors(sidecarId: string) {
    return this.http.get<{ sidecar_id: string; errors: IndexingError[] }>(
      `/api/sidecars/${encodeURIComponent(sidecarId)}/errors`,
    );
  }

  getMcpConfig(sidecarId: string) {
    return this.http.get<McpConfig>(`/api/sidecars/${encodeURIComponent(sidecarId)}/mcp-config`);
  }

  searchSidecar(sidecarId: string, query: string, mode: 'keyword' | 'semantic' | 'hybrid', limit = 10) {
    const params = new HttpParams().set('q', query).set('mode', mode).set('limit', limit);
    return this.http.get<SearchResponse>(`/api/sidecars/${encodeURIComponent(sidecarId)}/search`, {
      params,
    });
  }

  browseDirectories(path?: string) {
    const params = path ? new HttpParams().set('path', path) : undefined;
    return this.http.get<DirectoryBrowse>('/api/directories', { params });
  }
}
