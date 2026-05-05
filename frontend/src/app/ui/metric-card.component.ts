import { Component, computed, input } from '@angular/core';

import { cn } from '../utils/cn';

const METRIC_TONES = {
  neutral: 'border-slate-800 bg-slate-950',
  good: 'border-emerald-400/30 bg-emerald-400/10',
  warn: 'border-amber-400/30 bg-amber-400/10',
  danger: 'border-rose-400/30 bg-rose-400/10',
} as const;

@Component({
  selector: 'app-metric-card',
  standalone: true,
  host: {
    '[class]': 'hostClasses()',
  },
  template: `
    <span class="block text-xs font-bold uppercase text-slate-400">{{ label() }}</span>
    <strong class="mt-2 block break-words text-lg font-semibold text-slate-50">{{ value() }}</strong>
  `,
})
export class MetricCardComponent {
  label = input.required<string>();
  value = input.required<string | number>();
  tone = input<keyof typeof METRIC_TONES>('neutral');

  readonly hostClasses = computed(() =>
    cn('block min-w-0 rounded-lg border p-4 shadow-sm', METRIC_TONES[this.tone()]),
  );
}
