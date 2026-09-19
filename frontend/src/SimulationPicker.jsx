import { useEffect, useRef, useState } from 'react';

// Render inside the page instead of Qt's native select popup, which can acquire
// an oversized/clipped surface at fractional Windows display scaling.
export default function SimulationPicker({ value, options, disabled, onChange }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef(null), trigger = useRef(null), search = useRef({ text: '', time: 0 });
  const selected = Math.max(0, options.findIndex(option => option.value === value));
  useEffect(() => { setOpen(false); }, [disabled, value]);
  useEffect(() => {
    if (!open) return;
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  useEffect(() => {
    if (open) root.current?.querySelector(`#simulation-option-${active}`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);
  const choose = index => {
    setOpen(false);
    onChange(options[index].value);
    trigger.current?.focus();
  };
  function keydown(event) {
    if (event.key === 'Tab') { setOpen(false); return; }
    if (event.key === 'Escape') { if (open) event.stopPropagation(); setOpen(false); return; }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      setActive(event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
        : !open ? selected : Math.max(0, Math.min(options.length - 1, active + (event.key === 'ArrowDown' ? 1 : -1))));
      setOpen(true);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open) choose(active); else { setActive(selected); setOpen(true); }
    } else if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) {
      const now = Date.now();
      search.current = { text: (now - search.current.time < 700 ? search.current.text : '') + event.key.toLowerCase(), time: now };
      const index = options.findIndex(option => option.label.toLowerCase().startsWith(search.current.text));
      if (index >= 0) { setActive(index); setOpen(true); }
    }
  }
  return <div className="simulation-picker" ref={root} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <label id="simulation-label" htmlFor="local-region">Simulation</label>
    <button id="local-region" ref={trigger} type="button" role="combobox" value={value}
      aria-labelledby="simulation-label simulation-value" aria-haspopup="listbox"
      aria-expanded={open} aria-controls="simulation-options"
      aria-activedescendant={open ? `simulation-option-${active}` : undefined}
      disabled={disabled} onKeyDown={keydown} onClick={() => { setActive(selected); setOpen(!open); }}>
      <span id="simulation-value">{options[selected]?.label}</span><span aria-hidden="true">⌄</span>
    </button>
    {open && <ul id="simulation-options" role="listbox" aria-labelledby="simulation-label">
      {options.map((option, index) => <li key={option.value} id={`simulation-option-${index}`} role="option"
        aria-selected={value === option.value} data-value={option.value} className={index === active ? 'highlighted' : ''}
        onPointerMove={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(index)}>
        <span>{option.label}</span><small>{option.description}</small>
      </li>)}
    </ul>}
  </div>;
}
