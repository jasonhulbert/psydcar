import { Component, input } from '@angular/core';

export type IconName = 'book-open' | 'chevron-right' | 'code' | 'eye' | 'file-code' | 'file-plus' | 'file-text'
  | 'folder' | 'folder-open' | 'folder-plus' | 'image' | 'layers' | 'panel-left-close' | 'panel-left-open'
  | 'plug' | 'pencil' | 'refresh-cw'
  | 'save' | 'search' | 'table-of-contents' | 'trash-2' | 'triangle-alert' | 'x';

@Component({
  selector: 'psydcar-icon',
  host: { 'aria-hidden': 'true' },
  template: `<svg class="icon" focusable="false"><use [attr.href]="'/assets/icons.svg#' + name()" /></svg>`,
  styles: `
    :host { display: inline-flex; flex: none; vertical-align: middle; }
    .icon { width: var(--icon-size, 16px); height: var(--icon-size, 16px); }
  `,
})
export class IconComponent {
  readonly name = input.required<IconName>();
}
