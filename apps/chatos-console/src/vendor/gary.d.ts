// Ambient declarations for vendored Gary-UI plain-browser scripts.
declare module '*.js'

interface Window {
  GaryGradientWaves?: {
    mount(host: HTMLElement, options?: Record<string, unknown>): { destroy(): void; update(o: Record<string, unknown>): void }
  }
  GaryIcons?: { set(el: Element, name: string): void }
}
