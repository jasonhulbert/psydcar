import {
  Component, CSP_NONCE, effect, ElementRef, inject, input, output, viewChild,
} from '@angular/core';

import type { TextFile } from '../app-api';

export type EditorMode = 'live' | 'reading' | 'source';
export type EditorStatus = { line: number; column: number; selected: number };
type LinkOptions = { wiki?: boolean };
export type Outline = { headings: { level: number; text: string; from: number }[]; current: number };

interface CodeMirrorEditor {
  getText(): string;
  snapshot(): unknown;
  isDirty(): boolean;
  markSaved(snapshot: unknown): void;
  setMode(mode: EditorMode): void;
  revealAnchor(anchor: string): void;
  revealPosition(position: number): void;
  destroy(): void;
}

interface EditorOptions {
  parent: HTMLElement;
  path: string;
  doc: string;
  mode: EditorMode;
  nonce?: string;
  onChange(): void;
  onSave(): void;
  onOpenLink(url: string, options?: LinkOptions): void;
  resolveImage(url: string, options?: LinkOptions): string | null;
  onTag(tag: string): void;
  onOutline(outline: Outline): void;
  onStatus(status: EditorStatus): void;
}

type EditorBundle = { create(options: EditorOptions): CodeMirrorEditor };
declare global {
  interface Window {
    PsydcarEditor?: EditorBundle;
  }
}

let loadingBundle: Promise<EditorBundle> | undefined;
function editorBundle(): Promise<EditorBundle> {
  if (loadingBundle) return loadingBundle;
  loadingBundle = new Promise<EditorBundle>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/assets/editor.js';
    script.onload = () => {
      if (window.PsydcarEditor) resolve(window.PsydcarEditor);
      else reject(new Error('The editor could not start.'));
    };
    script.onerror = () => reject(new Error('The editor could not load.'));
    document.head.append(script);
  }).catch(error => {
    loadingBundle = undefined;
    throw error;
  });
  return loadingBundle;
}

@Component({
  selector: 'psydcar-editor',
  host: { class: 'editor-surface' },
  template: '<div #surface class="app-editor-mount"></div>',
  styles: '.app-editor-mount { height: 100%; }',
})
export class EditorComponent {
  readonly file = input.required<TextFile>();
  readonly mode = input.required<EditorMode>();
  readonly anchor = input('');
  readonly image = input.required<(url: string, options?: LinkOptions) => string | null>();
  readonly changed = output<boolean>();
  readonly ready = output<boolean>();
  readonly failed = output<string>();
  readonly save = output<void>();
  readonly link = output<{ url: string; options?: LinkOptions }>();
  readonly tag = output<string>();
  readonly outline = output<Outline>();
  readonly status = output<EditorStatus>();

  private readonly surface = viewChild<ElementRef<HTMLDivElement>>('surface');
  private readonly nonce = inject(CSP_NONCE);
  private editor?: CodeMirrorEditor;

  constructor() {
    effect(onCleanup => {
      const parent = this.surface()?.nativeElement;
      const file = this.file();
      if (!parent) return;
      let canceled = false;
      this.ready.emit(false);
      editorBundle().then(bundle => {
        if (canceled) return;
        this.editor = bundle.create({
          parent, path: file.path, doc: file.content, mode: this.mode(),
          nonce: this.nonce ?? undefined,
          onChange: () => this.changed.emit(this.editor?.isDirty() ?? false),
          onSave: () => this.save.emit(),
          onOpenLink: (url, options) => this.link.emit({ url, options }),
          resolveImage: (url, options) => this.image()(url, options),
          onTag: tag => this.tag.emit(tag),
          onOutline: outline => this.outline.emit(outline),
          onStatus: status => this.status.emit(status),
        });
        const anchor = this.anchor();
        if (anchor) this.editor.revealAnchor(anchor);
        this.ready.emit(true);
      }).catch(error => {
        if (!canceled) this.failed.emit(error instanceof Error ? error.message : 'The editor could not load.');
      });
      onCleanup(() => {
        canceled = true;
        this.editor?.destroy();
        this.editor = undefined;
      });
    });
    effect(() => {
      const mode = this.mode();
      this.editor?.setMode(mode);
    });
  }

  snapshot(): { text: string; document: unknown } | undefined {
    return this.editor && { text: this.editor.getText(), document: this.editor.snapshot() };
  }

  markSaved(document: unknown): void {
    this.editor?.markSaved(document);
    this.changed.emit(this.editor?.isDirty() ?? false);
  }

  revealAnchor(anchor: string): void {
    this.editor?.revealAnchor(anchor);
  }

  revealPosition(position: number): void {
    this.editor?.revealPosition(position);
  }
}
