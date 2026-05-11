import { useState, useEffect } from "react";
import { useStdout } from "ink";

export function useTerminalRows(): number {
  const { stdout } = useStdout();
  const [, bump] = useState(0);
  useEffect(() => {
    if (!stdout) return;
    const h = () => bump((n) => n + 1);
    stdout.on("resize", h);
    return () => { stdout.off("resize", h); };
  }, [stdout]);
  return stdout?.rows ?? 24;
}
