import { DatePipe } from '@angular/common';
import {
  afterNextRender, Component, computed, DestroyRef, ElementRef, HostListener, inject, Injector, signal, viewChild,
} from '@angular/core';
import { ActivatedRoute } from '@angular/router';

import { ButtonDirective } from '../design-system/button';
import { ApiError, AppApi, errorMessage, type EntryChange, type FileEntry, type TextFile } from '../app-api';
import { SegmentedControlComponent, type Segment } from '../design-system/segmented-control';
import { BadgeComponent } from '../design-system/badge';
import { EmptyStateComponent } from '../design-system/empty-state';
import { IconComponent } from '../design-system/icon';
import { EditorComponent, type EditorMode, type EditorStatus, type Outline } from './editor';
import { FileChangesComponent, type FileAction } from './file-changes';
import { fileIcon } from './file-icon';
import { FileTreeComponent } from './file-tree';
import { fileLink } from './links';
import { ProposalsComponent } from './proposals';
import { SyncComponent } from './sync';
import { WebPreviewComponent } from './web-preview';

const isHtml = (path: string) => /\.html?$/i.test(path);

@Component({
  imports: [ButtonDirective, BadgeComponent, DatePipe, EditorComponent, EmptyStateComponent, FileChangesComponent, FileTreeComponent,
    IconComponent, SegmentedControlComponent, ProposalsComponent, SyncComponent, WebPreviewComponent],
  templateUrl: './workspace.html',
  styleUrl: './workspace.css',
})
export class WorkspaceComponent {
  private readonly api = inject(AppApi);
  private readonly editor = viewChild(EditorComponent);
  private readonly tree = viewChild(FileTreeComponent);
  private readonly changes = viewChild(FileChangesComponent);
  private readonly preview = viewChild(WebPreviewComponent);
  private readonly proposals = viewChild(ProposalsComponent);
  private readonly sync = viewChild(SyncComponent);
  private readonly destroy = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly filesButton = viewChild<ElementRef<HTMLButtonElement>>('filesButton');
  private readonly outlineButton = viewChild<ElementRef<HTMLButtonElement>>('outlineButton');
  private readonly compactScreen = window.matchMedia('(max-width: 760px)');
  private readonly compact = signal(this.compactScreen.matches);
  private readonly browsingFiles = signal(false);
  private readonly filesCollapsed = signal(false);
  private version = '';
  private treeRequest = 0;

  readonly sidecarId = inject(ActivatedRoute).snapshot.paramMap.get('id')!;
  readonly sidecarName = signal('Files');
  readonly sidecarRoot = signal('');
  readonly entries = signal<FileEntry[]>([]);
  readonly file = signal<TextFile | null>(null);
  readonly selected = signal<FileEntry | null>(null);
  readonly entryIcon = computed(() => {
    const entry = this.selected();
    return entry ? fileIcon(entry) : 'folder';
  });
  readonly filePaneVisible = computed(() => !this.selected() || (this.compact() ? this.browsingFiles() : !this.filesCollapsed()));
  readonly editorPaneVisible = computed(() => !this.compact() || (!!this.selected() && !this.browsingFiles()));
  readonly image = computed(() => this.selected()?.viewable ? this.selected() : null);
  readonly dimensions = signal('');
  private readonly syncRevision = signal(0);
  readonly filter = signal('');
  readonly loading = signal(false);
  readonly treeLoading = signal(true);
  readonly updating = signal(false);
  readonly busy = computed(() => this.loading() || this.saving() || this.updating());
  readonly truncated = signal(false);
  readonly dirty = signal(false);
  readonly ready = signal(false);
  readonly saving = signal(false);
  readonly conflict = signal(false);
  readonly mode = signal<EditorMode>('live');
  readonly editorStatus = signal<EditorStatus | null>(null);
  readonly anchor = signal('');
  readonly outline = signal<Outline | null>(null);
  readonly showOutline = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly modes: Segment<EditorMode>[] = [
    { value: 'live', label: 'Edit', icon: 'pencil' },
    { value: 'reading', label: 'Reading', icon: 'book-open' },
    { value: 'source', label: 'Source', icon: 'code' },
  ];

