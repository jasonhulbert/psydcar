import { Component, computed, input } from '@angular/core';

@Component({
  selector: 'psydcar-diff',
  template: `
    <div class="diff-labels">
      <span class="diff-removed">− {{ beforeLabel() }}</span>
      <span class="diff-added">+ {{ afterLabel() }}</span>
    </div>
    <div class="diff-code" tabindex="0" role="region" [attr.aria-label]="label()">
      @for (line of lines(); track $index) {
        <span [class.diff-added]="line.startsWith('+')" [class.diff-removed]="line.startsWith('-')"
          [class.diff-hunk]="line.startsWith('@@')">{{ line }}</span>
      }
    </div>
  `,
  styles: `
    :host { display: block; min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-inset); overflow: hidden; }
    .diff-labels { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 6px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
    .diff-labels span { padding-inline: 6px; border-radius: var(--radius-sm); }
    .diff-code { max-height: 45dvh; overflow: auto; padding: 6px 0; tab-size: 4; line-height: 1.5; font-family: var(--font-mono); font-size: 12.5px; }
    .diff-code span { display: block; min-height: 1.5em; width: max-content; min-width: 100%; padding-inline: 12px; white-space: pre; }
    .diff-added { background: var(--success-bg); color: var(--success-fg); }
    .diff-removed { background: var(--danger-bg); color: var(--danger-fg); }
    .diff-hunk { background: var(--accent-subtle); color: var(--accent); }
  `,
})
export class DiffComponent {
  readonly diff = input.required<string>();
  readonly beforeLabel = input('Original');
  readonly afterLabel = input('Proposed');
  readonly label = input('Line-by-line changes');
  readonly lines = computed(() => this.diff().split('\n').slice(2, -1));
}
