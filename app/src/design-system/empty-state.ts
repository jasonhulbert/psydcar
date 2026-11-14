import { Component, input } from '@angular/core';

import { IconComponent, type IconName } from './icon';

@Component({
  selector: 'psydcar-empty-state',
  imports: [IconComponent],
  template: `
    <div class="blankslate" [class.blankslate-compact]="compact()">
      <psydcar-icon [name]="icon()" class="empty-state-icon" />
      <h3>{{ heading() }}</h3>
      <p><ng-content /></p>
      <ng-content select="[empty-action]" />
    </div>
  `,
  styles: `
    :host { display: block; }
    .empty-state-icon {
      --icon-size: 21px;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      margin-bottom: 12px;
      border: 1px solid var(--accent-border);
      border-radius: var(--radius-xl);
      background: var(--accent-subtle);
      color: var(--accent);
    }
  `,
})
export class EmptyStateComponent {
  readonly icon = input.required<IconName>();
  readonly heading = input.required<string>();
  readonly compact = input(false);
}
