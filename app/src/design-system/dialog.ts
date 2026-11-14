import { Component, ElementRef, input, output, viewChild } from '@angular/core';

@Component({
  selector: 'psydcar-dialog',
  template: `
    <dialog #dialog class="dialog" [class.dialog-md]="size() === 'form'"
      [class.dialog-workspace]="size() === 'workspace'" [attr.aria-label]="heading()"
      (cancel)="onCancel($event)" (close)="closed.emit()">
      <header class="dialog-header">
        <h2>{{ heading() }}</h2>
        <ng-content select="[dialog-header-actions]" />
      </header>
      <ng-content />
    </dialog>
  `,
  styles: `
    .dialog-workspace {
      width: min(1080px, calc(100vw - 24px));
      height: min(760px, calc(100dvh - 24px));
      max-height: none;
    }
    .dialog-workspace[open] { display: flex; flex-direction: column; }
    .dialog-workspace .dialog-header { flex-shrink: 0; }
  `,
})
export class DialogComponent {
  readonly heading = input.required<string>();
  readonly size = input<'standard' | 'form' | 'workspace'>('standard');
  readonly busy = input(false);
  readonly closed = output<void>();

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  show(): void {
    this.dialog().nativeElement.showModal();
  }

  close(): void {
    this.dialog().nativeElement.close();
  }

  onCancel(event: Event): void {
    if (this.busy()) event.preventDefault();
  }
}
