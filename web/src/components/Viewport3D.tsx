// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { useStore } from '../state/store';

/**
 * STUB 3D viewport: extrudes the board bounding box per stackup layer with exaggerated thickness.
 * Replaced later by the server's GLB (`/board/structure3d`), see ARCHITECTURE.md section 7.
 */
export function Viewport3D() {
  const host = useRef<HTMLDivElement>(null);
  const board = useStore((s) => s.board);
  const render = useStore((s) => s.render);

  useEffect(() => {
    const el = host.current;
    if (!el || !board || !render) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      el.textContent = '3D view unavailable: WebGL is not available';
      return;
    }
    const w = el.clientWidth || 600, h = el.clientHeight || 400;
    renderer.setSize(w, h);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(40, w / h, 0.1, 1000);
    const [x0, y0, x1, y1] = render.bbox;
    const bw = x1 - x0, bh = y1 - y0;
    const colors: Record<string, number> = { conductor: 0xc98a2b, dielectric: 0x3f7a5a, mask: 0x2d5fa8 };
    let z = 0;
    const group = new THREE.Group();
    for (const l of [...board.stackup.layers].reverse()) {
      const t = Math.max(l.thickness * 1e3 * 8, 0.15); // exaggerated mm
      const m = new THREE.Mesh(
        new THREE.BoxGeometry(bw, bh, t),
        new THREE.MeshLambertMaterial({ color: colors[l.kind], transparent: l.kind !== 'conductor', opacity: l.kind === 'conductor' ? 1 : 0.6 }),
      );
      m.position.set(0, 0, z + t / 2);
      z += t;
      group.add(m);
    }
    group.position.z = -z / 2;
    scene.add(group, new THREE.AmbientLight(0xffffff, 1.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2);
    sun.position.set(1, -1, 2);
    scene.add(sun);
    cam.up.set(0, 0, 1);
    cam.position.set(bw * 0.9, -bw * 1.1, bw * 0.9);
    cam.lookAt(0, 0, 0);
    let raf = 0;
    const loop = () => {
      group.rotation.z += 0.004;
      renderer.render(scene, cam);
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [board, render]);

  return <div ref={host} className="viewport3d" data-testid="viewport-3d" />;
}
