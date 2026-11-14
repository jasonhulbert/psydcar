import { Component, computed, input, model, output, signal, viewChild } from '@angular/core';

import { ButtonDirective } from '../design-system/button';
import type { FileEntry } from '../app-api';
import { EmptyStateComponent } from '../design-system/empty-state';
import { IconComponent } from '../design-system/icon';
import { SearchFieldComponent } from '../design-system/search-field';
import { fileIcon } from './file-icon';

@Component({
  selector: 'psydcar-file-tree',
  imports: [ButtonDirective, EmptyStateComponent, IconComponent, SearchFieldComponent],
  styleUrl: './file-tree.css',
  template: `
    <div class="app-file-filter">
      <psydcar-search-field label="Filter files" placeholder="Filter files or #tags" [(value)]="filter" />
      <button psButton="icon" type="button" aria-label="Refresh file list" title="Refresh file list"
        (click)="refresh.emit()" [disabled]="busy()"><psydcar-icon name="refresh-cw" /></button>
    </div>
    @if (truncated()) {
      <p class="file-tree-note muted">This list shows the first 5,000 entries.</p>
    }
    <div class="file-tree-scroll">
      @if (rows().length) {
        <ul aria-label="Files and folders">
          @for (row of rows(); track row.entry.path) {
            <li class="app-file-row" [class.is-active]="selected() === row.entry.path"
              [class.is-folder]="row.folder" [style.padding-inline-start.px]="row.depth * 12">
              @if (row.folder && !filter().trim()) {
                <button type="button" psButton="icon" class="app-tree-toggle" (click)="toggle(row.entry.path)"
                  [attr.aria-expanded]="row.expanded"
                  [attr.aria-label]="(row.expanded ? 'Collapse ' : 'Expand ') + row.entry.path">
                  <psydcar-icon name="chevron-right" />
                </button>
              } @else {
                <span class="tree-indent" aria-hidden="true"></span>
              }
              <button type="button" class="app-tree-entry" (click)="select.emit(row.entry)" [disabled]="busy()"
                [attr.aria-current]="selected() === row.entry.path ? 'true' : null" [title]="row.entry.path">
                <psydcar-icon [name]="row.icon" /><span>{{ row.label }}</span>
              </button>
            </li>
          }
        </ul>
      } @else if (loading()) {
        <p class="file-tree-note muted" role="status">Loading files…</p>
      } @else if (filter().trim()) {
        <psydcar-empty-state icon="search" heading="No matching files" [compact]="true">
          Try another name or tag.
          <button empty-action type="button" psButton="secondary" buttonSize="small" (click)="clearFilter()">Clear filter</button>
        </psydcar-empty-state>
      } @else {
        <psydcar-empty-state icon="folder" heading="No files yet" [compact]="true">
          Create a file to start writing in this sidecar.
        </psydcar-empty-state>
      }
    </div>
  `,
})
export class FileTreeComponent {
  readonly entries = input.required<FileEntry[]>();
  readonly selected = input<string>();
  readonly busy = input(false);
  readonly loading = input(false);
  readonly truncated = input(false);
  readonly filter = model('');
  readonly select = output<FileEntry>();
  readonly refresh = output<void>();
  readonly collapsed = signal(new Set<string>());
  private readonly search = viewChild.required(SearchFieldComponent);

  readonly rows = computed(() => {
    const query = this.filter().trim().toLowerCase();
    const collapsed = this.collapsed();
    let hiddenPrefix = '';
    return this.entries().flatMap(entry => {
      if (query) {
        const matches = query.startsWith('#')
          ? entry.tags?.some(tag => tag.toLowerCase().includes(query.slice(1)))
          : entry.path.toLowerCase().includes(query);
        if (!matches) return [];
      } else {
        if (hiddenPrefix && entry.path.startsWith(hiddenPrefix)) return [];
        hiddenPrefix = collapsed.has(entry.path) ? entry.path + '/' : '';
      }
      const parts = entry.path.split('/');
      const folder = entry.type === 'folder';
      const expanded = folder && !query && !collapsed.has(entry.path);
      return [{ entry, label: query ? entry.path : parts.at(-1), depth: query ? 0 : Math.min(parts.length - 1, 12),
        folder, expanded, icon: fileIcon(entry, expanded) }];
    });
  });

  toggle(path: string): void {
    const collapsed = new Set(this.collapsed());
    if (!collapsed.delete(path)) collapsed.add(path);
    this.collapsed.set(collapsed);
  }

  reveal(path: string): void {
    const collapsed = new Set(this.collapsed());
    const parts = path.split('/');
    for (let length = 1; length < parts.length; length++) {
      collapsed.delete(parts.slice(0, length).join('/'));
    }
    this.collapsed.set(collapsed);
  }

  focusFilter(): void {
    this.search().focus();
  }

  clearFilter(): void {
    this.filter.set('');
    this.focusFilter();
  }
}
