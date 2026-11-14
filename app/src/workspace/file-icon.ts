import type { FileEntry } from '../app-api';
import type { IconName } from '../design-system/icon';

export function fileIcon(entry: FileEntry, expanded = false): IconName {
  if (entry.type === 'folder') return expanded ? 'folder-open' : 'folder';
  if (entry.image) return 'image';
  return /\.(md|markdown|txt|rst)$/i.test(entry.path) ? 'file-text' : 'file-code';
}
