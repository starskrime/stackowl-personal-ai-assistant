import { useState, useEffect } from "react";
import { useStdout } from "ink";

export function useTerminalCols(): number {
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  useEffect(() => {
    if (!stdout) return;
    const h = () => setCols(stdout.columns ?? 80);
    stdout.on("resize", h);
    return () => { stdout.off("resize", h); };
  }, [stdout]);
  return cols;
}
