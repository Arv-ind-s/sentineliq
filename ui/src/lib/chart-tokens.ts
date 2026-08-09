import { useEffect, useState } from "react";
import { useTheme } from "./theme-context";

/** Recharts takes colours as JS strings, so plot chrome has to be read back out
 *  of the cascade rather than referenced as `var(...)`. Reading it here (instead
 *  of duplicating the hex) keeps the charts on the same tokens as the rest of
 *  the UI, so a palette edit reaches them too. */
const CHART_TOKENS = {
  grid: "--chart-grid",
  axis: "--chart-axis",
  tick: "--chart-tick",
  series: "--chart-series",
  tooltipBg: "--chart-tooltip-bg",
  panel: "--bg-panel",
  border: "--border",
  text: "--fg-1",
  muted: "--fg-4",
  accent: "--accent",
  ok: "--ok",
  warn: "--warn",
  high: "--high",
  crit: "--crit",
  nodata: "--nodata",
  okInk: "--ok-ink",
  warnInk: "--warn-ink",
  highInk: "--high-ink",
  critInk: "--crit-ink",
  nodataInk: "--nodata-ink",
  cat1: "--cat-1",
  cat2: "--cat-2",
  cat3: "--cat-3",
} as const;

export type ChartTokens = Record<keyof typeof CHART_TOKENS, string>;

function readTokens(): ChartTokens {
  const styles = getComputedStyle(document.documentElement);
  const resolved = {} as ChartTokens;
  for (const [name, property] of Object.entries(CHART_TOKENS) as Array<[keyof typeof CHART_TOKENS, string]>) {
    resolved[name] = styles.getPropertyValue(property).trim();
  }
  return resolved;
}

export function useChartTokens(): ChartTokens {
  const { theme } = useTheme();
  const [tokens, setTokens] = useState<ChartTokens>(readTokens);

  useEffect(() => {
    // The attribute write and this effect land in the same commit, so re-read on
    // the next frame to pick up the values the browser has actually resolved.
    const frame = requestAnimationFrame(() => setTokens(readTokens()));
    return () => cancelAnimationFrame(frame);
  }, [theme]);

  return tokens;
}

/** Risk bands are a state, not a series: they always resolve to status hues.
 *  The mark hue is mode-invariant so a colour always means one band. */
export function bandColor(tokens: ChartTokens, band: string) {
  switch (band) {
    case "LOW": return tokens.ok;
    case "MEDIUM": return tokens.warn;
    case "HIGH": return tokens.high;
    case "CRITICAL": return tokens.crit;
    default: return tokens.nodata;
  }
}

/** Same bands rendered as *text*, where the mark hue cannot clear contrast on
 *  both surfaces (amber on white in particular). */
export function bandInk(tokens: ChartTokens, band: string) {
  switch (band) {
    case "LOW": return tokens.okInk;
    case "MEDIUM": return tokens.warnInk;
    case "HIGH": return tokens.highInk;
    case "CRITICAL": return tokens.critInk;
    default: return tokens.nodataInk;
  }
}
