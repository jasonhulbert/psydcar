import { Component } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter, RouterLink, RouterOutlet } from '@angular/router';

import { BadgeComponent } from './design-system/badge';
import { SidecarsComponent } from './sidecars';

@Component({
  selector: 'psydcar-app',
  imports: [BadgeComponent, RouterLink, RouterOutlet],
  template: `
    <a class="skip-link" href="#main-content">Skip to content</a>
    <header class="app-header">
      <div class="app-header-inner">
        <a class="brand" routerLink="/">
          <img class="brand-mark" src="/assets/brand.svg" alt="" width="24" height="24">
          <span class="brand-name">Psydcar</span>
        </a>
        <span class="header-divider" aria-hidden="true">/</span>
        <a class="header-context" routerLink="/">Sidecars</a>
        <div class="header-end"><psydcar-badge tone="info"><span class="header-local-full">Local workspace</span><span class="header-local-short">Local</span></psydcar-badge></div>
      </div>
    </header>
    <router-outlet />
  `,
})
class AppComponent {}

bootstrapApplication(AppComponent, {
  providers: [provideRouter([
    { path: '', component: SidecarsComponent, title: 'Sidecars · Psydcar' },
    {
      path: 'sidecars/:id',
      loadComponent: () => import('./workspace/workspace').then(module => module.WorkspaceComponent),
      canDeactivate: [(component: { canLeave(): boolean }) => component.canLeave()],
      title: 'Files · Psydcar',
    },
    { path: '**', redirectTo: '' },
  ])],
}).catch(error => console.error(error));
