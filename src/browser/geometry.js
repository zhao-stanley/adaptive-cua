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
    const texts = [], textRects = [];
    if (point && !el.matches('script, style, noscript, select, option')) {
        for (const child of el.childNodes) {
            if (child.nodeType !== Node.TEXT_NODE || !child.textContent.trim()) continue;
            const range = document.createRange();
            range.selectNodeContents(child);
            const rects = Array.from(range.getClientRects()).filter(t =>
                t.width > 0 && t.height > 0 && t.right > 0 && t.bottom > 0 && t.left < innerWidth && t.top < innerHeight);
            // Check text itself, not only a large enclosing container's hit point.
            const visibleRects = rects.filter(t => {
                const x = (Math.max(0,t.left) + Math.min(innerWidth,t.right))/2;
                const y = (Math.max(0,t.top) + Math.min(innerHeight,t.bottom))/2;
                let hit = document.elementFromPoint(x,y);
                while (hit?.shadowRoot) {
                    const inner = hit.shadowRoot.elementFromPoint(x,y);
                    if (!inner || inner === hit) break;
                    hit = inner;
                }
                return hit === el || el.contains(hit);
            });
            if (visibleRects.length) {
                texts.push(child.textContent.trim());
                textRects.push(...visibleRects.map(t => ({x:t.x, y:t.y, width:t.width, height:t.height})));
            }
        }
    }
    const style = getComputedStyle(el);
    const outline = Math.max(0, parseFloat(style.outlineWidth) || 0) + Math.max(0, parseFloat(style.outlineOffset) || 0);
    return {
        decoration_padding: Math.min(12, Math.max(2, outline + 2)),
        options: el instanceof HTMLSelectElement ? Array.from(el.options)
            .filter(o => !o.hidden && !o.parentElement.hidden && getComputedStyle(o).display !== 'none')
            .map(o => ({label: o.label, value: o.value, disabled: o.disabled || o.parentElement.disabled === true})) : [],
        tag: el.localName, bounds, visible: !!point, point,
        enabled: !el.matches(':disabled') && !el.closest('[inert], [aria-disabled="true"]'),
        editable: !el.readOnly && (el.isContentEditable || el.matches('textarea, input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]):not([type=file])')),
        name: el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('alt') || el.getAttribute('title') || '',
        value: el.type === 'password' ? '' : String(el.value ?? ''),
        type: el.type || '',
        text: texts.join(' ').replace(/\s+/g, ' '), text_rects: textRects
    };
}
