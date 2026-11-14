import { Directive, input } from '@angular/core';

export type ButtonVariant = 'secondary' | 'primary' | 'danger' | 'quiet' | 'ghost' | 'icon' | 'icon-danger' | 'navigation';

@Directive({
  selector: 'button[psButton], a[psButton]',
  host: {
    '[class.btn]': '!icon() && psButton() !== "navigation"',
    '[class.btn-primary]': 'psButton() === "primary"',
    '[class.btn-danger]': 'psButton() === "danger"',
    '[class.btn-danger-quiet]': 'psButton() === "quiet"',
    '[class.btn-ghost]': 'psButton() === "ghost"',
    '[class.btn-icon]': 'icon()',
    '[class.btn-icon-danger]': 'psButton() === "icon-danger"',
    '[class.btn-navigation]': 'psButton() === "navigation"',
    '[class.btn-sm]': 'buttonSize() === "small"',
  },
})
export class ButtonDirective {
  readonly psButton = input<ButtonVariant>('secondary');
  readonly buttonSize = input<'medium' | 'small'>('medium');
  protected icon(): boolean {
    return this.psButton() === 'icon' || this.psButton() === 'icon-danger';
  }
}
