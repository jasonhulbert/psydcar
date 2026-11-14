# Psydcar UI system

Psydcar uses a small Angular component library over native browser controls. The app and legacy dashboard share their visual tokens, fonts, icons, and editor presentation. Feature components own layout and application behavior. Shared controls own their appearance and interaction.

## Foundations

The source of truth is `src/psydcar/dashboard_assets/design-system.css`. Both interfaces load it directly. Geist and Geist Mono are bundled locally. Lucide icons come from the shared SVG sprite. Use `psydcar-icon` in Angular and extend its `IconName` union when adding a bundled icon.

| Foundation | Tokens and convention |
| --- | --- |
| Surfaces | `--canvas` for the page, `--surface` for content, `--surface-subtle` for navigation, `--surface-inset` for code and previews |
| Text | `--fg`, `--fg-muted`, `--fg-subtle`; use muted text for supporting information, never for the only indication of state |
| Accent | `--accent`, `--accent-subtle`, `--accent-border`; reserve violet for selection, links, focus, and primary actions |
| Feedback | Success, warning, and danger each have foreground, background, border, and dot tokens; include text or an icon alongside color |
| Spacing | `--space-1` to `--space-6`, `--space-8`, and `--space-10` use a 4 px scale; use 12–16 px inside controls and 20–24 px around sections |
| Type | `--text-xs`, `--text-sm`, `--text-control`, `--text-body`, `--text-lg`, `--text-title`; prose uses its own document styles |
| Controls | `--control-height`, `--control-height-sm`, `--icon-button-size`; compact controls belong in toolbars, normal controls in forms |
| Shape | `--radius-sm` for inline details, `--radius` for controls, `--radius-lg` for panels, `--radius-xl` for dialogs and library cards |
| Focus | Shared `:focus-visible` styles and `--focus-ring`; retain a visible focus indicator and respect reduced motion |

The dark palette follows the system preference. Do not add separate feature palettes or hard-coded colors. Keep editor CSS in `editor.css` so both interfaces display documents consistently.

## Controls

| Control | Use |
| --- | --- |
| `ButtonDirective` | Every action button or button-styled link. Set `psButton` to `secondary`, `primary`, `danger`, `quiet`, `ghost`, `icon`, `icon-danger`, or `navigation`. Use `buttonSize="small"` for compact actions. Native `disabled`, `type`, and accessibility attributes stay on the element. |
| `SegmentedControlComponent` | Mutually exclusive view switches. Supply a label, typed options, and a value. Arrow keys, Home, and End switch views. Only the selected option participates in Tab traversal. |
| `DialogComponent` | Modal forms and review surfaces. Native dialog provides focus containment, Escape, and focus return. Supply a heading and use the shared body and footer classes. Pass `busy` when Escape must not interrupt work. |
| `SearchFieldComponent` | Labeled search and filtering. The visible placeholder supplements the accessible label. |
| `BadgeComponent` | Short status labels with a semantic tone. Use `counter` for a count rather than a status. |
| `DiffComponent` | Text change review, shared by agent proposals and sync conflicts. |
| `EmptyStateComponent` | Empty lists, documents, and review queues. Provide a heading, explanation, and an action when useful. |
| Native form fields | Wrap inputs and textareas in `label.field`, with `field-label` and optional `field-hint`. Use `checkbox` for checkbox labels. Keep browser semantics and validation. |
| Native feedback regions | Use the shared `flash` and tone classes. Errors use `role="alert"`; progress and results use `role="status"`. |

File navigation and outline rows remain specialized components. Their selection and focus states use the same visual tokens. Do not use the action button directive for document content, tree entries, or segmented options.

```html
<button psButton="primary" type="submit" [disabled]="saving()">Save changes</button>
<button psButton="icon" type="button" aria-label="Refresh" title="Refresh">
  <psydcar-icon name="refresh-cw" />
</button>
<psydcar-segmented-control label="View" [options]="views" [(value)]="view" />
```

Import the controls used by each standalone component. Avoid copied button markup, feature-specific control sizes, global element overrides in feature stylesheets, and `::ng-deep`. Shared classes belong in the shared stylesheet; layout belongs beside its feature.

## Document interaction

Edit mode styles Markdown without replacing source text. Selection, focus, and composition must not change document geometry. Reading mode owns fully rendered tables, properties, images, callouts, and HTML. Source mode uses monospace text and line numbers. Mode changes preserve the document and undo history.

Keep ordinary editing keys in CodeMirror's standard keymaps. Prose uses Tab to move focus and code uses Tab to indent. Prose does not automatically insert closing brackets. Avoid per-key workarounds for layout errors and avoid synchronous whole-document work on selection changes.

## Verification

Build the app and editor after changing shared presentation. Check library, workspace, forms, proposals, sync, empty states, and file viewing in both system color schemes. Inspect narrow layouts at 390 px and the minimum supported width of 320 px. Use the keyboard to reach actions, switch segmented views, traverse dialogs, edit and select text, undo and redo, find, and save. Include wrapped paragraphs, lists, tables, front matter, and a large Markdown file when changing the editor.
