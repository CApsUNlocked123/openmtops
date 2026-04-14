/**
 * Widget contract — every widget module MUST export these two functions.
 *
 * mount(container, socket, instrument)
 *   container  HTMLElement  — the .widget-slot div to render into
 *   socket     Socket.IO    — shared socket instance from the scan page
 *   instrument string       — e.g. "NIFTY", "BANKNIFTY"
 *   Returns: void
 *
 * unmount()
 *   Must clear all setInterval timers and socket.off() all named handlers
 *   registered during mount(). Called before re-mounting with a new
 *   instrument or when the page is torn down.
 *   Returns: void
 *
 * Example skeleton:
 *
 *   let _interval = null;
 *   let _handler  = null;
 *
 *   export function mount(container, socket, instrument) {
 *     _render(container, instrument);
 *     _interval = setInterval(() => _render(container, instrument), 5000);
 *     _handler  = (tick) => { if (tick.instrument === instrument) _onTick(container, tick); };
 *     socket.on('tick', _handler);
 *   }
 *
 *   export function unmount() {
 *     clearInterval(_interval); _interval = null;
 *     if (_handler) { socket.off('tick', _handler); _handler = null; }
 *   }
 */
