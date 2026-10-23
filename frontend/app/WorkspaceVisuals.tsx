'use client';

import {useEffect, useState} from 'react';

export function WorkspaceBackdrop() {
  return <div className="workspace-atmosphere" aria-hidden="true">
    <i className="ambient ambient-violet"/><i className="ambient ambient-mint"/>
    <div className="ambient-grid"/>
  </div>;
}

export function MotionControl() {
  const [motion, setMotion] = useState(true);
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    let saved: string | null = null;
    try { saved = localStorage.getItem('workspace-motion'); } catch {}
    const next = !preference.matches && saved !== 'off';
    setMotion(next);
    document.documentElement.dataset.motion = next ? 'on' : 'off';
    const changed = () => {
      let value: string | null = null;
      try { value = localStorage.getItem('workspace-motion'); } catch {}
      const enabled = !preference.matches && value !== 'off';
      setMotion(enabled);
      document.documentElement.dataset.motion = enabled ? 'on' : 'off';
    };
    preference.addEventListener('change', changed);
    return () => preference.removeEventListener('change', changed);
  }, []);

  function toggle() {
    const next = !motion && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setMotion(next);
    document.documentElement.dataset.motion = next ? 'on' : 'off';
    try { localStorage.setItem('workspace-motion', next ? 'on' : 'off'); } catch {}
  }

  return <button className="motion-control" type="button" onClick={toggle}
    aria-label="Анимация интерфейса" aria-pressed={motion}
    title={motion ? 'Выключить анимацию' : 'Включить анимацию (учитывает настройки системы)'}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>
      <path d="M20 3v4M18 5h4"/>
    </svg>
    <span>Анимация</span><i aria-hidden="true"/>
  </button>;
}
