import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ButtonDirective } from './design-system/button';
import { AppApi, errorMessage, type SidecarSummary } from './app-api';
import { BadgeComponent, type BadgeTone } from './design-system/badge';
import { EmptyStateComponent } from './design-system/empty-state';
import { IconComponent } from './design-system/icon';
import { SearchFieldComponent } from './design-system/search-field';

const indexStatus: Record<string, { label: string; tone: BadgeTone }> = {
  indexed: { label: 'Current', tone: 'success' },
  update_pending: { label: 'Updating', tone: 'info' },
  embedding_pending: { label: 'Embeddings pending', tone: 'info' },
  embedding_error: { label: 'Embeddings need attention', tone: 'warning' },
  index_error: { label: 'Index needs attention', tone: 'danger' },
  source_attention: { label: 'Source needs attention', tone: 'warning' },
};

const unindexed = { label: 'Awaiting index', tone: 'neutral' } as const;

@Component({
  imports: [ButtonDirective, BadgeComponent, EmptyStateComponent, IconComponent, RouterLink, SearchFieldComponent],
  templateUrl: './sidecars.html',
  styleUrl: './sidecars.css',
})
export class SidecarsComponent {
  readonly sidecars = signal<SidecarSummary[] | null>(null);
  readonly filter = signal('');
  readonly error = signal('');
  private readonly search = viewChild(SearchFieldComponent);
  readonly rows = computed(() => {
    const query = this.filter().trim().toLowerCase();
    return (this.sidecars() ?? [])
      .filter(sidecar => sidecar.name.toLowerCase().includes(query) || sidecar.source_path.toLowerCase().includes(query))
      .map(sidecar => ({ ...sidecar, index: indexStatus[sidecar.status] ?? unindexed }));
  });

  constructor() {
    inject(AppApi).sidecars()
      .then(sidecars => this.sidecars.set(sidecars))
      .catch(error => this.error.set(errorMessage(error)));
  }

  clearFilter(): void {
    this.filter.set('');
    this.search()?.focus();
  }
}
