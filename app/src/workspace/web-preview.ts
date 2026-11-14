import { Component, inject, input, signal, viewChild } from '@angular/core';
import { DomSanitizer, type SafeResourceUrl } from '@angular/platform-browser';

import { ButtonDirective } from '../design-system/button';
import { AppApi, errorMessage } from '../app-api';
import { DialogComponent } from '../design-system/dialog';
import { EmptyStateComponent } from '../design-system/empty-state';

const defaultEntries = ['index.html', 'dist/index.html', 'build/index.html'];

@Component({
  selector: 'psydcar-web-preview',
  imports: [ButtonDirective, DialogComponent, EmptyStateComponent],
  styles: `
    .app-preview-form { display: flex; align-items: end; flex-wrap: wrap; gap: 8px; padding: 14px 16px 4px; }
    .app-preview-form .field { flex: 1 0 160px; }
    .preview-hint { padding: 0 16px 12px; }
    .preview-error { margin: 0 16px 12px; }
    iframe { display: block; flex: 1; width: 100%; min-height: 0; border: 0; border-top: 1px solid var(--border); background: white; }
    psydcar-empty-state { display: grid; flex: 1; place-items: center; overflow: auto; }
  `,
  template: `
    <psydcar-dialog #dialog heading="Web preview" size="workspace" (closed)="reset()">
      <button dialog-header-actions psButton="secondary" buttonSize="small" type="button" (click)="dialog.close()">Close</button>
      <form class="app-preview-form" (submit)="render($event)">
        <label class="field">
          <span class="field-label">HTML entry point</span>
          <input #pathInput class="mono" name="entry" list="preview-entries" [value]="path()"
            (input)="path.set(pathInput.value)" required maxlength="1024" autocomplete="off">
          <datalist id="preview-entries">
            @for (entry of suggestions(); track entry) { <option [value]="entry"></option> }
          </datalist>
        </label>
        <button psButton="primary" type="submit" [disabled]="loading()">
          {{ loading() ? 'Opening…' : frame() ? 'Reload preview' : 'Open preview' }}
        </button>
      </form>
      <p class="field-hint preview-hint">
        Use a path relative to the sidecar. Preview scripts can read supported web assets in the entry point’s folder and its subfolders.
      </p>
      @if (error()) { <p class="flash flash-danger preview-error" role="alert">{{ error() }}</p> }
      @if (frame(); as url) {
        <iframe title="Sidecar web artifact" sandbox="allow-scripts" referrerpolicy="no-referrer" [src]="url"></iframe>
      } @else {
        <psydcar-empty-state icon="code" heading="Open a web artifact">
          For a built site, use relative asset URLs such as <code>./assets/main.js</code>.
        </psydcar-empty-state>
      }
    </psydcar-dialog>
  `,
})
export class WebPreviewComponent {
  readonly sidecarId = input.required<string>();
  readonly path = signal('');
  readonly suggestions = signal(defaultEntries);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly frame = signal<SafeResourceUrl | null>(null);

  private readonly api = inject(AppApi);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly dialog = viewChild.required(DialogComponent);
  private requestId = 0;

  show(initialPath: string, entries: string[]): void {
    this.reset();
    this.suggestions.set([...new Set([...entries, ...defaultEntries])]);
    this.path.set(initialPath || entries[0] || 'dist/index.html');
    this.dialog().show();
  }

  reset(): void {
    this.requestId++;
    this.loading.set(false);
    this.error.set('');
    this.frame.set(null);
  }

  async render(event: Event): Promise<void> {
    event.preventDefault();
    if (this.loading()) return;
    const requestId = ++this.requestId;
    this.loading.set(true);
    this.error.set('');
    this.frame.set(null);
    try {
      const { path } = await this.api.preview(this.sidecarId(), this.path());
      if (requestId !== this.requestId) return;
      const origin = new URL(window.location.origin);
      origin.hostname = 'localhost';
      const url = new URL(path, origin);
      if (!/^\/[\w-]{32}\/[^/]+$/.test(path) || url.origin !== origin.origin || url.pathname !== path) {
        throw new Error('Psydcar returned an invalid preview address.');
      }
      this.frame.set(this.sanitizer.bypassSecurityTrustResourceUrl(url.href));
    } catch (error) {
      if (requestId === this.requestId) this.error.set(errorMessage(error));
    } finally {
      if (requestId === this.requestId) this.loading.set(false);
    }
  }
}
