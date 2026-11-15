function () {
    if (!(this instanceof Element) || !this.isConnected) return null;
    const el = this;
    const r = el.getBoundingClientRect();
    const bounds = {x: r.x, y: r.y, width: r.width, height: r.height};
    let shown = r.width > 0 && r.height > 0;
    for (let p = el; p; p = p.parentElement || p.getRootNode().host) {
        const s = getComputedStyle(p);
        if (s.display === 'none' || s.visibility !== 'visible' || Number(s.opacity) === 0) shown = false;
    }
    const left = Math.max(0, r.left), top = Math.max(0, r.top);
    const right = Math.min(innerWidth, r.right), bottom = Math.min(innerHeight, r.bottom);
    let point = null;
    if (shown && left < right && top < bottom) {
        for (const [fx, fy] of [[.5,.5],[.15,.15],[.85,.15],[.15,.85],[.85,.85]]) {
            const x = left + (right-left)*fx, y = top + (bottom-top)*fy;
            let hit = document.elementFromPoint(x, y);
            while (hit?.shadowRoot) {
                const inner = hit.shadowRoot.elementFromPoint(x, y);
                if (!inner || inner === hit) break;
                hit = inner;
            }
            for (let p = hit; p; p = p.parentElement || p.getRootNode().host) {
                if (p === el) { point = {x,y}; break; }
            }
            if (point) break;
        }
    }
    return {
        tag: el.localName, bounds, visible: !!point, point,
        enabled: !el.matches(':disabled') && !el.closest('[inert], [aria-disabled="true"]'),
        editable: !el.readOnly && (el.isContentEditable || el.matches('textarea, input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]):not([type=file])')),
        name: el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('alt') || el.getAttribute('title') || '',
        value: el.type === 'password' ? '' : String(el.value ?? ''),
        type: el.type || ''
    };
}