  private readonly openable = computed(() => this.entries().filter(entry => entry.editable || entry.viewable));
  readonly markdown = computed(() => /\.(md|markdown)$/i.test(this.file()?.path ?? ''));
  readonly html = computed(() => {
    const entry = this.selected();
    return entry?.type === 'file' && isHtml(entry.path);
  });
  readonly size = computed(() => {
    const size = this.selected()?.size;
    if (size == null) return '';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
  });

  readonly resolveImage = (url: string, options?: { wiki?: boolean }) => {
    const file = this.file();
    if (!file) return null;
    const target = fileLink(url, file.path, this.openable(), options?.wiki)?.entry;
    return target?.viewable ? this.imageUrl(target.path) : null;
  };

  constructor() {
    const resize = (event: MediaQueryListEvent) => {
      this.compact.set(event.matches);
      this.browsingFiles.set(false);
    };
    this.compactScreen.addEventListener('change', resize);
    this.destroy.onDestroy(() => this.compactScreen.removeEventListener('change', resize));
    this.api.sidecars()
      .then(sidecars => {
        const sidecar = sidecars.find(item => item.id === this.sidecarId);
        if (sidecar) {
          this.sidecarName.set(sidecar.name);
          this.sidecarRoot.set(sidecar.source_path);
        }
      })
      .catch(error => this.error.set(errorMessage(error)));
    void this.refreshFiles();
  }

  async refreshFiles(): Promise<void> {
    const request = ++this.treeRequest;
    this.treeLoading.set(true);
    try {
      const tree = await this.api.files(this.sidecarId);
      if (this.destroy.destroyed || request !== this.treeRequest) return;
      this.entries.set(tree.entries);
      this.truncated.set(tree.truncated);
    } catch (error) {
      if (request === this.treeRequest) this.error.set(errorMessage(error));
    } finally {
      if (request === this.treeRequest) this.treeLoading.set(false);
    }
  }

  canLeave(): boolean {
    return !this.saving() && !this.updating() && !this.changes()?.working() && !this.proposals()?.working()
      && (!this.hasDraft() || window.confirm('Discard your unsaved edits?'));
  }

  private hasDraft(): boolean {
    const sync = this.sync();
    return this.dirty() || !!sync && sync.mergeText() !== null;
  }

  @HostListener('window:beforeunload', ['$event'])
  protectDraft(event: BeforeUnloadEvent): void {
    if (this.hasDraft() || this.saving() || this.updating() || this.changes()?.working() || this.proposals()?.working()) {
      event.preventDefault();
    }
  }

