import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme, type ThemePreference } from "../lib/theme-context";

const OPTIONS: Array<{ value: ThemePreference; label: string; icon: typeof Sun }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "system", label: "System", icon: Monitor },
  { value: "dark", label: "Dark", icon: Moon },
];

export function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={`${label} theme`}
          title={`${label} theme`}
          className={`theme-toggle-option${preference === value ? " theme-toggle-option--active" : ""}`}
          onClick={() => setPreference(value)}
        >
          <Icon size={14} strokeWidth={1.9} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
