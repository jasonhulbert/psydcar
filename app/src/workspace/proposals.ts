import { DatePipe } from '@angular/common';
import { Component, computed, DestroyRef, inject, input, output, signal, viewChild } from '@angular/core';

import { ButtonDirective } from '../design-system/button';
import { ApiError, AppApi, errorMessage, type ProposalDetail, type ProposalReview, type ProposalSummary } from '../app-api';
import { SegmentedControlComponent } from '../design-system/segmented-control';
import { BadgeComponent } from '../design-system/badge';
import { DiffComponent } from '../design-system/diff';
import { DialogComponent } from '../design-system/dialog';
import { EmptyStateComponent } from '../design-system/empty-state';

@Component({
  selector: 'psydcar-proposals',
  imports: [ButtonDirective, SegmentedControlComponent, BadgeComponent, DatePipe, DialogComponent, DiffComponent, EmptyStateComponent],
  templateUrl: './proposals.html',
  styleUrl: './proposals.css',
})
export class ProposalsComponent {
  readonly sidecarId = input.required<string>();
  readonly sidecarName = input.required<string>();
  readonly draftPath = input<string>();
  readonly approved = output<string>();
  readonly pendingCount = signal(0);
  readonly statusOptions = computed(() => [{ value: false, label: 'Pending', count: this.pendingCount() }, { value: true, label: 'Reviewed' }]);
  readonly reviewed = signal(false);
  readonly items = signal<ProposalSummary[]>([]);
  readonly activeId = signal('');
  readonly detail = signal<ProposalDetail | null>(null);
  readonly loading = signal(false);
  readonly working = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly blocked = computed(() => this.detail()?.path === this.draftPath() && !!this.draftPath());

  private readonly api = inject(AppApi);
  private readonly destroy = inject(DestroyRef);
  private readonly dialog = viewChild.required(DialogComponent);
  private open = false;
  private requestId = 0;

  ngOnInit(): void {
    const refreshCount = async () => {
      if (this.open || document.hidden) return;
      const id = this.requestId;
      try {
        const queue = await this.api.proposals(this.sidecarId(), false);
        if (!this.destroy.destroyed && id === this.requestId) this.pendingCount.set(queue.pending_count);
      } catch {
        // Show a request error if the user opens the queue.
      }
    };
    void refreshCount();
    const timer = window.setInterval(refreshCount, 10000);
    this.destroy.onDestroy(() => window.clearInterval(timer));
  }

  show(): void {
    this.open = true;
    this.items.set([]);
    this.activeId.set('');
    this.notice.set('');
    this.dialog().show();
    void this.refresh(false);
  }

  close(): void {
    this.open = false;
    this.requestId++;
    this.loading.set(false);
  }

  async refresh(reviewed = this.reviewed()): Promise<void> {
    const requestId = ++this.requestId;
    if (reviewed !== this.reviewed()) {
      this.items.set([]);
      this.activeId.set('');
    }
    this.reviewed.set(reviewed);
    this.detail.set(null);
    this.error.set('');
    this.loading.set(true);
    try {
      const queue = await this.api.proposals(this.sidecarId(), reviewed);
      if (!this.current(requestId)) return;
      this.items.set(queue.items);
      this.pendingCount.set(queue.pending_count);
      const selected = queue.items.find(item => item.id === this.activeId()) ?? queue.items[0];
      if (selected) await this.select(selected.id);
    } catch (error) {
      if (this.current(requestId)) this.error.set(errorMessage(error));
    } finally {
      if (this.current(requestId)) this.loading.set(false);
    }
  }

  async select(id: string): Promise<void> {
    const requestId = ++this.requestId;
    this.activeId.set(id);
    this.detail.set(null);
    this.error.set('');
    this.loading.set(true);
    try {
      const detail = await this.api.proposal(this.sidecarId(), id);
      if (this.current(requestId)) this.detail.set(detail);
    } catch (error) {
      if (this.current(requestId)) this.error.set(errorMessage(error));
    } finally {
      if (this.current(requestId)) this.loading.set(false);
    }
  }

  async decide(decision: ProposalReview['decision']): Promise<void> {
    const detail = this.detail();
    if (!detail || detail.status !== 'pending' || this.working()) return;
    if (decision === 'approved' && (detail.stale || this.blocked())) return;
    this.working.set(true);
    this.error.set('');
    this.notice.set('');
    let result: ProposalSummary;
    try {
      result = await this.api.review(this.sidecarId(), detail.id, decision);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        this.detail.set({ ...detail, stale: true });
      } else {
        this.error.set(errorMessage(error));
      }
      return;
    } finally {
      this.working.set(false);
    }
    if (this.destroy.destroyed) return;
    if (result.status === 'approved') this.approved.emit(result.path);
    this.notice.set(result.status === 'approved' ? 'Edit approved and saved to disk.' : 'Proposal rejected.');
    void this.refresh();
  }

  private current(id: number): boolean {
    return !this.destroy.destroyed && this.open && this.requestId === id;
  }
}
