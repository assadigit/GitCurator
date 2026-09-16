"use client";

/**
 * ThemeProvider — next-themes wrapper for the dashboard (v30.3).
 *
 * defaultTheme="dark" preserves the established zinc-950 look as the
 * initial experience; users can switch to light or follow the system.
 * attribute="class" drives the Tailwind `dark:` variant via the
 * `.dark` class already declared in globals.css.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

export function ThemeProvider(props: ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props} />;
}
