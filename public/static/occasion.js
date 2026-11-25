/* Festival theme: slide-down greeting + a light canvas effect. Loaded only while an occasion is on.
   Respects "reduce motion", pauses in background tabs, and stays out of the way (pointer-events: none).
   Closing the greeting hides the effect too and is remembered for that festival (this year). */
(() => {
  'use strict';
  const dataEl = document.getElementById('occasionData');
  const banner = document.getElementById('occasionBanner');
  const canvas = document.getElementById('occasionFx');
  if (!dataEl || !banner || !canvas) return;
  let data = {};
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
  const KEY = 'pf-occ-hide:' + data.id;
  let dismissed = false;
  try { dismissed = !data.preview && localStorage.getItem(KEY) === '1'; } catch (e) { dismissed = false; }

  const ctx = canvas.getContext && canvas.getContext('2d');
  let raf = 0;
  let running = false;
  function stop() { running = false; cancelAnimationFrame(raf); }

  if (dismissed) {
    banner.remove();
    canvas.remove();
    document.body.classList.add('occ-dismissed');
    return;
  }
  // Slide the greeting down a moment after the page settles.
  setTimeout(() => banner.classList.add('is-in'), 450);
  document.getElementById('occasionClose').addEventListener('click', () => {
    banner.classList.remove('is-in');
    banner.classList.add('is-out');
    try { if (!data.preview) localStorage.setItem(KEY, '1'); } catch (e) { /* storage off: just hide */ }
    stop();
    canvas.classList.add('is-off');
    document.body.classList.add('occ-dismissed');
    setTimeout(() => { banner.remove(); canvas.remove(); }, 700);
  });

  if (!ctx || data.effect === 'none') { canvas.remove(); return; }

  /* ------------------------------------------------------------ canvas */
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const palette = (Array.isArray(data.palette) && data.palette.length ? data.palette : ['#ffffff'])
    .filter((c) => /^#[0-9a-f]{6}$/i.test(c));
  let W = 0; let H = 0; let dpr = 1;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[(Math.random() * list.length) | 0];
  const TAU = Math.PI * 2;
  const light = () => document.documentElement.getAttribute('data-mdb-theme') === 'light';
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  const density = () => Math.max(18, Math.min(90, Math.round((W * H) / 20000))) * (W < 700 ? 0.7 : 1);
  function rgba(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function glow(x, y, r, color, a) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(color, a)); g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
  }

  /* ------------------------------------------------------------ effects */
  const effects = {
    snow: {
      make: (top) => ({ x: rand(0, W), y: top ? rand(-H, 0) : rand(0, H), r: rand(1, 3.6), vy: rand(0.35, 1.2), a: rand(0, TAU), s: rand(0.004, 0.02), o: rand(0.45, 0.95) }),
      step(p) { p.y += p.vy; p.a += p.s; p.x += Math.sin(p.a) * 0.6; if (p.y > H + 6) Object.assign(p, this.make(true), { y: -6 }); },
      draw(p) { ctx.fillStyle = light() ? `rgba(120,146,190,${p.o * 0.75})` : `rgba(255,255,255,${p.o})`; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, TAU); ctx.fill(); },
    },
    confetti: {
      make: (top) => ({ x: rand(0, W), y: top ? rand(-H, -10) : rand(0, H), w: rand(6, 11), h: rand(3, 6), c: pick(palette), r: rand(0, TAU), vr: rand(-0.12, 0.12), vy: rand(0.9, 2.2), vx: rand(-0.5, 0.5), f: rand(0, TAU) }),
      step(p) { p.y += p.vy; p.f += 0.08; p.x += p.vx + Math.sin(p.f) * 0.7; p.r += p.vr; if (p.y > H + 12) Object.assign(p, this.make(true), { y: -12 }); },
      draw(p) { ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.scale(1, Math.cos(p.f)); ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore(); },
    },
    petals: {
      count: 0.6,
      make: (top) => ({ x: rand(0, W), y: top ? rand(-H, -10) : rand(0, H), s: rand(5, 10), c: pick(palette), r: rand(0, TAU), vr: rand(-0.03, 0.03), vy: rand(0.5, 1.3), f: rand(0, TAU), o: rand(0.6, 0.95) }),
      step(p) { p.y += p.vy; p.f += 0.02; p.x += Math.sin(p.f) * 0.9; p.r += p.vr; if (p.y > H + 14) Object.assign(p, this.make(true), { y: -14 }); },
      draw(p) {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.globalAlpha = p.o; ctx.fillStyle = p.c;
        ctx.beginPath(); ctx.ellipse(0, 0, p.s, p.s * 0.55, 0, 0, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.beginPath(); ctx.ellipse(-p.s * 0.25, -p.s * 0.1, p.s * 0.35, p.s * 0.18, 0, 0, TAU); ctx.fill();
        ctx.restore();
      },
    },
    stars: {
      count: 0.9,
      make: () => ({ x: rand(0, W), y: rand(0, H * 0.75), r: rand(0.6, 2), t: rand(0, TAU), s: rand(0.01, 0.05), c: pick(palette) }),
      step(p) { p.t += p.s; },
      draw(p) { const a = 0.25 + 0.75 * Math.abs(Math.sin(p.t)); glow(p.x, p.y, p.r * 5, p.c, a * 0.35); ctx.fillStyle = rgba(p.c, a); ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, TAU); ctx.fill(); },
      extra() { moon(W - Math.min(140, W * 0.16), 110, 34, true); shooting(); },
    },
    lanterns: {
      count: 0.28,
      make: (top) => ({ x: rand(0, W), y: top ? H + rand(20, 200) : rand(0, H), s: rand(12, 22), vy: rand(0.25, 0.7), f: rand(0, TAU), c: pick(palette) }),
      step(p) { p.y -= p.vy; p.f += 0.015; p.x += Math.sin(p.f) * 0.35; if (p.y < -60) Object.assign(p, this.make(true)); },
      draw(p) {
        const fade = Math.min(1, p.y / (H * 0.35));
        glow(p.x, p.y, p.s * 3, p.c, 0.28 * fade);
        ctx.save(); ctx.globalAlpha = 0.9 * fade;
        const g = ctx.createLinearGradient(p.x, p.y - p.s, p.x, p.y + p.s);
        g.addColorStop(0, '#fff3c4'); g.addColorStop(1, p.c);
        ctx.fillStyle = g; ctx.beginPath();
        ctx.moveTo(p.x - p.s * 0.55, p.y - p.s * 0.8); ctx.lineTo(p.x + p.s * 0.55, p.y - p.s * 0.8);
        ctx.quadraticCurveTo(p.x + p.s * 0.85, p.y, p.x + p.s * 0.45, p.y + p.s * 0.85);
        ctx.lineTo(p.x - p.s * 0.45, p.y + p.s * 0.85); ctx.quadraticCurveTo(p.x - p.s * 0.85, p.y, p.x - p.s * 0.55, p.y - p.s * 0.8);
        ctx.fill(); ctx.restore();
      },
    },
    diyas: {
      count: 0.7,
      make: (top) => ({ x: rand(0, W), y: top ? H + rand(0, 60) : rand(H * 0.3, H), r: rand(1, 2.6), vy: rand(0.4, 1.3), f: rand(0, TAU), life: rand(0.5, 1), c: pick(palette) }),
      step(p) { p.y -= p.vy; p.f += 0.05; p.x += Math.sin(p.f) * 0.5; p.life -= 0.0025; if (p.y < H * 0.15 || p.life <= 0) Object.assign(p, this.make(true)); },
      draw(p) { glow(p.x, p.y, p.r * 6, p.c, 0.45 * p.life); ctx.fillStyle = rgba('#fff4d6', p.life); ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 0.6, 0, TAU); ctx.fill(); },
      extra(t) { diyaRow(t); },
    },
    colors: {
      count: 0,
      bursts: [],
      extra(t) {
        if (!this.next || t > this.next) { this.next = t + rand(500, 1100); this.burst(); }
        this.bursts = this.bursts.filter((p) => p.a > 0.02);
        for (const p of this.bursts) {
          p.x += p.vx; p.y += p.vy; p.vx *= 0.96; p.vy = p.vy * 0.96 + 0.02; p.r += 0.35; p.a *= 0.975;
          ctx.fillStyle = rgba(p.c, p.a * 0.55); ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, TAU); ctx.fill();
        }
      },
      burst() {
        const x = rand(W * 0.08, W * 0.92); const y = rand(H * 0.12, H * 0.8); const c = pick(palette);
        const n = W < 700 ? 22 : 36;
        for (let i = 0; i < n; i++) {
          const a = rand(0, TAU); const v = rand(1, 5.5);
          this.bursts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: rand(3, 9), a: rand(0.5, 0.9), c: Math.random() < 0.8 ? c : pick(palette) });
        }
      },
    },
    fireworks: {
      count: 0,
      rockets: [],
      sparks: [],
      extra(t) {
        if (!this.next || t > this.next) { this.next = t + rand(700, 1500); this.rockets.push({ x: rand(W * 0.15, W * 0.85), y: H + 10, vy: -rand(7, 10.5), c: pick(palette), top: rand(H * 0.12, H * 0.45) }); }
        this.rockets = this.rockets.filter((r) => {
          r.y += r.vy; r.vy += 0.12;
          glow(r.x, r.y, 6, r.c, 0.8);
          if (r.y <= r.top || r.vy >= 0) {
            const n = W < 700 ? 34 : 56;
            for (let i = 0; i < n; i++) { const a = (i / n) * TAU; const v = rand(1.5, 4.2); this.sparks.push({ x: r.x, y: r.y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, a: 1, c: Math.random() < 0.85 ? r.c : pick(palette) }); }
            return false;
          }
          return true;
        });
        this.sparks = this.sparks.filter((p) => p.a > 0.03);
        for (const p of this.sparks) {
          const px = p.x; const py = p.y;
          p.x += p.vx; p.y += p.vy; p.vx *= 0.985; p.vy = p.vy * 0.985 + 0.045; p.a *= 0.975;
          ctx.strokeStyle = rgba(p.c, p.a); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(p.x, p.y); ctx.stroke();
        }
      },
    },
    kites: {
      count: 0.09,
      make: (top) => ({ x: top ? -60 : rand(0, W), y: rand(H * 0.08, H * 0.55), s: rand(16, 26), vx: rand(0.4, 1), f: rand(0, TAU), c: pick(palette), c2: pick(palette) }),
      step(p) { p.x += p.vx; p.f += 0.02; p.y += Math.sin(p.f) * 0.5; if (p.x > W + 80) Object.assign(p, this.make(true)); },
      draw(p) {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(Math.sin(p.f) * 0.25);
        ctx.fillStyle = p.c; ctx.beginPath(); ctx.moveTo(0, -p.s); ctx.lineTo(p.s * 0.75, 0); ctx.lineTo(0, p.s * 1.1); ctx.lineTo(-p.s * 0.75, 0); ctx.closePath(); ctx.fill();
        ctx.fillStyle = p.c2; ctx.beginPath(); ctx.moveTo(0, -p.s); ctx.lineTo(p.s * 0.75, 0); ctx.lineTo(0, 0); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, p.s * 1.1);
        for (let i = 1; i <= 6; i++) ctx.lineTo(Math.sin(p.f * 3 + i) * 5, p.s * 1.1 + i * 8);
        ctx.stroke(); ctx.restore();
      },
    },
    bats: {
      count: 0.12,
      make: (top) => ({ x: top ? -40 : rand(0, W), y: rand(H * 0.06, H * 0.6), s: rand(9, 16), vx: rand(0.8, 2), f: rand(0, TAU), w: rand(0.15, 0.3) }),
      step(p) { p.x += p.vx; p.f += p.w; p.y += Math.sin(p.f * 0.3) * 0.6; if (p.x > W + 50) Object.assign(p, this.make(true)); },
      draw(p) {
        const flap = Math.sin(p.f) * p.s * 0.6;
        ctx.fillStyle = 'rgba(20,12,28,.85)'; ctx.beginPath();
        ctx.moveTo(p.x, p.y); ctx.quadraticCurveTo(p.x - p.s * 0.6, p.y - flap, p.x - p.s * 1.4, p.y - flap * 0.4);
        ctx.quadraticCurveTo(p.x - p.s * 0.8, p.y + p.s * 0.1, p.x, p.y + p.s * 0.25);
        ctx.quadraticCurveTo(p.x + p.s * 0.8, p.y + p.s * 0.1, p.x + p.s * 1.4, p.y - flap * 0.4);
        ctx.quadraticCurveTo(p.x + p.s * 0.6, p.y - flap, p.x, p.y); ctx.fill();
      },
      extra() { moon(Math.min(140, W * 0.16), 110, 40, false); },
    },
  };

  function moon(x, y, r, crescent) {
    glow(x, y, r * 3.2, '#fff2b3', 0.22);
    ctx.fillStyle = '#fff6cf'; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
    if (crescent) {
      ctx.save(); ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath(); ctx.arc(x + r * 0.45, y - r * 0.2, r * 0.92, 0, TAU); ctx.fill(); ctx.restore();
    }
  }
  let shoot = null;
  function shooting() {
    if (!shoot && Math.random() < 0.004) shoot = { x: rand(W * 0.2, W), y: rand(0, H * 0.3), a: 1 };
    if (!shoot) return;
    shoot.x -= 9; shoot.y += 4; shoot.a -= 0.02;
    ctx.strokeStyle = `rgba(255,255,255,${Math.max(shoot.a, 0)})`; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(shoot.x + 60, shoot.y - 26); ctx.stroke();
    if (shoot.a <= 0) shoot = null;
  }
  function diyaRow(t) {
    const n = Math.max(3, Math.min(9, Math.round(W / 170)));
    for (let i = 0; i < n; i++) {
      const x = ((i + 0.5) / n) * W; const y = H - 16;
      const flick = 1 + Math.sin(t / 90 + i * 1.7) * 0.12;
      glow(x, y - 16, 34 * flick, '#ffb703', 0.35);
      ctx.fillStyle = '#c2410c'; ctx.beginPath(); ctx.ellipse(x, y, 15, 7, 0, 0, Math.PI); ctx.fill();
      ctx.fillStyle = '#ea580c'; ctx.beginPath(); ctx.ellipse(x, y, 15, 3, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#ffd166'; ctx.beginPath();
      ctx.moveTo(x, y - 20 * flick); ctx.quadraticCurveTo(x + 5, y - 7, x, y - 3); ctx.quadraticCurveTo(x - 5, y - 7, x, y - 20 * flick); ctx.fill();
    }
  }

  const fx = effects[data.effect] || effects.confetti;
  const count = Math.round(density() * (fx.count == null ? 1 : fx.count));
  const parts = fx.make ? Array.from({ length: count }, () => fx.make(false)) : [];

  function frame(t) {
    if (!running) return;
    ctx.clearRect(0, 0, W, H);
    for (const p of parts) { fx.step(p); fx.draw(p); }
    if (fx.extra) fx.extra(t || 0);
    raf = requestAnimationFrame(frame);
  }
  function start() { if (running || reduce) return; running = true; raf = requestAnimationFrame(frame); }

  if (reduce) { // one calm, still frame - no movement
    ctx.clearRect(0, 0, W, H);
    for (const p of parts) fx.draw(p);
    if (fx.extra && data.effect !== 'colors' && data.effect !== 'fireworks') fx.extra(0);
  } else {
    start();
  }
  let resizeTimer = 0;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, 150); }, { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); else if (canvas.isConnected) start(); });
})();
