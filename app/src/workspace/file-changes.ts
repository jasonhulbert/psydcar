import { Component, computed, ElementRef, inject, input, output, signal, viewChild } from '@angular/core';

import { ButtonDirective } from '../design-system/button';
import { AppApi, errorMessage, type EntryChange } from '../app-api';
import { DialogComponent } from '../design-system/dialog';

export type FileAction = 'file' | 'folder' | 'move' | 'delete';

const titles: Record<FileAction, string> = {
  file: 'New file', folder: 'New folder', move: 'Rename or move', delete: 'Delete from disk',
};

@Component({
  selector: 'psydcar-file-changes',
  imports: [ButtonDirective, DialogComponent],
  template: `
    <psydcar-dialog #dialog [heading]="title()" size="form" [busy]="working()">
      <form (submit)="submit($event)">
        <div class="dialog-body">
          @if (action() === 'delete') {
            <p class="dialog-text">Permanently delete <strong>{{ source() }}</strong> from disk?</p>
          }
          <label class="field" [hidden]="action() === 'delete'">
            <span class="field-label">Path within this sidecar</span>
            <input #pathInput class="mono" name="path" [value]="path()" (input)="path.set(pathInput.value)"
              [required]="action() !== 'delete'" [disabled]="working()" maxlength="1024" autocomplete="off">
          </label>
          @if (action() === 'file') {
            <p class="field-hint">Include the file extension, for example notes.md.</p>
          }
          @if (error()) { <p class="flash flash-danger" role="alert">{{ error() }}</p> }
        </div>
        <footer class="dialog-footer">
          <button #cancelButton psButton="secondary" type="button" (click)="dialog.close()" [disabled]="working()">Cancel</button>
          <button [psButton]="action() === 'delete' ? 'danger' : 'primary'" type="submit" [disabled]="working()">
            {{ action() === 'delete' ? 'Delete' : action() === 'move' ? 'Move' : 'Create' }}
          </button>
        </footer>
      </form>
    </psydcar-dialog>
  `,
})
export class FileChangesComponent {
  readonly sidecarId = input.required<string>();
  readonly changed = output<{ action: FileAction; entry: EntryChange }>();
  readonly action = signal<FileAction>('file');
  readonly source = signal('');
  readonly path = signal('');
  readonly title = computed(() => titles[this.action()]);
  readonly error = signal('');
  readonly working = signal(false);

  private readonly api = inject(AppApi);
  private readonly dialog = viewChild.required(DialogComponent);
  private readonly pathInput = viewChild.required<ElementRef<HTMLInputElement>>('pathInput');
  private readonly cancelButton = viewChild.required<ElementRef<HTMLButtonElement>>('cancelButton');

  show(action: FileAction, source: string): void {
    this.action.set(action);
    this.source.set(source);
    this.path.set(source);
    this.error.set('');
    this.dialog().show();
    // Focus after Angular has switched between the path field and the confirmation.
    requestAnimationFrame(() => {
      if (action === 'delete') this.cancelButton().nativeElement.focus();
      else {
        const field = this.pathInput().nativeElement;
        field.focus();
        field.setSelectionRange(action === 'move' ? source.lastIndexOf('/') + 1 : source.length, source.length);
      }
    });
  }

  async submit(event: Event): Promise<void> {
    event.preventDefault();
    if (this.working()) return;
    const action = this.action();
    const id = this.sidecarId();
    const path = this.path();
    const source = this.source();
    if (action === 'move' && source === path) {
      this.dialog().close();
      return;
    }
    this.working.set(true);
    this.error.set('');
    try {
      let entry: EntryChange;
      switch (action) {
        case 'move': entry = await this.api.move(id, source, path); break;
        case 'delete': entry = await this.api.delete(id, source); break;
        default: entry = await this.api.create(id, { path, type: action });
      }
      this.dialog().close();
      this.changed.emit({ action, entry });
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.working.set(false);
    }
  }
}
