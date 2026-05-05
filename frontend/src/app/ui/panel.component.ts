import { Component, computed, input } from '@angular/core';

import { cn } from '../utils/cn';

const PANEL_VARIANTS = {
  default: 'border border-slate-800 bg-slate-950 shadow-sm shadow-black/20',
  elevated: 'border border-slate-700 bg-slate-950 shadow-lg shadow-black/30',
  soft: 'border border-slate-800 bg-slate-900 shadow-sm shadow-black/20',
} as const;

@Component({
  selector: 'app-panel',
  standalone: true,
  host: {
    '[class]': 'hostClasses()',
  },
  template: `<ng-content />`,
})
export class PanelComponent {
  variant = input<keyof typeof PANEL_VARIANTS>('default');
  padded = input(true);

  readonly hostClasses = computed(() =>
    cn(
      'block min-w-0 rounded-lg',
      PANEL_VARIANTS[this.variant()],
      this.padded() && 'p-4 sm:p-5',
    ),
  );
}
