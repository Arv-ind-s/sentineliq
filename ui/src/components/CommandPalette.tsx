import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { CornerDownLeft, Moon, Search, Sun } from "lucide-react";
import type { MachineSummary, UserRole } from "../types/api";
import { fetchJson } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { useTheme } from "../lib/theme-context";

export type CommandNavItem = { to: string; label: string; roles: readonly UserRole[] };

type Command = {
  id: string;
  label: string;
  hint: string;
  group: "Navigate" | "Machines" | "Settings";
  keywords: string;
  run: () => void;
};

/** Subsequence match, so "pmp7" finds PUMP-07 the way a fuzzy finder would. */
function matches(query: string, haystack: string) {
  if (!query) return true;
  const target = haystack.toLowerCase();
  let cursor = 0;
  for (const character of query.toLowerCase()) {
    cursor = target.indexOf(character, cursor);
    if (cursor === -1) return false;
    cursor += 1;
  }
  return true;
}

export function CommandPalette({ navigation, open, onOpenChange }: { navigation: readonly CommandNavItem[]; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { user } = useAuth();
  const { theme, setPreference } = useTheme();
  const reducedMotion = useReducedMotion() ?? false;

  const machines = useQuery({
    queryKey: ["machines"],
    queryFn: () => fetchJson<MachineSummary[]>("/machines"),
    staleTime: 60_000,
    enabled: open,
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      }
      if (event.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onOpenChange, open]);

  useEffect(() => {
    if (!open) { setQuery(""); setActiveIndex(0); }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const close = () => onOpenChange(false);
    const items: Command[] = navigation
      .filter((item) => user && item.roles.includes(user.role))
      .map((item) => ({ id: `nav:${item.to}`, label: item.label, hint: item.to, group: "Navigate" as const, keywords: `${item.label} ${item.to}`, run: () => { navigate(item.to); close(); } }));

    for (const machine of machines.data ?? []) {
      items.push({
        id: `machine:${machine.id}`,
        label: machine.id,
        hint: `${machine.name} · ${machine.line} · risk ${Math.round(machine.risk)}`,
        group: "Machines",
        keywords: `${machine.id} ${machine.name} ${machine.line} ${machine.type}`,
        run: () => { navigate(`/machines/${machine.id}`); close(); },
      });
    }

    items.push({
      id: "theme:toggle",
      label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
      hint: "Appearance",
      group: "Settings",
      keywords: "theme light dark appearance contrast",
      run: () => { setPreference(theme === "dark" ? "light" : "dark"); close(); },
    });
    items.push({
      id: "theme:system",
      label: "Follow system theme",
      hint: "Appearance",
      group: "Settings",
      keywords: "theme system auto os appearance",
      run: () => { setPreference("system"); close(); },
    });

    return items;
  }, [machines.data, navigate, navigation, onOpenChange, setPreference, theme, user]);

  const filtered = useMemo(() => commands.filter((command) => matches(query.trim(), command.keywords)), [commands, query]);
  const clampedIndex = Math.min(activeIndex, Math.max(0, filtered.length - 1));

  useEffect(() => { setActiveIndex(0); }, [query]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [clampedIndex, filtered.length]);

  if (!open) return null;

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") { event.preventDefault(); setActiveIndex((index) => Math.min(index + 1, filtered.length - 1)); }
    if (event.key === "ArrowUp") { event.preventDefault(); setActiveIndex((index) => Math.max(index - 1, 0)); }
    if (event.key === "Enter") { event.preventDefault(); filtered[clampedIndex]?.run(); }
  };

  let lastGroup: Command["group"] | null = null;

  return (
    <AnimatePresence>
      <motion.div
        className="palette-backdrop"
        role="presentation"
        onClick={() => onOpenChange(false)}
        initial={reducedMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: reducedMotion ? 0 : 0.12 }}
      >
        <motion.div
          className="palette"
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          onClick={(event) => event.stopPropagation()}
          initial={reducedMotion ? false : { opacity: 0, y: -8, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.99 }}
          transition={{ duration: reducedMotion ? 0 : 0.16, ease: [0.2, 0, 0.13, 1] }}
        >
          <div className="palette-input-row">
            <Search size={16} aria-hidden="true" />
            {/* The dialog exists only to receive this input, so focusing it on open is the expected behaviour. */}
            <input
              autoFocus
              className="palette-input"
              type="text"
              value={query}
              placeholder="Search machines, screens and settings…"
              aria-label="Search commands"
              aria-controls="palette-results"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onInputKeyDown}
            />
            <kbd className="palette-kbd">ESC</kbd>
          </div>
          <div className="palette-results" id="palette-results" role="listbox" aria-label="Commands" ref={listRef}>
            {filtered.length === 0 && <p className="palette-empty">No match for “{query}”.</p>}
            {filtered.map((command, index) => {
              const showGroup = command.group !== lastGroup;
              lastGroup = command.group;
              const active = index === clampedIndex;
              return (
                <div key={command.id}>
                  {showGroup && <div className="palette-group">{command.group}</div>}
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    data-active={active}
                    className={`palette-item${active ? " palette-item--active" : ""}`}
                    onMouseMove={() => setActiveIndex(index)}
                    onClick={command.run}
                  >
                    {command.group === "Settings" && (theme === "dark" ? <Sun size={14} aria-hidden="true" /> : <Moon size={14} aria-hidden="true" />)}
                    <span className="palette-item-label">{command.label}</span>
                    <span className="palette-item-hint">{command.hint}</span>
                    {active && <CornerDownLeft size={13} aria-hidden="true" />}
                  </button>
                </div>
              );
            })}
          </div>
          <div className="palette-footer">
            <span><kbd className="palette-kbd">↑</kbd><kbd className="palette-kbd">↓</kbd> navigate</span>
            <span><kbd className="palette-kbd">↵</kbd> open</span>
            <span><kbd className="palette-kbd">Ctrl</kbd><kbd className="palette-kbd">K</kbd> toggle</span>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
