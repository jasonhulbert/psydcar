import { Component, computed, input } from '@angular/core';

import { cn } from '../utils/cn';

@Component({
  selector: 'app-status-badge',
  standalone: true,
  host: {
    '[class]': 'hostClasses()',
  },
  template: `{{ label() }}`,
})
export class StatusBadgeComponent {
  status = input('');

  readonly label = computed(() => this.status() || 'unknown');

  readonly hostClasses = computed(() =>
    cn(
      'inline-flex w-fit items-center rounded-full border px-2.5 py-1 text-xs font-bold capitalize',
      this.status() === 'indexing'
        ? 'border-amber-400/40 bg-amber-400/10 text-amber-200'
        : 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200',
    ),
  );
}
