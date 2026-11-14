import { Component, input } from '@angular/core';

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

@Component({
  selector: 'psydcar-badge',
  template: `<span class="badge" [class]="'badge-' + tone()"><ng-content /></span>`,
})
export class BadgeComponent {
  readonly tone = input<BadgeTone>('neutral');
}
