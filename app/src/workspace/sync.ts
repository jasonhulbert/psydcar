import { DatePipe, DecimalPipe, NgTemplateOutlet } from '@angular/common';
import { Component, computed, DestroyRef, inject, input, output, signal, viewChild } from '@angular/core';

import { ButtonDirective } from '../design-system/button';
import { AppApi, errorMessage, type SyncConflictDetail, type SyncResolution, type SyncStatus } from '../app-api';
import { SegmentedControlComponent } from '../design-system/segmented-control';
import { BadgeComponent } from '../design-system/badge';
import { DiffComponent } from '../design-system/diff';
import { DialogComponent } from '../design-system/dialog';
import { EmptyStateComponent } from '../design-system/empty-state';
import { IconComponent } from '../design-system/icon';

@Component({
  selector: 'psydcar-sync',
  imports: [ButtonDirective, SegmentedControlComponent, BadgeComponent, DatePipe, DecimalPipe, DialogComponent, DiffComponent, EmptyStateComponent, IconComponent, NgTemplateOutlet],
  templateUrl: './sync.html',
  styleUrl: './sync.css',
})
export class SyncComponent {
  readonly sidecarId = input.required<string>();
  readonly paused = input(false);
  readonly draftPath = input<string>();
  readonly changed = output<void>();
  readonly status = signal<SyncStatus | null>(null);
  readonly detail = signal<SyncConflictDetail | null>(null);
  readonly working = signal(false);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly pairing = signal('');
  readonly enrollmentKey = signal('');
  readonly joinCode = signal('');
  readonly comparisonOptions = [{ value: false, label: 'Diff' }, { value: true, label: 'Full files' }];
  readonly fullVersions = signal(false);
  readonly mergeText = signal<string | null>(null);
  readonly blocked = computed(() => !!this.draftPath() && this.detail()?.path === this.draftPath());
  readonly canMerge = computed(() => {
    const detail = this.detail();
    return !!detail && [detail.local, detail.cloud].every(copy => !copy || copy.text !== null);
  });

  private readonly api = inject(AppApi);
  private readonly destroy = inject(DestroyRef);
  private readonly dialog = viewChild.required(DialogComponent);
  private showing = false;
  private selection = 0;
  private nextAttempt = 0;
  private delay = 30_000;

  ngOnInit(): void {
    const poll = async () => {
      if (this.working() || this.showing || this.paused() || document.hidden || Date.now() < this.nextAttempt) return;
      try {
        if (!this.status()?.linked) this.update(await this.api.syncStatus(this.sidecarId()));
        if (this.status()?.linked && !this.paused() && !this.showing && !this.destroy.destroyed) await this.run();
        else this.nextAttempt = Date.now() + this.delay;
      } catch {
        this.nextAttempt = Date.now() + this.delay;
      }
    };
    const timer = window.setInterval(poll, 5000);
    const reconnect = () => { this.nextAttempt = 0; void poll(); };
    window.addEventListener('online', reconnect);
    this.destroy.onDestroy(() => {
      window.clearInterval(timer);
      window.removeEventListener('online', reconnect);
    });
    void poll();
  }

  async show(): Promise<void> {
    this.showing = true;
    this.notice.set('');
    this.error.set('');
    this.dialog().show();
    await this.load();
  }

  close(): void {
    this.showing = false;
    this.selection++;
    this.loading.set(false);
    this.detail.set(null);
    this.mergeText.set(null);
    this.pairing.set('');
    this.enrollmentKey.set('');
    this.joinCode.set('');
  }

  async load(): Promise<void> {
    try {
      this.update(await this.api.syncStatus(this.sidecarId()));
    } catch (error) {
      this.error.set(errorMessage(error));
    }
  }

  async run(): Promise<void> {
    if (this.paused() || this.working() || this.mergeText() !== null) return;
    await this.action(async () => {
      this.detail.set(null);
      this.mergeText.set(null);
      const status = await this.api.sync(this.sidecarId());
      this.update(status);
      if (this.showing && !status.error) this.notice.set(status.conflicts.length ? 'Sync complete. Review the conflicts below.' : 'Sync complete.');
    });
    this.delay = this.error() || this.status()?.error ? Math.min(this.delay * 2, 300_000) : 30_000;
    this.nextAttempt = Date.now() + this.delay;
  }

  async setup(mode: 'enable' | 'join'): Promise<void> {
    await this.action(async () => {
      this.update(await this.api.sync(this.sidecarId(), mode, mode === 'enable'
        ? { enrollment_key: this.enrollmentKey() } : { code: this.joinCode() }));
      this.enrollmentKey.set('');
      this.joinCode.set('');
    });
    if (this.status()?.linked && !this.error()) await this.run();
  }

  async pair(): Promise<void> {
    await this.action(async () => {
      const code = await this.api.syncPair(this.sidecarId());
      if (this.showing) this.pairing.set(code);
    });
  }

  async copyPairing(): Promise<void> {
    await this.action(async () => {
      await navigator.clipboard.writeText(this.pairing());
      this.notice.set('Pairing code copied.');
    });
  }

  async revoke(): Promise<void> {
    if (!window.confirm('Revoke the other device’s access to all synced sidecars in this account?')) return;
    await this.action(async () => {
      this.update(await this.api.sync(this.sidecarId(), 'revoke'));
      this.pairing.set('');
      this.notice.set('Other device revoked.');
    });
  }

  async select(id: string): Promise<void> {
    const request = ++this.selection;
    this.error.set('');
    this.detail.set(null);
    this.fullVersions.set(false);
    this.mergeText.set(null);
    this.loading.set(true);
    try {
      const detail = await this.api.syncConflict(this.sidecarId(), id);
      if (request === this.selection) this.detail.set(detail);
    } catch (error) {
      if (request === this.selection) this.error.set(errorMessage(error));
    } finally {
      if (request === this.selection) this.loading.set(false);
    }
  }

  download(copy: 'local' | 'cloud', digest: string): string {
    return this.api.syncDownload(this.sidecarId(), this.detail()!.id, copy, digest);
  }

  async resolve(decision: SyncResolution['decision']): Promise<void> {
    const detail = this.detail();
    if (!detail || this.blocked()) return;
    await this.action(async () => {
      const status = await this.api.resolveSync(this.sidecarId(), detail.id, {
        decision, local_digest: detail.local?.digest ?? null, merged: this.mergeText(),
      });
      this.update(status);
      this.detail.set(null);
      this.mergeText.set(null);
      this.notice.set('Resolution saved on this device. Sync to share it.');
      this.nextAttempt = 0;
    });
  }

  private update(status: SyncStatus): void {
    if (this.destroy.destroyed) return;
    const previous = this.status();
    this.status.set(status);
    if (previous && previous.revision !== status.revision) this.changed.emit();
    const detail = this.detail();
    if (detail && !status.conflicts.some(item => item.id === detail.id)) {
      this.detail.set(null);
      this.mergeText.set(null);
    }
  }

  private async action(work: () => Promise<void>): Promise<void> {
    if (this.working() || this.destroy.destroyed) return;
    this.working.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      await work();
    } catch (error) {
      if (!this.destroy.destroyed) this.error.set(errorMessage(error));
    } finally {
      this.working.set(false);
    }
  }
}
