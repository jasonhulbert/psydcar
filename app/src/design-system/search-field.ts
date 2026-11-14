import { Component, ElementRef, input, model, viewChild } from '@angular/core';

import { IconComponent } from './icon';

@Component({
  selector: 'psydcar-search-field',
  imports: [IconComponent],
  template: `
    <label class="search-input">
      <psydcar-icon name="search" class="search-icon" />
      <span class="visually-hidden">{{ label() }}</span>
      <input #field type="search" [placeholder]="placeholder()" [value]="value()"
        (input)="value.set(field.value)" autocomplete="off" spellcheck="false">
    </label>
  `,
  styles: ':host { display: block; min-width: 0; } .search-input { min-width: 0; }',
})
export class SearchFieldComponent {
  readonly label = input.required<string>();
  readonly placeholder = input.required<string>();
  readonly value = model('');
  private readonly field = viewChild.required<ElementRef<HTMLInputElement>>('field');

  focus(): void {
    this.field().nativeElement.focus();
  }
}
