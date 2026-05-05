import { Component, input } from '@angular/core';

@Component({
  selector: 'app-section-header',
  standalone: true,
  host: {
    class: 'flex min-w-0 items-center justify-between gap-3',
  },
  template: `
    <div class="min-w-0">
      @if (eyebrow()) {
        <p class="mb-1 text-xs font-bold uppercase text-slate-400">{{ eyebrow() }}</p>
      }
      <h2 class="truncate text-base font-semibold text-slate-50">{{ title() }}</h2>
      @if (description()) {
        <p class="mt-1 break-words text-sm text-slate-400">{{ description() }}</p>
      }
    </div>
    @if (count() !== null) {
      <span class="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs font-semibold text-slate-300">
        {{ count() }}
      </span>
    }
    <ng-content />
  `,
})
export class SectionHeaderComponent {
  title = input.required<string>();
  eyebrow = input('');
  description = input('');
  count = input<number | string | null>(null);
}
