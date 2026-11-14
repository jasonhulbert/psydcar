import { Component, ElementRef, inject, input, model } from '@angular/core';

import { IconComponent, type IconName } from './icon';

export interface Segment<T> {
  value: T;
  label: string;
  icon?: IconName;
  count?: number;
}

@Component({
  selector: 'psydcar-segmented-control',
  imports: [IconComponent],
  host: { '(keydown)': 'navigate($event)' },
  template: `
    <div class="segmented" role="group" [attr.aria-label]="label()">
      @for (option of options(); track option.value) {
        <button type="button" [attr.aria-pressed]="value() === option.value"
          [attr.tabindex]="value() === option.value ? 0 : -1" [disabled]="disabled()"
          [attr.aria-label]="option.label" (click)="value.set(option.value)">
          @if (option.icon) { <psydcar-icon [name]="option.icon" /> }
          <span>{{ option.label }}</span>
          @if (option.count !== undefined) { <span class="counter">{{ option.count }}</span> }
        </button>
      }
    </div>
  `,
  styles: `
    :host { display: inline-flex; min-width: 0; }
  `,
})
export class SegmentedControlComponent<T extends string | boolean> {
  readonly label = input.required<string>();
  readonly options = input.required<readonly Segment<T>[]>();
  readonly value = model.required<T>();
  readonly disabled = input(false);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  navigate(event: KeyboardEvent): void {
    if (event.altKey || event.ctrlKey || event.metaKey || this.disabled()) return;
    const buttons = [...this.host.nativeElement.querySelectorAll('button')];
    const current = buttons.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    let next: number;
    switch (event.key) {
      case 'ArrowRight': next = (current + 1) % buttons.length; break;
      case 'ArrowLeft': next = (current + buttons.length - 1) % buttons.length; break;
      case 'Home': next = 0; break;
      case 'End': next = buttons.length - 1; break;
      default: return;
    }
    event.preventDefault();
    this.value.set(this.options()[next].value);
    buttons[next].focus();
  }
}