  async open(entry: FileEntry, anchor = '', reload = false): Promise<void> {
    if (this.busy()) return;
    if (!reload && this.selected()?.path === entry.path) {
      if (anchor) this.editor()?.revealAnchor(anchor);
      this.showDocument();
      return;
    }
    if (!this.canLeave()) return;
    const focusDocument = this.compact() && this.filePaneVisible();
    const draft = this.editor()?.snapshot()?.document;
    this.loading.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      const file = entry.editable ? await this.api.open(this.sidecarId, entry.path) : null;
      if (this.destroy.destroyed) return;
      if (draft !== this.editor()?.snapshot()?.document && !this.canLeave()) return;
      this.clearDocument();
      this.anchor.set(anchor);
      this.version = file?.version ?? '';
      this.file.set(file);
      this.selected.set(file ? { ...entry, size: file.size, mtime_ns: file.mtime_ns } : entry);
      this.tree()?.reveal(entry.path);
      this.showDocument(focusDocument);
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.loading.set(false);
    }
  }

  private clearDocument(): void {
    this.selected.set(null);
    this.file.set(null);
    this.dimensions.set('');
    this.outline.set(null);
    this.editorStatus.set(null);
    this.ready.set(false);
    this.dirty.set(false);
    this.conflict.set(false);
  }

  toggleFiles(fromSidebar = false): void {
    if (!this.compact()) {
      const reveal = this.filesCollapsed();
      this.filesCollapsed.set(!reveal);
      if (reveal) this.focusFiles();
      else if (fromSidebar) this.focusDocument();
      return;
    }
    if (this.browsingFiles()) {
      this.showDocument();
    } else {
      this.browsingFiles.set(true);
      this.focusFiles();
    }
  }

  private showDocument(focus = this.browsingFiles()): void {
    if (!this.compact()) return;
    this.browsingFiles.set(false);
    if (focus) this.focusDocument();
  }

  private focusDocument(): void {
    afterNextRender(() => this.filesButton()?.nativeElement.focus(), { injector: this.injector });
  }

  private focusFiles(): void {
    afterNextRender(() => this.tree()?.focusFilter(), { injector: this.injector });
  }

  fileAction(action: FileAction): void {
    if (this.busy()) return;
    const selected = this.selected();
    if (action === 'move' || action === 'delete') {
      if (selected && !this.dirty()) this.changes()?.show(action, selected.path);
    } else {
      const path = selected?.path ?? '';
      const parent = selected?.type === 'folder' ? path + '/' : path.slice(0, path.lastIndexOf('/') + 1);
      this.changes()?.show(action, parent);
    }
  }

  async entryChanged(action: FileAction, entry: EntryChange): Promise<void> {
    const previous = this.selected();
    this.error.set('');
    if (action === 'delete' || action === 'move') this.clearDocument();
    this.updating.set(true);
    try {
      await this.refreshFiles();
    } finally {
      this.updating.set(false);
    }
    if (this.destroy.destroyed) return;
    if (action !== 'delete' && !this.dirty()) {
      this.filter.set('');
      const current = this.entries().find(item => item.path === entry.path);
      const fallback = action === 'move' ? { ...previous, ...entry } : { ...entry, editable: entry.type === 'file' };
      await this.open(current ?? fallback);
    }
    const verb = action === 'delete' ? 'Deleted' : action === 'move' ? 'Moved to' : 'Created';
    this.notice.set(`${verb} ${entry.path}.`);
  }

  openPreview(): void {
    const entries = this.entries().filter(entry => entry.type === 'file' && isHtml(entry.path)).map(entry => entry.path);
    const active = this.html() ? this.selected()?.path : undefined;
    this.preview()?.show(active ?? '', entries);
  }

  async proposalApproved(path: string): Promise<void> {
    const entry = this.selected();
    if (entry?.path === path && !this.dirty()) await this.open(entry, '', true);
    void this.refreshFiles();
  }

  async filesSynced(): Promise<void> {
    this.syncRevision.update(revision => revision + 1);
    const selected = this.selected();
    await this.refreshFiles();
    if (!selected || this.destroy.destroyed || this.selected() !== selected || this.busy() || this.dirty()) return;
    const current = this.entries().find(item => item.path === selected.path);
    if (!current) {
      this.clearDocument();
      this.notice.set('The open file was removed by sync.');
      return;
    }
    if (!current.editable || current.mtime_ns !== selected.mtime_ns || current.size !== selected.size) {
      await this.open(current, '', true);
    }
  }

  revealHeading(position: number): void {
    this.editor()?.revealPosition(position);
  }

  closeOutline(): void {
    this.showOutline.set(false);
    this.outlineButton()?.nativeElement.focus();
  }

  async save(): Promise<void> {
    const file = this.file();
    const editor = this.editor();
    const snapshot = editor?.snapshot();
    if (!file || !editor || !snapshot || !this.dirty() || this.busy() || this.conflict()) return;
    this.saving.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      const saved = await this.api.save(this.sidecarId, file.path, {
        version: this.version,
        content: snapshot.text,
        newline: file.newline,
        bom: file.bom,
      });
      this.version = saved.version;
      this.selected.update(entry => entry && { ...entry, size: saved.size, mtime_ns: saved.mtime_ns });
      editor.markSaved(snapshot.document);
      this.notice.set('Saved to disk.');
      void this.refreshFiles();
    } catch (error) {
      this.error.set(errorMessage(error));
      this.conflict.set(error instanceof ApiError && error.status === 409);
    } finally {
      this.saving.set(false);
    }
  }

  reload(): void {
    const entry = this.selected();
    if (entry?.editable) void this.open(entry, '', true);
  }

  follow(url: string, wiki = false): void {
    const source = this.file()?.path;
    if (!source) return;
    const target = fileLink(url, source, this.openable(), wiki);
    if (target?.entry) void this.open(target.entry, target.anchor);
    else this.error.set('That link does not point to an openable file in this sidecar.');
  }

  imageUrl(path: string): string {
    return this.api.imageUrl(this.sidecarId, path) + '&sync=' + this.syncRevision();
  }
}
