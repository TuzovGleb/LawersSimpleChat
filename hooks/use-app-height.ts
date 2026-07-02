import { useEffect } from "react";

/**
 * Mirrors the visual viewport into the `--app-h` CSS variable on <html> so a
 * full-height app shell can size itself as `h-[var(--app-h,100dvh)]`.
 *
 * Why: on iOS Safari the software keyboard shrinks only the *visual*
 * viewport — the layout viewport (and every vh/dvh unit) stays put, so a
 * bottom-pinned composer disappears behind the keyboard. Reclaiming layout
 * space requires JS; this is the app's single custom layout hook.
 *
 * The `+ offsetTop` term cooperates with Safari's native "scroll the focused
 * field into view" push instead of fighting it, and makes the formula a
 * no-op wherever the layout viewport already resizes with the keyboard
 * (Android with interactive-widget=resizes-content): there
 * vv.height === innerHeight and offsetTop === 0, i.e. --app-h ≡ 100dvh.
 *
 * Constraint: one mounted consumer at a time (the variable lives on
 * documentElement and is removed on unmount).
 */
export function useAppHeight() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return; // no VisualViewport API — the 100dvh fallback applies

    let frame = 0;
    const update = () => {
      frame = 0;
      // Pinch-zoom also shrinks vv.height; never resize the shell for it.
      if (Math.abs(vv.scale - 1) > 0.01) return;
      const h = Math.round(vv.height + Math.max(0, vv.offsetTop));
      document.documentElement.style.setProperty("--app-h", `${h}px`);
    };
    // Coalesce the event storm during keyboard/URL-bar animations.
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };

    update();
    vv.addEventListener("resize", schedule);
    vv.addEventListener("scroll", schedule); // offsetTop changes fire "scroll"
    return () => {
      cancelAnimationFrame(frame);
      vv.removeEventListener("resize", schedule);
      vv.removeEventListener("scroll", schedule);
      document.documentElement.style.removeProperty("--app-h");
    };
  }, []);
}
