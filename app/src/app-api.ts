import { Injectable } from '@angular/core';

import type {
  EntryChange, FileTree, FileVersion, Preview, ProposalDetail, ProposalList, ProposalReview, ProposalSummary,
  SaveText, Session, SidecarSummary, SyncConflictDetail, SyncPairing, SyncResolution, SyncStatus, TextFile,
} from './api-client/types.gen';

export type { EntryChange, FileEntry, ProposalDetail, ProposalReview, ProposalSummary, SidecarSummary, SyncConflictDetail, SyncResolution, SyncStatus, SyncVersion, TextFile }
  from './api-client/types.gen';

export class ApiError extends Error {
  constructor(readonly status: number, detail: string) {
    super(detail);
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The app could not complete the request.';
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'The request could not be completed.';
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

@Injectable({ providedIn: 'root' })
export class AppApi {
  private session?: Promise<Session>;

  sidecars(): Promise<SidecarSummary[]> {
    return request('/api/v1/sidecars');
  }

  files(sidecarId: string): Promise<FileTree> {
    return request(this.sidecarUrl(sidecarId) + '/files');
  }

  open(sidecarId: string, path: string): Promise<TextFile> {
    return request(this.sidecarUrl(sidecarId) + '/file?' + new URLSearchParams({ path }));
  }

  imageUrl(sidecarId: string, path: string): string {
    return this.sidecarUrl(sidecarId) + '/image?' + new URLSearchParams({ path });
  }

  save(sidecarId: string, path: string, change: SaveText): Promise<FileVersion> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/file?' + new URLSearchParams({ path }), 'PUT', change);
  }

  create(sidecarId: string, entry: EntryChange): Promise<EntryChange> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/files', 'POST', entry);
  }

  move(sidecarId: string, source: string, target: string): Promise<EntryChange> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/files/move', 'POST', { source, target });
  }

  delete(sidecarId: string, path: string): Promise<EntryChange> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/files', 'DELETE', { path });
  }

  preview(sidecarId: string, path: string): Promise<Preview> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/previews', 'POST', { path });
  }

  proposals(sidecarId: string, reviewed: boolean): Promise<ProposalList> {
    return request(this.sidecarUrl(sidecarId) + '/proposals?' + new URLSearchParams({ reviewed: String(reviewed) }));
  }

  proposal(sidecarId: string, id: string): Promise<ProposalDetail> {
    return request(this.sidecarUrl(sidecarId) + '/proposals/' + encodeURIComponent(id));
  }

  review(sidecarId: string, id: string, decision: ProposalReview['decision']): Promise<ProposalSummary> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/proposals/' + encodeURIComponent(id), 'POST', { decision });
  }

  syncStatus(sidecarId: string): Promise<SyncStatus> {
    return request(this.sidecarUrl(sidecarId) + '/sync');
  }

  sync(sidecarId: string, action = '', body = {}): Promise<SyncStatus> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/sync' + (action ? '/' + action : ''), 'POST', body);
  }

  async syncPair(sidecarId: string): Promise<string> {
    const pairing = await this.mutate<SyncPairing>(this.sidecarUrl(sidecarId) + '/sync/pair', 'POST', {});
    return pairing.code;
  }

  syncConflict(sidecarId: string, id: string): Promise<SyncConflictDetail> {
    return request(this.sidecarUrl(sidecarId) + '/sync/conflicts/' + encodeURIComponent(id));
  }

  syncDownload(sidecarId: string, id: string, copy: string, digest: string): string {
    return this.sidecarUrl(sidecarId) + '/sync/conflicts/' + encodeURIComponent(id) + '/download?'
      + new URLSearchParams({ copy, digest });
  }

  resolveSync(sidecarId: string, id: string, resolution: SyncResolution): Promise<SyncStatus> {
    return this.mutate(this.sidecarUrl(sidecarId) + '/sync/conflicts/' + encodeURIComponent(id), 'POST', resolution);
  }

  private async mutate<T>(url: string, method: string, change: object): Promise<T> {
    this.session ??= request<Session>('/api/v1/session').catch(error => {
      this.session = undefined;
      throw error;
    });
    const { token } = await this.session;
    try {
      return await request(url, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-Psydcar-Token': token },
        body: JSON.stringify(change),
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) this.session = undefined;
      throw error;
    }
  }

  private sidecarUrl(sidecarId: string): string {
    return '/api/v1/sidecars/' + encodeURIComponent(sidecarId);
  }
}
