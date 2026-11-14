import type { FileEntry } from '../app-api';

export function fileLink(url: string, source: string, files: FileEntry[], wiki = false) {
  const [name, anchor = ''] = url.split('#', 2);
  const folder = source.split('/').slice(0, -1);
  if (wiki && !name.startsWith('./') && !name.startsWith('../')) {
    const wanted = name.trim().replace(/^\/+/, '').toLowerCase();
    const candidates = [wanted, wanted + '.md'];
    const paths = [...candidates, ...candidates.map(path => [...folder, path].join('/').toLowerCase())];
    for (const path of paths) {
      const entry = files.find(file => file.path.toLowerCase() === path);
      if (entry) return { entry, anchor };
    }
    const entry = files.filter(file => candidates.some(path => file.path.toLowerCase().endsWith('/' + path)))
      .sort((a, b) => a.path.split('/').length - b.path.split('/').length || a.path.localeCompare(b.path))[0];
    return { entry, anchor };
  }

  let target = name.split('?', 1)[0].replace(/^<(.*)>$/, '$1');
  if (/^[a-z][a-z\d+.-]*:|^\/\//i.test(target)) return undefined;
  try { target = decodeURIComponent(target); } catch { return undefined; }
  const parts = target.startsWith('/') ? [] : folder;
  for (const part of target.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!parts.length) return undefined;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  const path = target ? parts.join('/') : source;
  const entry = files.find(file => file.path === path) ??
    files.find(file => file.path === path + '.md');
  return { entry, anchor };
}
