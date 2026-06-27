import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
    vi.restoreAllMocks();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the dashboard heading', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await flushInitialLoad(fixture, http);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('h1')?.textContent).toContain('Service dashboard');
  });

  it('should render and copy separate MCP configs', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    fixture.detectChanges();
    await flushInitialLoad(fixture, http);

    const claudeCodeConfig = {
      mcpServers: {
        'psydcar-docs': {
          type: 'stdio',
          command: '/tmp/bin/psydcar',
          args: ['mcp', '--sidecars', 'docs'],
          env: {},
        },
      },
    };
    const codexConfig =
      '[mcp_servers."psydcar-docs"]\ncommand = "/tmp/bin/psydcar"\nargs = ["mcp", "--sidecars", "docs"]';

    app.sidecars.set([
      {
        id: 'docs',
        name: 'Docs',
        root_path: '/tmp/docs',
        created_at: '2026-01-01T00:00:00+00:00',
        updated_at: '2026-01-01T00:00:00+00:00',
        indexing_status: 'indexed',
        last_refresh_at: null,
        indexed_file_count: 1,
        chunk_count: 1,
        error_count: 0,
        config: {
          max_file_size_bytes: 1000,
        },
      },
    ]);
    app.selectedId.set('docs');
    app.activeTab = 'mcp';
    app.mcpConfig.set({
      sidecar_id: 'docs',
      command: '/tmp/bin/psydcar',
      args: ['mcp', '--sidecars', 'docs'],
      claude_code_config: claudeCodeConfig,
      codex_config: codexConfig,
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Claude Code');
    expect(compiled.textContent).toContain('.mcp.json');
    expect(compiled.textContent).toContain('Codex');
    expect(compiled.textContent).toContain('config.toml');
    expect(compiled.textContent).toContain('"type": "stdio"');
    expect(compiled.textContent).toContain('[mcp_servers."psydcar-docs"]');

    await app.copyMcpConfig('claude-code');
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify(claudeCodeConfig, null, 2));

    await app.copyMcpConfig('codex');
    expect(writeText).toHaveBeenLastCalledWith(codexConfig);
  });

  it('should remove the selected sidecar after confirmation', async () => {
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await flushInitialLoad(fixture, http);

    app.sidecars.set([
      {
        id: 'docs',
        name: 'Docs',
        root_path: '/tmp/docs',
        created_at: '2026-01-01T00:00:00+00:00',
        updated_at: '2026-01-01T00:00:00+00:00',
        indexing_status: 'indexed',
        last_refresh_at: null,
        indexed_file_count: 1,
        chunk_count: 1,
        error_count: 0,
        config: {
          max_file_size_bytes: 1000,
        },
      },
    ]);
    app.selectedId.set('docs');
    app.files.set([{ relative_path: 'notes.md', extension: '.md', size_bytes: 12 }]);

    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const remove = app.removeSelectedSidecar();

    const deleteRequest = http.expectOne('/api/sidecars/docs');
    expect(deleteRequest.request.method).toBe('DELETE');
    deleteRequest.flush(null);

    const listRequest = await expectRequest(http, '/api/sidecars');
    expect(listRequest.request.method).toBe('GET');
    listRequest.flush([]);

    await remove;

    expect(app.sidecars()).toEqual([]);
    expect(app.selectedId()).toBeNull();
    expect(app.files()).toEqual([]);
  });
});

async function flushInitialLoad(fixture: ComponentFixture<App>, http: HttpTestingController) {
  const sidecarsRequest = await expectRequest(http, '/api/sidecars');
  expect(sidecarsRequest.request.method).toBe('GET');
  sidecarsRequest.flush([]);

  const directoryRequest = await expectRequest(http, '/api/directories');
  expect(directoryRequest.request.method).toBe('GET');
  directoryRequest.flush({
    path: '/tmp',
    parent_path: null,
    entries: [],
  });
  await fixture.whenStable();
}

async function expectRequest(http: HttpTestingController, url: string): Promise<TestRequest> {
  for (let attempt = 0; attempt < 10; attempt++) {
    const requests = http.match(url);
    if (requests.length === 1) {
      return requests[0];
    }
    if (requests.length > 1) {
      throw new Error(`Expected one request to ${url}, found ${requests.length}.`);
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  return http.expectOne(url);
}
