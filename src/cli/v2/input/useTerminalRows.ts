import { useState, useEffect } from "react";
import { useStdout } from "ink";

export function useTerminalRows(): number {
  const { stdout } = useStdout();
  const [rows, setRows] = useState(stdout?.rows ?? 24);
  useEffect(() => {
    if (!stdout) return;
    const h = () => setRows(stdout.rows ?? 24);
    stdout.on("resize", h);
    return () => { stdout.off("resize", h); };
  }, [stdout]);
  return rows;
}
