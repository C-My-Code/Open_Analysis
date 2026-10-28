// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../commands';
import { useStore } from '../state/store';

/**
 * RESERVED SLOT for the AI assistant. Placeholder only: it makes no LLM calls. A later WP will
 * mount a chat component here that drives the app through `dispatch` / `commandCatalog`.
 * Hidden unless enabled in settings.
 */
export function AssistantPanel() {
  const enabled = useStore((s) => s.settings.assistantEnabled);
  const open = useStore((s) => s.assistantOpen);
  if (!enabled) return null;
  return (
    <aside className={`assistant ${open ? 'open' : 'collapsed'}`} aria-label="Assistant" data-testid="assistant-panel">
      <button
        className="assistant-toggle"
        aria-expanded={open}
        onClick={() => void dispatch({ type: 'assistant.setOpen', open: !open })}
      >
        {open ? 'Assistant ▸' : '◂'}
      </button>
      {open && (
        <div className="pad">
          <h2>Assistant</h2>
          <p className="muted">Not connected. This slot is reserved for an AI assistant that will use the same commands as the UI.</p>
        </div>
      )}
    </aside>
  );
}
